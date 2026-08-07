"""
The only supported way to change stock.

`apply_movement` is the single write path. Everything else — sales, purchases,
waste, counts — calls it. That is what makes the ledger trustworthy: there is
exactly one place where a quantity can move, and it always appends a movement.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.exceptions import AppError

from .models import (
    CostingMethod,
    InventoryItem,
    MovementType,
    StockLevel,
    StockMovement,
    Unit,
    UnitConversion,
)

logger = logging.getLogger(__name__)

QUANTITY_PLACES = Decimal("0.001")
COST_PLACES = Decimal("0.0001")


class InsufficientStock(AppError):
    status_code = 409
    code = "INSUFFICIENT_STOCK"
    default_detail = "الكمية المتاحة غير كافية"


class UnitMismatch(AppError):
    code = "UNIT_CONVERSION_MISSING"
    default_detail = "لا يوجد تحويل بين وحدتي القياس"


def q(value: Decimal) -> Decimal:
    return Decimal(value).quantize(QUANTITY_PLACES, rounding=ROUND_HALF_UP)


def c(value: Decimal) -> Decimal:
    return Decimal(value).quantize(COST_PLACES, rounding=ROUND_HALF_UP)


# ── unit conversion ──────────────────────────────────────────────────────────


def convert(quantity: Decimal, from_unit: Unit, to_unit: Unit) -> Decimal:
    """
    Convert between units, following a direct or inverse conversion.

    Only one direction needs to be configured: storing both `1 KG = 1000 G` and
    `1 G = 0.001 KG` invites them to disagree.
    """
    if from_unit_id_equals(from_unit, to_unit):
        return q(quantity)

    direct = UnitConversion.objects.filter(from_unit=from_unit, to_unit=to_unit).first()
    if direct:
        return q(quantity * direct.factor)

    inverse = UnitConversion.objects.filter(from_unit=to_unit, to_unit=from_unit).first()
    if inverse:
        if inverse.factor == 0:
            raise UnitMismatch("معامل التحويل يساوي صفر")
        return q(quantity / inverse.factor)

    raise UnitMismatch(
        f"لا يوجد تحويل من {from_unit.code} إلى {to_unit.code}",
        extra={"from": from_unit.code, "to": to_unit.code},
    )


def from_unit_id_equals(a: Unit, b: Unit) -> bool:
    return getattr(a, "pk", a) == getattr(b, "pk", b)


# ── the ledger ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MovementResult:
    movement: StockMovement
    level: StockLevel


@transaction.atomic
def apply_movement(
    *,
    item: InventoryItem,
    quantity_delta: Decimal,
    movement_type: str,
    unit_cost: Decimal | None = None,
    ref=None,
    user=None,
    device_id=None,
    reason: str = "",
    occurred_at=None,
    allow_negative: bool = True,
) -> MovementResult:
    """
    Move stock and record why, atomically.

    `select_for_update()` on the level row is what makes concurrent sales safe.
    Without it, two simultaneous cappuccino sales both read 500g of beans and
    both write 482g — losing 18g silently, every time it races.

    Weighted-average cost is recomputed only on inbound movements: consuming
    stock does not change what the remaining stock cost.
    """
    quantity_delta = q(quantity_delta)
    if quantity_delta == 0:
        raise AppError("لا يمكن تسجيل حركة بكمية صفر", code="ZERO_MOVEMENT")

    level, _ = StockLevel.objects.select_for_update().get_or_create(item=item)

    if quantity_delta < 0 and not allow_negative:
        if level.quantity_on_hand + quantity_delta < 0:
            raise InsufficientStock(
                f"الكمية المتاحة من {item.name_ar} غير كافية "
                f"(المتاح {level.quantity_on_hand}، المطلوب {abs(quantity_delta)})",
                extra={
                    "item": item.code,
                    "available": str(level.quantity_on_hand),
                    "requested": str(abs(quantity_delta)),
                },
            )

    if quantity_delta > 0 and unit_cost is not None:
        level.weighted_avg_cost = _weighted_average(
            current_quantity=level.quantity_on_hand,
            current_cost=level.weighted_avg_cost,
            incoming_quantity=quantity_delta,
            incoming_cost=c(unit_cost),
            method=item.costing_method,
        )

    level.quantity_on_hand = q(level.quantity_on_hand + quantity_delta)
    level.last_movement_at = occurred_at or timezone.now()
    level.save()

    movement = StockMovement.objects.create(
        branch=item.branch,
        item=item,
        movement_type=movement_type,
        quantity_delta=quantity_delta,
        # Outbound movements are valued at the current average, which is what
        # makes COGS meaningful on a sale.
        unit_cost=c(unit_cost) if unit_cost is not None else level.weighted_avg_cost,
        balance_after=level.quantity_on_hand,
        ref_type=ref.__class__.__name__ if ref is not None else "",
        ref_id=getattr(ref, "pk", None),
        user=user,
        device_id=device_id,
        reason=reason,
        occurred_at=occurred_at or timezone.now(),
    )

    return MovementResult(movement=movement, level=level)


def _weighted_average(
    *,
    current_quantity: Decimal,
    current_cost: Decimal,
    incoming_quantity: Decimal,
    incoming_cost: Decimal,
    method: str,
) -> Decimal:
    """
    New average cost after a receipt.

    A negative on-hand balance (permitted when `allow_negative`) would produce a
    nonsensical average, so the incoming cost simply takes over.
    """
    if method != CostingMethod.WEIGHTED_AVG:
        return incoming_cost
    if current_quantity <= 0:
        return incoming_cost

    total_value = (current_quantity * current_cost) + (incoming_quantity * incoming_cost)
    total_quantity = current_quantity + incoming_quantity
    return c(total_value / total_quantity)


# ── operations built on the ledger ───────────────────────────────────────────


def record_waste(*, item, quantity: Decimal, reason: str, user=None, device_id=None):
    if quantity <= 0:
        raise AppError("كمية الهالك يجب أن تكون أكبر من صفر", code="INVALID_QUANTITY")
    return apply_movement(
        item=item,
        quantity_delta=-abs(quantity),
        movement_type=MovementType.WASTE,
        user=user,
        device_id=device_id,
        reason=reason,
    )


def adjust(*, item, new_quantity: Decimal, reason: str, user=None):
    """
    Set stock to an absolute figure by recording the DIFFERENCE.

    Never writes the level directly — an adjustment that left no movement would
    be an unexplained change to a financial record.
    """
    level, _ = StockLevel.objects.get_or_create(item=item)
    delta = q(new_quantity) - level.quantity_on_hand
    if delta == 0:
        return None
    return apply_movement(
        item=item,
        quantity_delta=delta,
        movement_type=MovementType.ADJUSTMENT,
        user=user,
        reason=reason,
    )


def set_opening_balance(*, item, quantity: Decimal, unit_cost: Decimal, user=None):
    return apply_movement(
        item=item,
        quantity_delta=q(quantity),
        movement_type=MovementType.OPENING,
        unit_cost=unit_cost,
        user=user,
        reason="رصيد افتتاحي",
    )


@transaction.atomic
def post_count(count, *, user=None):
    """
    Turn a completed count into adjustments.

    Each variance becomes its own COUNT movement, so the ledger explains what
    was found rather than just showing a number changing.
    """
    from .models import CountStatus

    if count.status == CountStatus.POSTED:
        raise AppError("تم ترحيل هذا الجرد بالفعل", code="COUNT_ALREADY_POSTED")

    movements = []
    for line in count.lines.select_related("item").all():
        if line.counted_quantity is None:
            continue
        variance = line.variance
        if variance == 0:
            continue
        movements.append(
            apply_movement(
                item=line.item,
                quantity_delta=variance,
                movement_type=MovementType.COUNT,
                ref=count,
                user=user,
                reason=line.reason or f"جرد {count.reference}",
            )
        )

    count.status = CountStatus.POSTED
    count.posted_at = timezone.now()
    count.posted_by = user
    count.save(update_fields=["status", "posted_at", "posted_by", "updated_at"])
    return movements


# ── reconciliation ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Drift:
    item_code: str
    item_name: str
    level_quantity: Decimal
    ledger_quantity: Decimal

    @property
    def difference(self) -> Decimal:
        return self.level_quantity - self.ledger_quantity


def reconcile(branch=None) -> list[Drift]:
    """
    Replay every item's movements and compare against its level.

    This is how we find out whether a code path ever bypassed
    `apply_movement`. A clean run proves the projection still matches the
    ledger; a drift is a bug, not a stock problem.
    """
    items = InventoryItem.all_objects.select_related("level")
    if branch is not None:
        items = items.filter(branch=branch)

    ledger = {
        row["item_id"]: row["total"]
        for row in StockMovement.objects.filter(item__in=items)
        .values("item_id")
        .annotate(total=Sum("quantity_delta"))
    }

    drifts: list[Drift] = []
    for item in items:
        level = getattr(item, "level", None)
        recorded = q(level.quantity_on_hand) if level else Decimal("0")
        replayed = q(ledger.get(item.id, Decimal("0")))

        if recorded != replayed:
            drift = Drift(
                item_code=item.code,
                item_name=item.name_ar,
                level_quantity=recorded,
                ledger_quantity=replayed,
            )
            drifts.append(drift)
            logger.error(
                "Stock level does not match its ledger",
                extra={
                    "item": item.code,
                    "level": str(recorded),
                    "ledger": str(replayed),
                    "difference": str(drift.difference),
                },
            )

    return drifts


def low_stock(branch) -> list[StockLevel]:
    """Items at or below their minimum, counting reserved quantity as gone."""
    return [
        level
        for level in StockLevel.objects.select_related("item").filter(
            item__branch=branch, item__is_active=True
        )
        if level.is_low
    ]
