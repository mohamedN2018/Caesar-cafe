"""
Opening and closing a shift.

The cash variance this produces is one of the two loss-prevention signals in
the product (the other is void rate per user). Both fall out of data the system
records anyway.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.configuration import resolver
from apps.configuration.resolver import ScopeContext
from apps.core.exceptions import AppError

from .models import CashMovement, CashMovementType, Shift, ShiftStatus

logger = logging.getLogger(__name__)


class ShiftAlreadyOpen(AppError):
    status_code = 409
    code = "SHIFT_ALREADY_OPEN"
    default_detail = "توجد وردية مفتوحة بالفعل على هذا الجهاز"


@dataclass(frozen=True)
class ShiftTotals:
    gross_sales: Decimal
    discounts: Decimal
    refunds: Decimal
    net_sales: Decimal
    tax: Decimal
    service: Decimal
    cash_sales: Decimal
    non_cash_sales: Decimal
    cash_in: Decimal
    cash_out: Decimal
    expected_cash: Decimal
    order_count: int
    void_count: int


@transaction.atomic
def open_shift(
    *, branch, user=None, device_id=None, opening_cash=Decimal("0"), shift_id=None
) -> Shift:
    """
    `shift_id` lets an offline terminal name its own shift, the same way it
    names its own orders — so the cash movements it queued against that id
    resolve when they arrive, instead of referring to a shift that only got its
    identity once the server saw it.
    """
    if device_id is not None:
        existing = Shift.objects.filter(device_id=device_id, status=ShiftStatus.OPEN).first()
        if existing is not None:
            raise ShiftAlreadyOpen(extra={"shift_id": str(existing.id)})

    shift = Shift.objects.create(
        **({"id": shift_id} if shift_id else {}),
        organization=branch.organization,
        branch=branch,
        user=user,
        device_id=device_id,
        opening_cash=Decimal(opening_cash).quantize(Decimal("0.01")),
        created_by=user,
    )

    from apps.audit import services as audit

    audit.record(
        "shift.opened",
        branch=branch,
        actor=user,
        obj=shift,
        detail={
            "opening_cash": shift.opening_cash,
            "device": str(device_id) if device_id else None,
        },
    )
    return shift


def record_cash_movement(
    *, shift: Shift, movement_type: str, amount: Decimal, reason: str, user=None
) -> CashMovement:
    if not shift.is_open:
        raise AppError("الوردية مغلقة", code="SHIFT_CLOSED")
    if amount <= 0:
        raise AppError("المبلغ يجب أن يكون أكبر من صفر", code="INVALID_AMOUNT")

    movement = CashMovement.objects.create(
        shift=shift,
        movement_type=movement_type,
        amount=Decimal(amount).quantize(Decimal("0.01")),
        reason=reason[:200],
        user=user,
        created_by=user,
    )

    from apps.audit import services as audit

    audit.record(
        "shift.cash_movement",
        branch=shift.branch,
        actor=user,
        obj=shift,
        detail={"type": movement_type, "amount": movement.amount, "reason": movement.reason},
    )
    return movement


def compute_totals(shift: Shift) -> ShiftTotals:
    """
    Everything the X and Z reports need, from one pass over the shift.

    `expected_cash` counts only methods flagged `counts_as_cash`, so a card
    payment never inflates what the drawer should hold.
    """
    from apps.orders.models import Order, OrderStatus
    from apps.payments.models import Payment, Refund

    orders = Order.objects.filter(shift=shift)
    settled = orders.filter(status__in=[OrderStatus.PAID, OrderStatus.REFUNDED])

    aggregates = settled.aggregate(
        gross=Sum("subtotal"),
        discounts=Sum("discount_total"),
        tax=Sum("tax_total"),
        service=Sum("service_total"),
        total=Sum("grand_total"),
    )

    payments = Payment.objects.filter(shift=shift).select_related("method")
    cash_sales = sum((p.amount for p in payments if p.method.counts_as_cash), Decimal("0.00"))
    non_cash_sales = sum(
        (p.amount for p in payments if not p.method.counts_as_cash), Decimal("0.00")
    )

    refunds = Refund.objects.filter(shift=shift).aggregate(total=Sum("amount"))["total"] or Decimal(
        "0.00"
    )

    movements = shift.cash_movements.all()
    cash_in = sum(
        (m.amount for m in movements if m.movement_type == CashMovementType.IN), Decimal("0.00")
    )
    cash_out = sum(
        (m.amount for m in movements if m.movement_type != CashMovementType.IN), Decimal("0.00")
    )

    expected = shift.opening_cash + cash_sales + cash_in - cash_out - refunds

    return ShiftTotals(
        gross_sales=aggregates["gross"] or Decimal("0.00"),
        discounts=aggregates["discounts"] or Decimal("0.00"),
        refunds=refunds,
        net_sales=(aggregates["total"] or Decimal("0.00")) - refunds,
        tax=aggregates["tax"] or Decimal("0.00"),
        service=aggregates["service"] or Decimal("0.00"),
        cash_sales=cash_sales,
        non_cash_sales=non_cash_sales,
        cash_in=cash_in,
        cash_out=cash_out,
        expected_cash=expected.quantize(Decimal("0.01")),
        order_count=settled.count(),
        void_count=orders.filter(status=OrderStatus.CANCELLED).count(),
    )


def _outstanding_play(shift: Shift) -> dict:
    """
    Children still in the play area, with their running charge.

    Deliberately reported rather than blocking: a handover with children present
    is completely ordinary — the day shift ends, the kids stay. What must not
    happen is the shift closing *silently* over an unbilled meter, so the
    liability is on the report and the count is in the log.
    """
    from apps.kids import services as kids_services

    sessions = kids_services.outstanding_sessions(shift.branch)
    liability = sum((Decimal(s["running_charge"]) for s in sessions), Decimal("0.00"))
    return {
        "open_play_sessions": len(sessions),
        "open_play_liability": str(liability),
        "play_sessions": sessions,
    }


def x_report(shift: Shift) -> dict:
    """A mid-shift read. Does not close anything."""
    totals = compute_totals(shift)
    return {
        "shift_id": str(shift.id),
        "opened_at": shift.opened_at.isoformat(),
        "user": shift.user.full_name_ar if shift.user else None,
        "is_final": False,
        **{k: str(v) if isinstance(v, Decimal) else v for k, v in totals.__dict__.items()},
        **_outstanding_play(shift),
    }


@transaction.atomic
def close_shift(
    *, shift: Shift, counted_cash: Decimal, reason: str = "", user=None, approved_by=None
) -> Shift:
    """
    Close and freeze the Z-report.

    Blind close (`shifts.blind_close`) is the anti-fudge setting: when on, the
    cashier counts the drawer WITHOUT seeing what the system expects, so the
    count is an observation rather than a number worked backwards from a target.
    """
    locked = Shift.objects.select_for_update().get(pk=shift.pk)
    if not locked.is_open:
        raise AppError("الوردية مغلقة بالفعل", code="SHIFT_ALREADY_CLOSED")

    open_orders = locked.orders.filter(status__in=["OPEN", "IN_KITCHEN", "READY", "SERVED"]).count()
    if open_orders:
        raise AppError(
            f"لا يمكن إغلاق الوردية وبها {open_orders} طلب مفتوح",
            code="OPEN_ORDERS_REMAIN",
            extra={"open_orders": open_orders},
        )

    totals = compute_totals(locked)
    counted = Decimal(counted_cash).quantize(Decimal("0.01"))
    variance = counted - totals.expected_cash

    context = ScopeContext(organization_id=locked.organization_id, branch_id=locked.branch_id)
    limit = Decimal(str(resolver.get("shifts.max_variance", context)))
    require_reason = resolver.get("shifts.require_variance_reason", context)

    if abs(variance) > limit and approved_by is None:
        raise AppError(
            f"فرق النقدية {variance} يتجاوز الحد المسموح ({limit}) — يتطلب موافقة مدير.",
            code="VARIANCE_EXCEEDS_LIMIT",
            extra={"variance": str(variance), "limit": str(limit)},
        )
    if variance != 0 and require_reason and not reason:
        raise AppError("سبب الفرق مطلوب", code="VARIANCE_REASON_REQUIRED")

    locked.counted_cash = counted
    locked.variance = variance
    locked.variance_reason = reason[:200]
    locked.status = ShiftStatus.CLOSED
    locked.closed_at = timezone.now()
    locked.closed_by = user
    locked.z_report = {
        **x_report(locked),
        "is_final": True,
        "counted_cash": str(counted),
        "variance": str(variance),
        "variance_reason": reason,
        "closed_at": locked.closed_at.isoformat(),
        "closed_by": user.full_name_ar if user else None,
        "approved_by": approved_by.full_name_ar if approved_by else None,
    }
    locked.save()

    from apps.audit import services as audit

    audit.record(
        "shift.closed",
        branch=locked.branch,
        actor=user,
        approved_by=approved_by,
        obj=locked,
        detail={
            "expected_cash": totals.expected_cash,
            "counted_cash": counted,
            "variance": variance,
            "order_count": totals.order_count,
        },
    )
    if variance != 0:
        # Separate from the close itself: a variance is the loss-prevention
        # signal, and it should be findable without reading every shift close.
        audit.record(
            "shift.variance_recorded",
            branch=locked.branch,
            actor=user,
            approved_by=approved_by,
            obj=locked,
            detail={"variance": variance, "reason": reason, "limit": limit},
        )

    if locked.z_report.get("open_play_sessions"):
        logger.warning(
            "Shift closed with children still in the play area",
            extra={
                "shift": str(locked.id),
                "sessions": locked.z_report["open_play_sessions"],
                "liability": locked.z_report["open_play_liability"],
            },
        )

    if variance != 0:
        logger.warning(
            "Shift closed with a cash variance",
            extra={
                "shift": str(locked.id),
                "user": locked.user.full_name_ar if locked.user else None,
                "variance": str(variance),
            },
        )

    return locked
