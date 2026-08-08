"""
Taking money.

Every function here is idempotent and transactional. A payment that raised the
order's paid total but failed to record the Payment row — or the reverse —
would be a partial financial transaction, which docs/02 §49 forbids outright.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.exceptions import AppError
from apps.orders import state
from apps.orders.models import EventType, ItemStatus, Order, OrderStatus
from apps.orders.services import _record

from .models import Invoice, Payment, PaymentMethod, Refund

logger = logging.getLogger(__name__)


class Overpayment(AppError):
    status_code = 422
    code = "OVERPAYMENT"
    default_detail = "المبلغ أكبر من المستحق"


@transaction.atomic
def take_payment(
    *,
    order: Order,
    method: PaymentMethod,
    amount: Decimal,
    idempotency_key: str,
    tendered: Decimal | None = None,
    reference: str = "",
    shift=None,
    user=None,
    device_id=None,
) -> Payment:
    """
    Record a payment against an order.

    Supports split payment: several partial payments settle one order, and the
    order closes only when the balance reaches zero.
    """
    existing = Payment.objects.filter(idempotency_key=idempotency_key).first()
    if existing is not None:
        # A replay. Return the original rather than charging twice.
        logger.info("Replayed payment", extra={"key": idempotency_key})
        return existing

    locked = Order.objects.select_for_update().get(pk=order.pk)

    if locked.status in state.TERMINAL:
        raise state.InvalidStateTransition(
            f"لا يمكن تحصيل طلب في حالة {locked.status}", extra={"current": locked.status}
        )

    amount = Decimal(amount).quantize(Decimal("0.01"))
    if amount <= 0:
        raise AppError("المبلغ يجب أن يكون أكبر من صفر", code="INVALID_AMOUNT")

    if amount > locked.balance_due:
        raise Overpayment(
            f"المستحق {locked.balance_due} والمبلغ {amount}",
            extra={"due": str(locked.balance_due), "amount": str(amount)},
        )

    if method.requires_reference and not reference:
        raise AppError("رقم المرجع مطلوب لطريقة الدفع هذه", code="REFERENCE_REQUIRED")

    change = Decimal("0")
    if tendered is not None:
        tendered = Decimal(tendered).quantize(Decimal("0.01"))
        if tendered < amount:
            raise AppError("المبلغ المستلم أقل من المطلوب", code="INSUFFICIENT_TENDER")
        change = tendered - amount

    try:
        payment = Payment.objects.create(
            order=locked,
            method=method,
            shift=shift,
            amount=amount,
            tendered=tendered,
            change_given=change,
            reference=reference,
            idempotency_key=idempotency_key,
            received_by=user,
            device_id=device_id,
            created_by=user,
        )
    except IntegrityError:
        # Lost the race to a concurrent identical request; its row is correct.
        return Payment.objects.get(idempotency_key=idempotency_key)

    locked.paid_total = (locked.paid_total + amount).quantize(Decimal("0.01"))
    update_fields = ["paid_total", "updated_at"]

    if locked.is_settled:
        state.assert_transition(locked.status, OrderStatus.PAID)
        locked.status = OrderStatus.PAID
        locked.closed_at = timezone.now()
        update_fields += ["status", "closed_at"]

    locked.save(update_fields=update_fields)

    _record(
        locked,
        EventType.PAYMENT_TAKEN,
        payload={"amount": str(amount), "method": method.code},
        actor=user,
        device_id=device_id,
    )

    if locked.status == OrderStatus.PAID:
        _on_settled(locked, user=user)

    from apps.audit import services as audit

    audit.record(
        "payment.taken",
        branch=locked.branch,
        actor=user,
        obj=locked,
        object_label=locked.local_number,
        detail={
            "amount": amount,
            "method": method.code,
            "reference": reference,
            "order_status": locked.status,
        },
    )
    return payment


def _on_settled(order: Order, *, user=None) -> None:
    """Deduct stock and issue the invoice, once, when the order is fully paid."""
    from apps.configuration import resolver
    from apps.configuration.resolver import ScopeContext
    from apps.recipes.services import consume_for_modifier, consume_for_sale

    context = ScopeContext(organization_id=order.organization_id, branch_id=order.branch_id)
    deduct_on = resolver.get("inventory.deduct_on", context)
    allow_negative = resolver.get("inventory.allow_negative", context)

    if deduct_on == "PAYMENT":
        for item in order.items.filter(status=ItemStatus.ACTIVE).select_related("variant"):
            consume_for_sale(
                variant=item.variant,
                quantity=item.quantity,
                ref=order,
                user=user,
                allow_negative=allow_negative,
            )
            for modifier_link in item.modifiers.select_related("modifier"):
                if modifier_link.modifier is not None:
                    consume_for_modifier(
                        modifier=modifier_link.modifier,
                        quantity=item.quantity,
                        ref=order,
                        user=user,
                    )

    issue_invoice(order)


@transaction.atomic
def issue_invoice(order: Order) -> Invoice:
    """
    Freeze the receipt.

    The number comes from the device's reserved block (C9), so offline
    terminals produce sequential numbers without colliding.
    """
    existing = Invoice.objects.filter(order=order).first()
    if existing is not None:
        return existing

    number = _next_invoice_number(order)
    serial = f"{order.branch.code}-{order.opened_at:%Y}-{number:06d}"

    return Invoice.objects.create(
        order=order,
        invoice_number=number,
        serial=serial,
        snapshot=build_receipt(order),
    )


def _next_invoice_number(order: Order) -> int:
    """
    Consume one number from this device's block, allocating a new block if the
    current one is exhausted.
    """
    from apps.licensing.models import Device, InvoiceBlock
    from apps.licensing.services import allocate_invoice_block

    block = (
        InvoiceBlock.objects.select_for_update()
        .filter(branch=order.branch, device_id=order.device_id, exhausted_at__isnull=True)
        .order_by("range_start")
        .first()
    )

    if block is None:
        device = Device.objects.filter(id=order.device_id).first()
        if device is None:
            # No device (a web-created order): fall back to a branch-wide
            # sequence. Still gapless, just not block-allocated.
            highest = (
                Invoice.objects.filter(order__branch=order.branch)
                .order_by("-invoice_number")
                .values_list("invoice_number", flat=True)
                .first()
            )
            return (highest or 0) + 1
        block = allocate_invoice_block(device)

    number = block.next_unused
    block.next_unused += 1
    if block.next_unused > block.range_end:
        block.exhausted_at = timezone.now()
    block.save(update_fields=["next_unused", "exhausted_at", "updated_at"])
    return number


def build_receipt(order: Order) -> dict:
    """
    The receipt as structured data.

    Rendered identically to a thermal printer, a PDF or a preview pane — so
    what the cashier sees is what the customer receives.
    """
    return {
        "order_number": order.local_number,
        "order_type": order.order_type,
        "table": (order.table_session.table.number if order.table_session else None),
        "opened_at": order.opened_at.isoformat(),
        "cashier": order.opened_by.full_name_ar if order.opened_by else None,
        "items": [
            {
                "name": item.name_snapshot,
                "quantity": str(item.quantity),
                "unit_price": str(item.unit_price_snapshot),
                "line_total": str(item.line_total),
                "note": item.note,
                "modifiers": [m.name_snapshot for m in item.modifiers.all()],
            }
            for item in order.items.filter(status=ItemStatus.ACTIVE).prefetch_related("modifiers")
        ],
        "subtotal": str(order.subtotal),
        "discount_total": str(order.discount_total),
        "service_total": str(order.service_total),
        "tax_total": str(order.tax_total),
        "rounding_adjustment": str(order.rounding_adjustment),
        "grand_total": str(order.grand_total),
        "payments": [
            {"method": p.method.name_ar, "amount": str(p.amount)}
            for p in order.payments.select_related("method")
        ],
        "vat_percent": str(order.vat_percent),
        "service_percent": str(order.service_percent),
    }


@transaction.atomic
def refund(
    *,
    order: Order,
    amount: Decimal,
    reason: str,
    idempotency_key: str,
    original_payment: Payment | None = None,
    shift=None,
    user=None,
    approved_by=None,
) -> Refund:
    """
    Reverse money. Never edits or deletes the original payment.

    Two records instead of one silently altered one — which is the difference
    between an auditable correction and a cover-up.
    """
    existing = Refund.objects.filter(idempotency_key=idempotency_key).first()
    if existing is not None:
        return existing

    locked = Order.objects.select_for_update().get(pk=order.pk)

    amount = Decimal(amount).quantize(Decimal("0.01"))
    if amount <= 0:
        raise AppError("قيمة الاسترجاع يجب أن تكون أكبر من صفر", code="INVALID_AMOUNT")

    already = sum((r.amount for r in locked.refunds.all()), Decimal("0"))
    if amount + already > locked.paid_total:
        raise AppError(
            f"إجمالي الاسترجاع يتجاوز المدفوع ({locked.paid_total})",
            code="REFUND_EXCEEDS_PAID",
        )

    refund_row = Refund.objects.create(
        order=locked,
        original_payment=original_payment,
        shift=shift,
        amount=amount,
        reason=reason[:200],
        idempotency_key=idempotency_key,
        approved_by=approved_by,
        refunded_by=user,
        created_by=user,
    )

    if amount + already >= locked.paid_total:
        state.assert_transition(locked.status, OrderStatus.REFUNDED)
        locked.status = OrderStatus.REFUNDED
        locked.save(update_fields=["status", "updated_at"])

    from apps.audit import services as audit

    audit.record(
        "payment.refunded",
        branch=locked.branch,
        actor=user,
        approved_by=approved_by,
        obj=locked,
        object_label=locked.local_number,
        detail={"amount": amount, "reason": reason, "paid_total": locked.paid_total},
    )
    logger.warning(
        "Refund issued",
        extra={
            "order": locked.local_number,
            "amount": str(amount),
            "reason": reason,
            "approved_by": str(approved_by.id) if approved_by else None,
        },
    )
    return refund_row
