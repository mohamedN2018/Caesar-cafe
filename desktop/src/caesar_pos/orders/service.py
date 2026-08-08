"""
Taking an order on the terminal.

Every function here follows the same shape, and the shape IS the design:

    with transaction(db.connection):
        append the event
        refold and persist the projection
        enqueue the outbox row

One transaction. The event, the thing the screen reads, and the promise to tell
the server about it either all exist or none of them do.

The projection (`l_orders`, `l_order_items`) is a cache of the fold, kept so the
floor board can list twelve tables without replaying twelve event streams. The
events are the truth; if the two ever disagree, `reload` rebuilds the projection
and the events win.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from ..local import outbox
from ..local.db import Database, transaction
from ..vendored.money import TaxRules
from .events import TERMINAL, EventType, InvalidTransition, ItemStatus, OrderStatus, can_transition
from .fold import FoldedOrder, fold

logger = logging.getLogger(__name__)


class OrderClosed(RuntimeError):
    """
    The order takes no more edits.

    Refused HERE, not only in the UI. A greyed-out button is a hint; this is the
    rule, and it is the same one the server applies when the events sync.
    """


@dataclass(frozen=True)
class Settings:
    """
    The money rules resolved from the mirror, snapshotted onto an order at open.

    No defaults, for the same reason `TaxRules` has none: a service that built
    these by mistake would charge a rate nobody configured, and the mistake
    would look like a working system.
    """

    vat_percent: Decimal
    vat_enabled: bool
    vat_inclusive: bool
    service_percent: Decimal
    service_enabled: bool
    rounding_step: Decimal

    def to_rules(self) -> TaxRules:
        return TaxRules(
            vat_percent=self.vat_percent,
            vat_enabled=self.vat_enabled,
            vat_inclusive=self.vat_inclusive,
            service_percent=self.service_percent,
            service_enabled=self.service_enabled,
            rounding_step=self.rounding_step,
        )


def settings_from_mirror(db: Database, *, order_type: str = "DINE_IN") -> Settings:
    """
    Read `finance.*` out of `m_settings`.

    A terminal with no settings mirrored yet cannot price anything, so this
    raises rather than defaulting. Selling at a guessed VAT rate is worse than
    refusing to sell.
    """

    def value(key: str, cast):
        raw = db.scalar("SELECT value FROM m_settings WHERE key = ?", (f"finance.{key}",))
        if raw is None:
            raise RuntimeError(
                f"finance.{key} has not synced yet. This terminal cannot price an order "
                "until the config stream has been pulled."
            )
        return cast(json.loads(raw))

    applies_to = value("service_applies_to", list)
    service_enabled = value("service_enabled", bool) and order_type in applies_to

    return Settings(
        vat_percent=Decimal(str(value("vat_percent", str))),
        vat_enabled=value("vat_enabled", bool),
        vat_inclusive=value("vat_inclusive", bool),
        service_percent=Decimal(str(value("service_percent", str))),
        service_enabled=service_enabled,
        rounding_step=Decimal(str(value("rounding_step", str))),
    )


# ── opening ──────────────────────────────────────────────────────────────────


def open_order(
    db: Database,
    *,
    settings: Settings,
    order_type: str = "DINE_IN",
    table_id: str | None = None,
    shift_id: str | None = None,
    device_code: str = "01",
    branch_code: str = "MB",
    order_id: str | None = None,
) -> FoldedOrder:
    """
    Open an order with a locally-minted id and number.

    Both are generated here rather than requested, because an offline terminal
    cannot make a round-trip and a cashier cannot wait for one. `MB-01-0042`
    stays unique without coordination because the device code is in it.
    """
    order_id = order_id or str(uuid.uuid4())
    local_number = _next_local_number(db, branch_code, device_code)
    now = datetime.now(UTC).isoformat()

    with transaction(db.connection):
        db.insert(
            "l_orders",
            {
                "id": order_id,
                "local_number": local_number,
                "order_type": order_type,
                "status": OrderStatus.OPEN,
                "table_id": table_id,
                "shift_id": shift_id,
                # Snapshotted now. A VAT change pulled mid-service must not
                # rewrite a bill the customer is already looking at.
                "vat_percent": str(settings.vat_percent if settings.vat_enabled else 0),
                "service_percent": str(settings.service_percent if settings.service_enabled else 0),
                "vat_inclusive": settings.vat_inclusive,
                "rounding_step": str(settings.rounding_step),
                "opened_at": now,
            },
        )
        _append_event(db, order_id, EventType.ORDER_OPENED, {"order_type": order_type}, sequence=1)
        outbox.enqueue(
            db,
            entity_type="order_open",
            entity_id=order_id,
            payload={
                "order_id": order_id,
                "order_type": order_type,
                "local_number": local_number,
                "table_id": table_id,
                # The server attributes the sale to this drawer. Omitting it left
                # every synced order with no shift, which empties the Z-report of
                # exactly the terminal that made the sales.
                "shift_id": shift_id,
            },
        )

    return reload(db, order_id)


def _next_local_number(db: Database, branch_code: str, device_code: str) -> str:
    today = datetime.now(UTC).date().isoformat()
    count = db.scalar(
        "SELECT COUNT(*) FROM l_orders WHERE substr(opened_at, 1, 10) = ?", (today,), default=0
    )
    return f"{branch_code}-{device_code}-{count + 1:04d}"


# ── editing ──────────────────────────────────────────────────────────────────


def add_item(
    db: Database,
    order_id: str,
    *,
    variant_id: str,
    quantity: Decimal = Decimal("1"),
    modifiers: list[dict] | None = None,
    note: str = "",
) -> FoldedOrder:
    """
    Add a line, snapshotting the price from the mirror at this instant.

    The snapshot is the point. The mirror may be updated by the puller thirty
    seconds from now, and this line must keep the price the customer was quoted.
    """
    order = _editable(db, order_id)
    variant = _variant(db, variant_id)

    payload = {
        "line_id": str(uuid.uuid4()),
        "variant_id": variant_id,
        "name_snapshot": variant["name"],
        "unit_price_snapshot": variant["price"],
        "cost_snapshot": variant["cost"],
        "tax_exempt_snapshot": variant["tax_exempt"],
        "station_id": variant["station_id"],
        "quantity": str(quantity),
        "modifiers": modifiers or [],
        "note": note,
    }
    return _record(db, order, EventType.ITEM_ADDED, payload)


def change_quantity(db: Database, order_id: str, line_id: str, quantity: Decimal) -> FoldedOrder:
    if quantity <= 0:
        raise ValueError("الكمية يجب أن تكون أكبر من صفر")

    order = _editable(db, order_id)
    return _record(
        db,
        order,
        EventType.ITEM_QUANTITY_CHANGED,
        {"line_id": line_id, "quantity": str(quantity)},
    )


def void_item(db: Database, order_id: str, line_id: str, *, reason: str) -> FoldedOrder:
    order = _editable(db, order_id)
    return _record(db, order, EventType.ITEM_VOIDED, {"line_id": line_id, "reason": reason})


def apply_discount(
    db: Database,
    order_id: str,
    *,
    percent: Decimal,
    line_id: str | None = None,
    reason: str = "",
) -> FoldedOrder:
    if not (Decimal("0") <= percent <= Decimal("100")):
        raise ValueError("نسبة خصم غير صالحة")

    order = _editable(db, order_id)
    payload: dict = {"percent": str(percent), "reason": reason}
    if line_id:
        payload["line_id"] = line_id

    return _record(db, order, EventType.DISCOUNT_APPLIED, payload)


def set_note(db: Database, order_id: str, line_id: str, note: str) -> FoldedOrder:
    order = _editable(db, order_id)
    return _record(db, order, EventType.ITEM_NOTE_SET, {"line_id": line_id, "note": note})


def fire(db: Database, order_id: str) -> FoldedOrder:
    """
    Send the unfired items to the kitchen.

    Refuses when there is nothing new. A second press that re-sent everything
    would have the kitchen make the first round twice, and the cashier would
    have no way to tell.
    """
    order = _editable(db, order_id)
    if not order.unfired_items:
        raise ValueError("لا توجد أصناف جديدة لإرسالها")

    return _record(db, order, EventType.ORDER_FIRED, {"fired_at": datetime.now(UTC).isoformat()})


def assign_table(db: Database, order_id: str, table_id: str) -> FoldedOrder:
    order = _editable(db, order_id)
    return _record(db, order, EventType.TABLE_ASSIGNED, {"table_id": table_id})


def void_order(db: Database, order_id: str, *, reason: str) -> FoldedOrder:
    order = load(db, order_id)
    if order.status in TERMINAL:
        raise OrderClosed(f"لا يمكن إلغاء طلب في حالة {order.status}")
    if not can_transition(order.status, OrderStatus.CANCELLED):
        raise InvalidTransition(order.status, OrderStatus.CANCELLED)

    return _record(db, order, EventType.ORDER_VOIDED, {"reason": reason})


# ── payment ──────────────────────────────────────────────────────────────────


def take_payment(
    db: Database,
    order_id: str,
    *,
    method_id: str,
    amount: Decimal,
    tendered: Decimal | None = None,
    reference: str = "",
    shift_id: str | None = None,
) -> FoldedOrder:
    """
    Record money taken. Supports split payment; the order closes at zero balance.

    The idempotency key is minted here and travels with the operation, so a push
    that times out and is resent charges exactly once — the server returns the
    original payment rather than creating a second.
    """
    order = load(db, order_id)
    if order.status in TERMINAL:
        raise OrderClosed(f"لا يمكن تحصيل طلب في حالة {order.status}")

    amount = Decimal(amount).quantize(Decimal("0.01"))
    if amount <= 0:
        raise ValueError("المبلغ يجب أن يكون أكبر من صفر")
    if amount > order.balance_due:
        raise ValueError(f"المستحق {order.balance_due} والمبلغ {amount}")

    change = Decimal("0.00")
    if tendered is not None:
        tendered = Decimal(tendered).quantize(Decimal("0.01"))
        if tendered < amount:
            raise ValueError("المبلغ المستلم أقل من المطلوب")
        change = tendered - amount

    payment_id = str(uuid.uuid4())
    idempotency_key = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()

    with transaction(db.connection):
        db.insert(
            "l_payments",
            {
                "id": payment_id,
                "order_id": order_id,
                "method_id": method_id,
                "amount": str(amount),
                "tendered": str(tendered) if tendered is not None else None,
                "change_given": str(change),
                "reference": reference,
                "idempotency_key": idempotency_key,
                "shift_id": shift_id,
                "taken_at": now,
            },
        )
        sequence = _append_event(
            db,
            order_id,
            EventType.PAYMENT_TAKEN,
            {"amount": str(amount), "method_id": method_id},
        )
        outbox.enqueue(
            db,
            entity_type="payment",
            entity_id=order_id,
            aggregate_seq=sequence,
            payload={
                "order_id": order_id,
                "method_id": method_id,
                "amount": str(amount),
                "tendered": str(tendered) if tendered is not None else None,
                "reference": reference,
                "idempotency_key": idempotency_key,
                "shift_id": shift_id,
            },
        )

    order = reload(db, order_id)

    if order.is_settled:
        order = _record(db, order, EventType.ORDER_CLOSED, {}, enqueue=False)

    return order


# ── reading ──────────────────────────────────────────────────────────────────


def load(db: Database, order_id: str) -> FoldedOrder:
    """Fold from the events — the truth, not the projection."""
    header = db.one("SELECT * FROM l_orders WHERE id = ?", (order_id,))
    if header is None:
        raise LookupError(f"الطلب غير موجود: {order_id}")

    events = [
        {
            "sequence": row["sequence"],
            "event_type": row["event_type"],
            "payload": json.loads(row["payload"]),
        }
        for row in db.query(
            "SELECT sequence, event_type, payload FROM l_order_events "
            "WHERE order_id = ? ORDER BY sequence",
            (order_id,),
        )
    ]

    rules = TaxRules(
        vat_percent=Decimal(header["vat_percent"]),
        vat_enabled=Decimal(header["vat_percent"]) > 0,
        vat_inclusive=bool(header["vat_inclusive"]),
        service_percent=Decimal(header["service_percent"]),
        service_enabled=Decimal(header["service_percent"]) > 0,
        rounding_step=Decimal(header["rounding_step"]),
    )

    return fold(
        order_id,
        events,
        rules,
        local_number=header["local_number"],
        order_type=header["order_type"],
        table_id=header["table_id"],
    )


def reload(db: Database, order_id: str) -> FoldedOrder:
    """Fold, then write the projection back. The events always win."""
    order = load(db, order_id)
    _persist_projection(db, order)
    return order


def open_orders(db: Database) -> list[dict]:
    """The floor board. Reads the projection — twelve tables, not twelve folds."""
    from .events import OPEN_STATUSES

    placeholders = ", ".join("?" for _ in OPEN_STATUSES)
    rows = db.query(
        f"SELECT * FROM l_orders WHERE status IN ({placeholders}) ORDER BY opened_at",  # noqa: S608
        OPEN_STATUSES,
    )
    return [dict(row) for row in rows]


# ── internals ────────────────────────────────────────────────────────────────


def _editable(db: Database, order_id: str) -> FoldedOrder:
    order = load(db, order_id)
    if order.status in TERMINAL:
        raise OrderClosed(f"الطلب في حالة {order.status} ولا يقبل التعديل")
    return order


def _record(
    db: Database,
    order: FoldedOrder,
    event_type: str,
    payload: dict,
    *,
    enqueue: bool = True,
) -> FoldedOrder:
    """Append, refold, persist, queue — in one transaction."""
    event_id = str(uuid.uuid4())

    with transaction(db.connection):
        sequence = _append_event(db, order.order_id, event_type, payload, event_id=event_id)

        if enqueue:
            outbox.enqueue(
                db,
                entity_type="order_event",
                entity_id=order.order_id,
                aggregate_seq=sequence,
                payload={
                    "order_id": order.order_id,
                    "event": {
                        "id": event_id,
                        "sequence": sequence,
                        "type": event_type,
                        "payload": payload,
                    },
                },
            )

    return reload(db, order.order_id)


def _append_event(
    db: Database,
    order_id: str,
    event_type: str,
    payload: dict,
    *,
    sequence: int | None = None,
    event_id: str | None = None,
) -> int:
    if sequence is None:
        highest = db.scalar(
            "SELECT MAX(sequence) FROM l_order_events WHERE order_id = ?", (order_id,), default=0
        )
        sequence = (highest or 0) + 1

    db.insert(
        "l_order_events",
        {
            "id": event_id or str(uuid.uuid4()),
            "order_id": order_id,
            "sequence": sequence,
            "event_type": event_type,
            "payload": payload,
            "occurred_at": datetime.now(UTC).isoformat(),
        },
    )
    return sequence


def _persist_projection(db: Database, order: FoldedOrder) -> None:
    totals = order.totals

    with transaction(db.connection):
        db.update(
            "l_orders",
            {
                "status": order.status,
                "table_id": order.table_id,
                "discount_percent": str(order.discount_percent),
                "subtotal": str(totals.subtotal),
                "discount_total": str(totals.discount_total),
                "service_total": str(totals.service_total),
                "tax_total": str(totals.tax_total),
                "grand_total": str(totals.grand_total),
                "paid_total": str(order.paid_total),
                "closed_at": (datetime.now(UTC).isoformat() if order.status in TERMINAL else None),
            },
            where="id = ?",
            params=(order.order_id,),
        )

        # Replaced wholesale rather than diffed. The projection is a cache of
        # the fold; rebuilding it is cheap and cannot drift, whereas a diff can.
        db.execute("DELETE FROM l_order_items WHERE order_id = ?", (order.order_id,))
        for item in order.items:
            db.insert(
                "l_order_items",
                {
                    "line_id": item.line_id,
                    "order_id": order.order_id,
                    "variant_id": item.variant_id,
                    "station_id": item.station_id,
                    "name_snapshot": item.name_snapshot,
                    "unit_price_snapshot": str(item.unit_price_snapshot),
                    "cost_snapshot": str(item.cost_snapshot),
                    "tax_exempt_snapshot": item.tax_exempt_snapshot,
                    "quantity": str(item.quantity),
                    "discount_percent": str(item.discount_percent),
                    "line_total": str(item.line_total),
                    "modifiers": item.modifiers,
                    "note": item.note,
                    "status": item.status,
                    "fired_at": item.fired_at,
                },
            )


def _variant(db: Database, variant_id: str) -> dict:
    row = db.one(
        """
        SELECT v.price, v.cost, v.name_ar AS variant_name, v.payload AS variant_payload,
               p.name_ar AS product_name, p.station_id, p.payload AS product_payload
        FROM m_variants v
        JOIN m_products p ON p.id = v.product_id
        WHERE v.id = ?
        """,
        (variant_id,),
    )
    if row is None:
        raise LookupError(f"الصنف غير موجود في النسخة المحلية: {variant_id}")

    product = json.loads(row["product_payload"] or "{}")
    name = f"{row['product_name']} {row['variant_name'] or ''}".strip()

    return {
        "name": name,
        "price": row["price"],
        "cost": row["cost"],
        "station_id": row["station_id"],
        "tax_exempt": bool(product.get("is_tax_exempt", False)),
    }


__all__ = [
    "ItemStatus",
    "OrderClosed",
    "OrderStatus",
    "Settings",
    "add_item",
    "apply_discount",
    "assign_table",
    "change_quantity",
    "fire",
    "load",
    "open_order",
    "open_orders",
    "reload",
    "set_note",
    "settings_from_mirror",
    "take_payment",
    "void_item",
    "void_order",
]
