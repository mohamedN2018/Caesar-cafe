"""
Applying order events and folding them into the aggregate.

`apply_events` is the only mutation path for an order. Every client — the SPA,
the Desktop, the sync engine — goes through it, so ordering, idempotency,
authorization and total recalculation exist in exactly one place.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Modifier, ProductVariant
from apps.configuration import resolver
from apps.configuration.resolver import ScopeContext
from apps.core.exceptions import AppError, NotFoundError
from apps.core.money import OrderLine, TaxRules, compute_order

from . import state
from .models import (
    EventType,
    ItemStatus,
    Order,
    OrderEvent,
    OrderItem,
    OrderItemModifier,
    OrderStatus,
    OrderType,
)

logger = logging.getLogger(__name__)


class EventRejected(AppError):
    status_code = 422
    code = "EVENT_REJECTED"


@dataclass(frozen=True)
class ApplyResult:
    order: Order
    applied: list[str]
    skipped: list[str]
    """Event ids already recorded — a replay, not an error."""


# ── tax rules ────────────────────────────────────────────────────────────────


def tax_rules_for(branch) -> dict:
    """
    Resolve the branch's money rules, snapshotted onto the order at open time.

    Snapshotting is what stops a mid-service VAT change from silently rewriting
    a bill the customer is already looking at.
    """
    context = ScopeContext(organization_id=branch.organization_id, branch_id=branch.id)
    return {
        "vat_percent": resolver.get("finance.vat_percent", context),
        "vat_enabled": resolver.get("finance.vat_enabled", context),
        "vat_inclusive": resolver.get("finance.vat_inclusive", context),
        "service_percent": resolver.get("finance.service_percent", context),
        "service_enabled": resolver.get("finance.service_enabled", context),
        "service_applies_to": resolver.get("finance.service_applies_to", context),
        "rounding_step": resolver.get("finance.rounding_step", context),
    }


def _rules_from_order(order: Order) -> TaxRules:
    return TaxRules(
        vat_percent=order.vat_percent,
        vat_enabled=order.vat_percent > 0,
        vat_inclusive=order.vat_inclusive,
        service_percent=order.service_percent,
        service_enabled=order.service_percent > 0,
        rounding_step=order.rounding_step,
    )


# ── opening ──────────────────────────────────────────────────────────────────


@transaction.atomic
def open_order(
    *,
    branch,
    order_type: str = OrderType.DINE_IN,
    order_id=None,
    local_number: str | None = None,
    table_session=None,
    shift=None,
    device_id=None,
    user=None,
) -> Order:
    rules = tax_rules_for(branch)
    service_applies = order_type in (rules["service_applies_to"] or [])

    order = Order.objects.create(
        id=order_id or uuid.uuid4(),
        organization=branch.organization,
        branch=branch,
        order_type=order_type,
        status=OrderStatus.DRAFT,
        local_number=local_number or _next_local_number(branch, device_id),
        table_session=table_session,
        shift=shift,
        device_id=device_id,
        opened_by=user,
        vat_percent=rules["vat_percent"] if rules["vat_enabled"] else Decimal("0"),
        vat_inclusive=rules["vat_inclusive"],
        service_percent=(
            rules["service_percent"]
            if (rules["service_enabled"] and service_applies)
            else Decimal("0")
        ),
        rounding_step=rules["rounding_step"],
        created_by=user,
    )

    _record(
        order,
        EventType.ORDER_OPENED,
        payload={"order_type": order_type},
        actor=user,
        device_id=device_id,
    )
    order.status = OrderStatus.OPEN
    order.save(update_fields=["status", "updated_at"])
    return order


def _next_local_number(branch, device_id) -> str:
    """
    `MB-01-0042`. Readable, and unique per branch without a coordination
    round-trip — which an offline device cannot make.
    """
    device_code = str(device_id)[-2:].upper() if device_id else "00"
    today = timezone.now().date()
    count = Order.objects.filter(branch=branch, opened_at__date=today).count() + 1
    return f"{branch.code}-{device_code}-{count:04d}"


# ── events ───────────────────────────────────────────────────────────────────

_HANDLERS = {}


def handles(event_type):
    def register(func):
        _HANDLERS[event_type] = func
        return func

    return register


@transaction.atomic
def apply_events(
    order: Order,
    events: list[dict],
    *,
    actor=None,
    device_id=None,
    approval=None,
) -> ApplyResult:
    """
    Append events to an order and refold it.

    Events already recorded are SKIPPED, not rejected: a Desktop whose push
    timed out must be able to retry the whole batch without duplicating a line.
    """
    locked = Order.objects.select_for_update().get(pk=order.pk)
    existing_ids = set(map(str, locked.events.values_list("id", flat=True)))
    next_sequence = (
        locked.events.order_by("-sequence").values_list("sequence", flat=True).first() or 0
    )

    applied: list[str] = []
    skipped: list[str] = []

    for raw in events:
        event_id = str(raw.get("id") or uuid.uuid4())
        if event_id in existing_ids:
            skipped.append(event_id)
            continue

        event_type = raw.get("type") or raw.get("event_type")
        handler = _HANDLERS.get(event_type)
        if handler is None:
            raise EventRejected(f"نوع حدث غير معروف: {event_type}", code="UNKNOWN_EVENT_TYPE")

        state.assert_mutable(locked)
        handler(locked, raw.get("payload") or {}, actor=actor)

        next_sequence += 1
        OrderEvent.objects.create(
            id=uuid.UUID(event_id),
            order=locked,
            sequence=next_sequence,
            event_type=event_type,
            payload=raw.get("payload") or {},
            device_id=device_id,
            actor=actor,
            approved_by=approval,
            occurred_at=raw.get("occurred_at") or timezone.now(),
        )
        existing_ids.add(event_id)
        applied.append(event_id)

    recalculate(locked)
    return ApplyResult(order=locked, applied=applied, skipped=skipped)


@handles(EventType.ITEM_ADDED)
def _item_added(order: Order, payload: dict, *, actor=None) -> None:
    variant = (
        ProductVariant.objects.select_related("product", "product__station")
        .prefetch_related("channel_prices")
        .filter(id=payload["variant_id"], product__branch_id=order.branch_id)
        .first()
    )
    if variant is None:
        raise NotFoundError("الصنف غير موجود", code="VARIANT_NOT_FOUND")

    quantity = Decimal(str(payload.get("quantity", 1)))
    if quantity <= 0:
        raise EventRejected("الكمية يجب أن تكون أكبر من صفر", code="INVALID_QUANTITY")

    item = OrderItem.objects.create(
        order=order,
        line_id=payload.get("line_id") or uuid.uuid4(),
        variant=variant,
        station=variant.product.station,
        # Snapshots: the receipt must survive a later price change.
        name_snapshot=str(variant),
        # The CHANNEL price, resolved once and frozen onto the line. The order's
        # type is fixed at open time, so every line on one bill is rung on the
        # same channel — a bill that changed channel halfway would have two
        # prices for the same bottle of water.
        unit_price_snapshot=variant.price_for(order.order_type),
        cost_snapshot=variant.cost,
        tax_exempt_snapshot=variant.product.is_tax_exempt,
        quantity=quantity,
        note=payload.get("note", "")[:200],
        created_by=actor,
    )

    for modifier_id in payload.get("modifiers", []):
        modifier = Modifier.objects.filter(id=modifier_id).first()
        if modifier is None:
            continue
        OrderItemModifier.objects.create(
            order_item=item,
            modifier=modifier,
            name_snapshot=modifier.name_ar,
            price_delta_snapshot=modifier.price_delta,
        )


@handles(EventType.ITEM_QUANTITY_CHANGED)
def _quantity_changed(order: Order, payload: dict, *, actor=None) -> None:
    item = _get_line(order, payload["line_id"])
    quantity = Decimal(str(payload["quantity"]))
    if quantity <= 0:
        raise EventRejected("الكمية يجب أن تكون أكبر من صفر", code="INVALID_QUANTITY")
    item.quantity = quantity
    item.save(update_fields=["quantity", "updated_at"])


@handles(EventType.ITEM_VOIDED)
def _item_voided(order: Order, payload: dict, *, actor=None) -> None:
    """
    Voiding marks, never deletes.

    A deleted line is an unexplained gap in a financial record; a voided one is
    an auditable decision, and the void rate per user is a loss-prevention
    signal (docs/03).
    """
    from apps.audit import services as audit

    item = _get_line(order, payload["line_id"])
    item.status = ItemStatus.VOIDED
    item.voided_at = timezone.now()
    item.void_reason = payload.get("reason", "")[:200]
    item.save(update_fields=["status", "voided_at", "void_reason", "updated_at"])

    audit.record(
        "order.item_voided",
        branch=order.branch,
        actor=actor,
        obj=order,
        object_label=order.local_number,
        detail={
            "item": item.name_snapshot,
            "quantity": item.quantity,
            "value": item.line_total,
            "reason": item.void_reason,
            "after_fire": item.fired_at is not None,
        },
    )


@handles(EventType.ITEM_NOTE_SET)
def _note_set(order: Order, payload: dict, *, actor=None) -> None:
    item = _get_line(order, payload["line_id"])
    item.note = payload.get("note", "")[:200]
    item.save(update_fields=["note", "updated_at"])


@handles(EventType.ITEM_PRICE_OVERRIDDEN)
def _price_overridden(order: Order, payload: dict, *, actor=None) -> None:
    """
    A manual unit price on one line.

    Distinct from a discount, and the distinction is the reason this exists.
    A discount is a percentage off a known price and reports as a discount; an
    override is a different price entirely — the damaged cake sold at half, the
    staff meal, the drink remade after a complaint. Forcing those through the
    discount field made the discount rate meaningless as a loss signal, which is
    the one thing it is watched for.

    The catalogue price is kept beside the override rather than replaced, so the
    audit row and the reports can both say what it was supposed to be.

    Sending `price` as null CLEARS the override and the line returns to the
    catalogue price — a mistyped override must be undoable without voiding the
    line and re-ringing it, which on a fired order means the kitchen makes it
    twice.
    """
    from apps.audit import services as audit

    item = _get_line(order, payload["line_id"])
    raw = payload.get("price")

    if raw is None:
        was, item.price_override, item.price_override_reason = item.price_override, None, ""
    else:
        price = Decimal(str(raw))
        if price < 0:
            # A negative price is not a giveaway, it is money out of the drawer
            # with no refund record. Zero is allowed; below zero is not.
            raise EventRejected("السعر لا يمكن أن يكون سالباً", code="INVALID_PRICE")
        was = item.price_override
        item.price_override = price
        item.price_override_reason = payload.get("reason", "")[:200]

    item.save(update_fields=["price_override", "price_override_reason", "updated_at"])

    audit.record(
        "order.price_overridden",
        branch=order.branch,
        actor=actor,
        obj=order,
        object_label=order.local_number,
        detail={
            "item": item.name_snapshot,
            "catalog_price": item.unit_price_snapshot,
            "previous_override": was,
            "new_price": item.price_override,
            "quantity": item.quantity,
            "reason": item.price_override_reason,
            "after_fire": item.fired_at is not None,
        },
    )


@handles(EventType.DISCOUNT_APPLIED)
def _discount_applied(order: Order, payload: dict, *, actor=None) -> None:
    percent = Decimal(str(payload.get("percent", 0)))
    if not (Decimal("0") <= percent <= Decimal("100")):
        raise EventRejected("نسبة خصم غير صالحة", code="INVALID_DISCOUNT")

    from apps.audit import services as audit

    if line_id := payload.get("line_id"):
        item = _get_line(order, line_id)
        item.discount_percent = percent
        item.save(update_fields=["discount_percent", "updated_at"])
        scope = item.name_snapshot
    else:
        order.discount_percent = percent
        order.discount_reason = payload.get("reason", "")[:200]
        order.save(update_fields=["discount_percent", "discount_reason", "updated_at"])
        scope = "الطلب"

    audit.record(
        "order.discount_applied",
        branch=order.branch,
        actor=actor,
        obj=order,
        object_label=order.local_number,
        detail={"percent": percent, "scope": scope, "reason": payload.get("reason", "")},
    )


@handles(EventType.ORDER_FIRED)
def _order_fired(order: Order, payload: dict, *, actor=None) -> None:
    """
    Send the unfired items to the kitchen.

    `route_order` stamps `fired_at`, which is what makes a repeated fire
    idempotent: a second press sends only what has been added since.
    """
    from apps.kitchen.services import route_order

    if not order.items.filter(status=ItemStatus.ACTIVE, fired_at__isnull=True).exists():
        raise EventRejected("لا توجد أصناف جديدة لإرسالها", code="NOTHING_TO_FIRE")

    result = route_order(order, user=actor)
    if result.unrouted:
        # Surfaced on the response, not swallowed: an item with no station is an
        # item nobody makes, and the cashier needs to know now.
        logger.warning(
            "Fired items had no kitchen station",
            extra={"order": order.local_number, "items": result.unrouted},
        )

    # Firing a second round from IN_KITCHEN is normal — a table orders more
    # after the first round arrives. Only assert when the status actually moves.
    if order.status != OrderStatus.IN_KITCHEN:
        state.assert_transition(order.status, OrderStatus.IN_KITCHEN)
        order.status = OrderStatus.IN_KITCHEN
        order.save(update_fields=["status", "updated_at"])


@handles(EventType.PLAY_SESSION_CHARGED)
def _play_session_charged(order: Order, payload: dict, *, actor=None) -> None:
    """
    A closed play session becomes one ordinary order line.

    The price is the session's, not the variant's — a play visit is priced by
    duration, and the variant exists only to give the sale a place in the
    catalog so play revenue reports beside the coffee instead of in a parallel
    universe of its own.

    Deliberately an event like any other: VAT, service charge, discounts, split
    payment, refunds and shift reconciliation then work unmodified, which is the
    entire reason a session converts at checkout rather than metering into the
    financial core (docs/12, C12).
    """
    from apps.kids import services as kids_services
    from apps.kids.models import PlaySession, SessionStatus

    session = (
        PlaySession.objects.select_related("area", "child", "guardian")
        .filter(id=payload["session_id"], branch_id=order.branch_id)
        .first()
    )
    if session is None:
        raise NotFoundError("جلسة اللعب غير موجودة", code="PLAY_SESSION_NOT_FOUND")
    if session.status != SessionStatus.CHECKED_OUT:
        raise EventRejected("الجلسة لم تُغلق بعد", code="SESSION_NOT_CLOSED")

    variant = kids_services.billing_variant_for(session.area)

    OrderItem.objects.create(
        order=order,
        line_id=payload.get("line_id") or uuid.uuid4(),
        variant=variant,
        station=None,  # Nothing to make. The kitchen must never see this line.
        name_snapshot=kids_services.line_description(session),
        unit_price_snapshot=session.payable,
        cost_snapshot=Decimal("0"),
        tax_exempt_snapshot=variant.product.is_tax_exempt,
        quantity=Decimal("1"),
        created_by=actor,
    )


@handles(EventType.TABLE_ASSIGNED)
def _table_assigned(order: Order, payload: dict, *, actor=None) -> None:
    from apps.floor.models import TableSession

    session = TableSession.objects.filter(id=payload["table_session_id"]).first()
    if session is None:
        raise NotFoundError("الجلسة غير موجودة", code="SESSION_NOT_FOUND")
    order.table_session = session
    order.save(update_fields=["table_session", "updated_at"])


@handles(EventType.CUSTOMER_ASSIGNED)
def _customer_assigned(order: Order, payload: dict, *, actor=None) -> None:
    order.customer_name = payload.get("name", "")[:200]
    order.customer_phone = payload.get("phone", "")[:32]
    order.save(update_fields=["customer_name", "customer_phone", "updated_at"])


def _get_line(order: Order, line_id) -> OrderItem:
    item = order.items.filter(line_id=line_id).first()
    if item is None:
        raise NotFoundError("السطر غير موجود", code="LINE_NOT_FOUND")
    return item


def _record(order, event_type, *, payload=None, actor=None, device_id=None) -> OrderEvent:
    next_sequence = (
        order.events.order_by("-sequence").values_list("sequence", flat=True).first() or 0
    ) + 1
    return OrderEvent.objects.create(
        id=uuid.uuid4(),
        order=order,
        sequence=next_sequence,
        event_type=event_type,
        payload=payload or {},
        actor=actor,
        device_id=device_id,
        occurred_at=timezone.now(),
    )


# ── folding ──────────────────────────────────────────────────────────────────


def recalculate(order: Order) -> Order:
    """
    Recompute totals from the item projection.

    Uses `apps.core.money` — the same module the Desktop vendors — so the two
    sides cannot disagree by a piaster.
    """
    items = list(order.items.filter(status=ItemStatus.ACTIVE).prefetch_related("modifiers"))

    lines = [
        OrderLine(
            # The override when there is one — see `OrderItem.effective_unit_price`.
            unit_price=item.effective_unit_price,
            quantity=item.quantity,
            discount_percent=item.discount_percent,
            modifier_deltas=tuple(m.price_delta_snapshot for m in item.modifiers.all()),
            tax_exempt=item.tax_exempt_snapshot,
        )
        for item in items
    ]

    totals = compute_order(lines, _rules_from_order(order), order.discount_percent)

    for item, line_totals in zip(items, totals.lines, strict=True):
        item.line_gross = line_totals.gross
        item.line_discount = line_totals.discount
        item.line_total = line_totals.net
        item.save(update_fields=["line_gross", "line_discount", "line_total", "updated_at"])

    order.subtotal = totals.subtotal
    order.discount_total = totals.discount_total
    order.service_total = totals.service_total
    order.tax_total = totals.tax_total
    order.rounding_adjustment = totals.rounding_adjustment
    order.grand_total = totals.grand_total
    order.save(
        update_fields=[
            "subtotal",
            "discount_total",
            "service_total",
            "tax_total",
            "rounding_adjustment",
            "grand_total",
            "updated_at",
        ]
    )
    return order


# ── lifecycle ────────────────────────────────────────────────────────────────


def void_order(order: Order, *, reason: str, actor=None, approval=None) -> Order:
    """
    Deliberately NOT decorated with `@transaction.atomic`.

    The refusal path records an audit row and then raises. Inside one atomic
    block that row would be rolled back by the very exception it exists to
    explain, so the guard runs outside a transaction and only the mutation is
    wrapped. (A caller that has already opened a transaction still loses the row
    on rollback — unavoidable, and the reason `apply_push` gives each operation
    its own savepoint rather than sharing one.)
    """
    from apps.audit import services as audit

    if order.status in state.TERMINAL:
        # Recorded, not merely refused: an attempt to reopen a paid order is a
        # loss-prevention signal even when the server says no (docs/09, T4).
        audit.record(
            "order.reopen_attempt",
            branch=order.branch,
            actor=actor,
            obj=order,
            object_label=order.local_number,
            detail={"status": order.status, "reason": reason, "total": order.grand_total},
        )
        raise state.InvalidStateTransition(
            f"لا يمكن إلغاء طلب في حالة {order.status}", extra={"current": order.status}
        )
    state.assert_transition(order.status, OrderStatus.CANCELLED)

    before = audit.snapshot(order, ["status", "grand_total", "void_reason"])

    with transaction.atomic():
        order.status = OrderStatus.CANCELLED
        order.void_reason = reason[:200]
        order.closed_at = timezone.now()
        order.save(update_fields=["status", "void_reason", "closed_at", "updated_at"])
        _record(order, EventType.ORDER_VOIDED, payload={"reason": reason}, actor=actor)

    audit.record(
        "order.voided",
        branch=order.branch,
        actor=actor,
        approved_by=approval,
        obj=order,
        object_label=order.local_number,
        before=before,
        after=audit.snapshot(order, ["status", "grand_total", "void_reason"]),
        detail={"reason": reason, "total": order.grand_total},
    )
    logger.warning(
        "Order voided",
        extra={"order": order.local_number, "reason": reason, "total": str(order.grand_total)},
    )
    return order


def requires_void_approval(order: Order) -> bool:
    """
    Voiding is routine before the kitchen sees an order, and a loss-prevention
    event afterwards. The grace window is a setting, not a constant.
    """
    if order.status == OrderStatus.DRAFT or order.status == OrderStatus.OPEN:
        return False

    context = ScopeContext(organization_id=order.organization_id, branch_id=order.branch_id)
    grace = resolver.get("orders.void_grace_seconds", context)
    fired = order.items.filter(fired_at__isnull=False).order_by("fired_at").first()
    if fired is None:
        return False
    return (timezone.now() - fired.fired_at).total_seconds() > grace
