"""
What each pushed operation actually does.

Every handler is thin on purpose: it unpacks a payload and calls the same
service the online path calls. A sync-specific code path that "does the same
thing" as the ordinary one is a second implementation of the business rule, and
the two only stay in step until the first time somebody fixes a bug in one.

Handlers raise. `services.apply_push` decides what a raised exception means:

    ConflictError   → the operation goes to `sync_conflicts` for a human
    AppError        → REJECTED, never retried; a structurally invalid operation
                      is invalid forever, and retrying it every five minutes for
                      a week buries the failures that matter
    anything else   → REJECTED with the exception recorded
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal

from django.utils import timezone

from apps.core.exceptions import AppError, ConflictError, NotFoundError
from apps.orders.models import Order, OrderStatus, OrderType

logger = logging.getLogger(__name__)

HANDLERS: dict[str, object] = {}


def handles(entity_type: str):
    def register(func):
        HANDLERS[entity_type] = func
        func.entity_type = entity_type
        return func

    return register


class SequenceGap(ConflictError):
    code = "SEQUENCE_GAP"


class OrderAlreadyClosed(ConflictError):
    code = "ORDER_ALREADY_CLOSED"


# ── orders ───────────────────────────────────────────────────────────────────


@handles("order_open")
def _order_open(*, device, branch, payload: dict, actor=None) -> dict:
    """
    Register an order a device opened while offline.

    The id is client-minted, so this is naturally idempotent: an order that
    already exists is returned rather than duplicated. The local number the
    terminal printed is preserved — it is on a slip in a customer's hand.
    """
    from apps.orders import services as order_services

    order_id = uuid.UUID(str(payload["order_id"]))
    existing = Order.objects.filter(id=order_id).first()
    if existing is not None:
        return {"order_id": str(existing.id), "local_number": existing.local_number}

    order = order_services.open_order(
        branch=branch,
        order_type=payload.get("order_type", OrderType.DINE_IN),
        order_id=order_id,
        local_number=payload.get("local_number"),
        device_id=device.id if device else None,
        user=actor,
    )
    return {"order_id": str(order.id), "local_number": order.local_number}


@handles("order_event")
def _order_event(*, device, branch, payload: dict, actor=None) -> dict:
    """
    Append one event to an order's stream.

    Two rejections that are NOT the same thing:

      * `SEQUENCE_GAP` — event 5 arrived before event 4. Self-healing: the
        client re-sends from 4 and nobody is involved. The server's own
        sequence is assigned on apply, so this checks the DEVICE's per-order
        numbering, which is what can actually develop a hole in a partial batch.

      * `ORDER_ALREADY_CLOSED` — an item was added to an order another device
        has already been paid for. Not self-healing and not the client's fault:
        the food was made and somebody has to decide who pays. It goes to a
        human with the server's state attached.
    """
    from apps.orders import services as order_services
    from apps.sync.models import SyncOperation

    order = Order.objects.filter(id=payload["order_id"], branch=branch).first()
    if order is None:
        raise NotFoundError("الطلب غير موجود", code="ORDER_NOT_FOUND")

    event = payload["event"]
    sequence = event.get("sequence")

    if sequence is not None and device is not None:
        highest = (
            SyncOperation.objects.filter(
                device=device,
                entity_type="order_event",
                entity_id=order.id,
                status="APPLIED",
                aggregate_seq__isnull=False,
            )
            .order_by("-aggregate_seq")
            .values_list("aggregate_seq", flat=True)
            .first()
        ) or 0

        if sequence > highest + 1:
            raise SequenceGap(
                f"ينقص الحدث رقم {highest + 1}",
                code="SEQUENCE_GAP",
                extra={"expected": highest + 1, "received": sequence, "order_id": str(order.id)},
            )

    if order.status in {OrderStatus.PAID, OrderStatus.REFUNDED, OrderStatus.CANCELLED}:
        raise OrderAlreadyClosed(
            f"الطلب {order.local_number} تم إغلاقه بالفعل",
            code="ORDER_ALREADY_CLOSED",
            extra={
                "order_id": str(order.id),
                "local_number": order.local_number,
                "status": order.status,
                "grand_total": str(order.grand_total),
            },
        )

    result = order_services.apply_events(
        order,
        [
            {
                "id": event["id"],
                "type": event["type"],
                "payload": event.get("payload", {}),
                "occurred_at": event.get("occurred_at"),
            }
        ],
        actor=actor,
        device_id=device.id if device else None,
    )
    return {
        "order_id": str(order.id),
        "applied": result.applied,
        "skipped": result.skipped,
        "grand_total": str(result.order.grand_total),
    }


@handles("payment")
def _payment(*, device, branch, payload: dict, actor=None) -> dict:
    """
    Money taken offline.

    The idempotency key is the client's, so a payment retried after a timeout is
    charged exactly once — the second call returns the first payment rather than
    creating another.
    """
    from apps.payments import services as payment_services
    from apps.payments.models import Invoice, PaymentMethod

    order = Order.objects.filter(id=payload["order_id"], branch=branch).first()
    if order is None:
        raise NotFoundError("الطلب غير موجود", code="ORDER_NOT_FOUND")

    method = PaymentMethod.objects.filter(id=payload["method_id"], branch=branch).first()
    if method is None:
        raise NotFoundError("طريقة الدفع غير موجودة", code="METHOD_NOT_FOUND")

    payment = payment_services.take_payment(
        order=order,
        method=method,
        amount=Decimal(str(payload["amount"])),
        idempotency_key=payload["idempotency_key"],
        tendered=Decimal(str(payload["tendered"])) if payload.get("tendered") else None,
        reference=payload.get("reference", ""),
        shift=_shift_for(payload.get("shift_id")),
        user=actor,
        device_id=device.id if device else None,
    )

    # A terminal that exhausted its block offline printed a provisional serial.
    # Recording it beside the permanent one is what lets a customer's slip be
    # matched to the invoice later (C9).
    provisional = payload.get("provisional_serial", "")
    invoice = Invoice.objects.filter(order=order).first()
    if provisional and invoice is not None and not invoice.provisional_serial:
        invoice.provisional_serial = provisional[:32]
        invoice.save(update_fields=["provisional_serial", "updated_at"])

    return {
        "payment_id": str(payment.id),
        "order_status": Order.objects.values_list("status", flat=True).get(pk=order.pk),
        "invoice_serial": invoice.serial if invoice else None,
    }


@handles("refund")
def _refund(*, device, branch, payload: dict, actor=None) -> dict:
    from apps.payments import services as payment_services

    order = Order.objects.filter(id=payload["order_id"], branch=branch).first()
    if order is None:
        raise NotFoundError("الطلب غير موجود", code="ORDER_NOT_FOUND")

    refund = payment_services.refund(
        order=order,
        amount=Decimal(str(payload["amount"])),
        reason=payload.get("reason", ""),
        idempotency_key=payload["idempotency_key"],
        shift=_shift_for(payload.get("shift_id")),
        user=actor,
    )
    return {"refund_id": str(refund.id)}


# ── shifts ───────────────────────────────────────────────────────────────────


@handles("shift_open")
def _shift_open(*, device, branch, payload: dict, actor=None) -> dict:
    """
    One open shift per device, enforced by the service.

    A device that crashed mid-shift and re-opened one on restart would otherwise
    end the day with two drawers to reconcile and no way to say which counted.
    """
    from apps.shifts import services as shift_services
    from apps.shifts.models import Shift, ShiftStatus

    shift_id = payload.get("shift_id")
    if shift_id:
        existing = Shift.objects.filter(id=shift_id).first()
        if existing is not None:
            return {"shift_id": str(existing.id), "replayed": True}

    open_now = Shift.objects.filter(
        device_id=device.id if device else None, status=ShiftStatus.OPEN
    ).first()
    if open_now is not None:
        raise ConflictError(
            "الجهاز لديه وردية مفتوحة بالفعل",
            code="SHIFT_ALREADY_OPEN",
            extra={"shift_id": str(open_now.id), "opened_at": open_now.opened_at.isoformat()},
        )

    shift = shift_services.open_shift(
        branch=branch,
        user=actor,
        device_id=device.id if device else None,
        opening_cash=Decimal(str(payload.get("opening_cash", "0"))),
        # Adopt the client's id, so the cash movements it already queued against
        # that id resolve instead of pointing at nothing.
        shift_id=shift_id,
    )
    return {"shift_id": str(shift.id)}


@handles("shift_close")
def _shift_close(*, device, branch, payload: dict, actor=None) -> dict:
    """
    The Z-report is recomputed here, not accepted from the device.

    A terminal computes its own close so the cashier can count and leave during
    an outage; the server's figure is the one that counts. When they differ,
    that difference is itself the finding.
    """
    from apps.shifts import services as shift_services
    from apps.shifts.models import Shift, ShiftStatus

    shift = Shift.objects.filter(id=payload["shift_id"], branch=branch).first()
    if shift is None:
        raise NotFoundError("الوردية غير موجودة", code="SHIFT_NOT_FOUND")
    if shift.status != ShiftStatus.OPEN:
        return {"shift_id": str(shift.id), "replayed": True, "variance": str(shift.variance)}

    closed = shift_services.close_shift(
        shift=shift,
        counted_cash=Decimal(str(payload["counted_cash"])),
        reason=payload.get("reason", ""),
        user=actor,
    )

    client_expected = payload.get("client_expected_cash")
    result = {"shift_id": str(closed.id), "variance": str(closed.variance)}

    if client_expected is not None:
        drift = Decimal(str(client_expected)) - Decimal(closed.z_report["expected_cash"])
        result["client_expected_cash"] = str(client_expected)
        result["server_client_drift"] = str(drift)
        if drift != 0:
            logger.warning(
                "Device and server disagreed on expected cash",
                extra={"shift": str(closed.id), "drift": str(drift)},
            )
    return result


@handles("cash_movement")
def _cash_movement(*, device, branch, payload: dict, actor=None) -> dict:
    from apps.shifts import services as shift_services
    from apps.shifts.models import Shift

    shift = Shift.objects.filter(id=payload["shift_id"], branch=branch).first()
    if shift is None:
        raise NotFoundError("الوردية غير موجودة", code="SHIFT_NOT_FOUND")

    movement = shift_services.record_cash_movement(
        shift=shift,
        movement_type=payload["movement_type"],
        amount=Decimal(str(payload["amount"])),
        reason=payload.get("reason", ""),
        user=actor,
    )
    return {"movement_id": str(movement.id)}


# ── inventory ────────────────────────────────────────────────────────────────


@handles("waste")
def _waste(*, device, branch, payload: dict, actor=None) -> dict:
    """
    Append-only: each waste event is a distinct real-world thing that happened,
    so two devices reporting waste on the same item are two facts, not a
    conflict to reconcile.
    """
    from apps.inventory import services as inventory_services
    from apps.inventory.models import InventoryItem

    item = InventoryItem.objects.filter(id=payload["item_id"], branch=branch).first()
    if item is None:
        raise NotFoundError("الصنف غير موجود", code="ITEM_NOT_FOUND")

    movement = inventory_services.record_waste(
        item=item,
        quantity=Decimal(str(payload["quantity"])),
        reason=payload.get("reason", ""),
        user=actor,
        device_id=device.id if device else None,
    )
    return {"movement_id": str(movement.id)}


# ── kids ─────────────────────────────────────────────────────────────────────


@handles("play_check_in")
def _play_check_in(*, device, branch, payload: dict, actor=None) -> dict:
    """
    A child admitted during an outage.

    Capacity was already enforced locally — it has to be, a child is physically
    present — and it is enforced again here. When the server refuses, the child
    is already inside and nobody is going to remove them, so this is a CONFLICT
    for a human rather than a rejection that discards the visit.
    """
    from apps.kids import services as kids_services
    from apps.kids.models import PlayArea, PlaySession, PlayTariff

    session_id = payload.get("session_id")
    if session_id and PlaySession.objects.filter(id=session_id).exists():
        return {"session_id": str(session_id), "replayed": True}

    area = PlayArea.objects.filter(id=payload["area_id"], branch=branch).first()
    if area is None:
        raise NotFoundError("الصالة غير موجودة", code="AREA_NOT_FOUND")

    tariff = PlayTariff.objects.filter(id=payload.get("tariff_id"), area=area).first()

    try:
        result = kids_services.check_in(
            area=area,
            child_name=payload["child_name"],
            guardian_name=payload["guardian_name"],
            guardian_phone=payload.get("guardian_phone", ""),
            age_months=payload.get("age_months"),
            tariff=tariff,
            tag_number=payload["tag_number"],
            session_id=session_id,
            device_id=device.id if device else None,
            user=actor,
            now=_parse_time(payload.get("checked_in_at")),
        )
    except kids_services.CapacityExceeded as exc:
        raise ConflictError(
            str(exc.detail),
            code="KIDS_AREA_FULL",
            extra={"area_id": str(area.id), "occupancy": area.occupancy()},
        ) from exc

    return {"session_id": str(result.session.id), "warnings": result.warnings}


@handles("play_check_out")
def _play_check_out(*, device, branch, payload: dict, actor=None) -> dict:
    from apps.kids import services as kids_services
    from apps.kids.models import PlaySession

    session = PlaySession.objects.filter(id=payload["session_id"], branch=branch).first()
    if session is None:
        raise NotFoundError("الجلسة غير موجودة", code="SESSION_NOT_FOUND")
    if not session.is_open:
        return {"session_id": str(session.id), "replayed": True, "charge": str(session.payable)}

    closed = kids_services.check_out(
        session,
        verified=payload.get("verified", False),
        user=actor,
        now=_parse_time(payload.get("checked_out_at")),
    )
    order = None
    if payload.get("bill", True):
        order = kids_services.bill_session(
            closed, user=actor, device_id=device.id if device else None
        )
        closed.refresh_from_db()

    return {
        "session_id": str(closed.id),
        "charge": str(closed.payable),
        "computed_charge": str(closed.computed_charge),
        "order_id": str(order.id) if order else None,
    }


# ── helpers ──────────────────────────────────────────────────────────────────


def _shift_for(shift_id):
    if not shift_id:
        return None
    from apps.shifts.models import Shift

    return Shift.objects.filter(id=shift_id).first()


def _parse_time(value):
    if not value:
        return None
    from django.utils.dateparse import parse_datetime

    parsed = parse_datetime(value)
    if parsed is None:
        raise AppError("تاريخ غير صالح", code="INVALID_TIMESTAMP")
    return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed)
