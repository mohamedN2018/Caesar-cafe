"""
Folding an event stream into an order.

This is the client half of commitment C1, and it has exactly one job worth
stating: **produce the same numbers the server will produce for the same
events.** It does that by not doing the arithmetic itself — every total comes
from `vendored/money.py`, which is byte-identical to the backend's copy and
verified so in CI.

The fold is pure: events in, a folded order out, nothing written. That is what
lets the golden-file test run it directly against the same fixture the server
uses, which is the only evidence that the two agree that is worth having.

Snapshots matter as much here as on the server. A line records the price, the
name and the cost AS SOLD, so a price change pulled from the mirror mid-service
cannot rewrite a bill the customer is already looking at.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ..vendored.money import OrderLine, OrderTotals, TaxRules, compute_order
from .events import EventType, ItemStatus, OrderStatus

ZERO = Decimal("0.00")


@dataclass
class FoldedItem:
    line_id: str
    variant_id: str
    name_snapshot: str
    unit_price_snapshot: Decimal
    cost_snapshot: Decimal = ZERO
    tax_exempt_snapshot: bool = False
    quantity: Decimal = Decimal("1")
    discount_percent: Decimal = ZERO
    modifiers: list[dict] = field(default_factory=list)
    note: str = ""
    status: str = ItemStatus.ACTIVE
    station_id: str | None = None
    fired_at: str | None = None
    line_total: Decimal = ZERO

    @property
    def is_active(self) -> bool:
        return self.status == ItemStatus.ACTIVE

    @property
    def modifier_deltas(self) -> tuple[Decimal, ...]:
        return tuple(Decimal(str(m.get("price_delta", "0"))) for m in self.modifiers)


@dataclass
class FoldedOrder:
    order_id: str
    status: str = OrderStatus.DRAFT
    order_type: str = "DINE_IN"
    table_id: str | None = None
    local_number: str = ""
    discount_percent: Decimal = ZERO
    discount_reason: str = ""
    paid_total: Decimal = ZERO
    items: list[FoldedItem] = field(default_factory=list)
    totals: OrderTotals | None = None

    @property
    def active_items(self) -> list[FoldedItem]:
        return [item for item in self.items if item.is_active]

    @property
    def grand_total(self) -> Decimal:
        return self.totals.grand_total if self.totals else ZERO

    @property
    def balance_due(self) -> Decimal:
        return self.grand_total - self.paid_total

    @property
    def is_settled(self) -> bool:
        return self.balance_due <= ZERO

    @property
    def unfired_items(self) -> list[FoldedItem]:
        """What a fire sends. `fired_at` is what makes a second press idempotent."""
        return [item for item in self.active_items if item.fired_at is None]

    def item(self, line_id: str) -> FoldedItem | None:
        return next((i for i in self.items if i.line_id == line_id), None)


def fold(order_id: str, events: list[dict], rules: TaxRules, **header) -> FoldedOrder:
    """
    Replay `events` into an order.

    `rules` are the ones SNAPSHOTTED on the order at open time, not whatever the
    mirror currently says. A VAT change that arrived ten minutes ago must not
    alter a bill already on the counter — the same reason the server stores them
    on the order row.
    """
    order = FoldedOrder(order_id=order_id, **header)

    for event in sorted(events, key=lambda e: e.get("sequence", 0)):
        _apply(order, event.get("event_type", ""), event.get("payload") or {})

    recalculate(order, rules)
    return order


def recalculate(order: FoldedOrder, rules: TaxRules) -> FoldedOrder:
    """
    Compute totals with the SAME module the server runs.

    Line totals are written back onto each item so the receipt and the order
    screen show the figure the total was actually built from, rather than one
    recomputed slightly differently for display.
    """
    active = order.active_items

    lines = [
        OrderLine(
            unit_price=item.unit_price_snapshot,
            quantity=item.quantity,
            discount_percent=item.discount_percent,
            modifier_deltas=item.modifier_deltas,
            tax_exempt=item.tax_exempt_snapshot,
        )
        for item in active
    ]

    totals = compute_order(lines, rules, order.discount_percent)

    for item, line_totals in zip(active, totals.lines, strict=True):
        item.line_total = line_totals.net

    order.totals = totals
    return order


# ── handlers ─────────────────────────────────────────────────────────────────


def _apply(order: FoldedOrder, event_type: str, payload: dict) -> None:
    handler = _HANDLERS.get(event_type)
    if handler is None:
        # An event this build does not know about — the terminal is older than
        # the server. Skipping is right: refusing to render an order because one
        # event is unfamiliar would take the whole table off the screen.
        return
    handler(order, payload)


def _order_opened(order: FoldedOrder, payload: dict) -> None:
    order.status = OrderStatus.OPEN
    order.order_type = payload.get("order_type", order.order_type)


def _item_added(order: FoldedOrder, payload: dict) -> None:
    order.items.append(
        FoldedItem(
            line_id=payload["line_id"],
            variant_id=payload["variant_id"],
            # Snapshots, exactly as the server takes them: a receipt is a record
            # of what was sold at what price.
            name_snapshot=payload.get("name_snapshot", ""),
            unit_price_snapshot=Decimal(str(payload.get("unit_price_snapshot", "0"))),
            cost_snapshot=Decimal(str(payload.get("cost_snapshot", "0"))),
            tax_exempt_snapshot=bool(payload.get("tax_exempt_snapshot", False)),
            quantity=Decimal(str(payload.get("quantity", "1"))),
            modifiers=payload.get("modifiers", []),
            note=payload.get("note", ""),
            station_id=payload.get("station_id"),
        )
    )


def _quantity_changed(order: FoldedOrder, payload: dict) -> None:
    item = order.item(payload["line_id"])
    if item is not None:
        item.quantity = Decimal(str(payload["quantity"]))


def _item_voided(order: FoldedOrder, payload: dict) -> None:
    """
    Marks, never removes.

    A deleted line is an unexplained gap in a financial record; a voided one is
    an auditable decision, and the void rate per user is a loss-prevention
    signal the server reports on.
    """
    item = order.item(payload["line_id"])
    if item is not None:
        item.status = ItemStatus.VOIDED


def _note_set(order: FoldedOrder, payload: dict) -> None:
    item = order.item(payload["line_id"])
    if item is not None:
        item.note = payload.get("note", "")


def _discount_applied(order: FoldedOrder, payload: dict) -> None:
    percent = Decimal(str(payload.get("percent", "0")))

    if line_id := payload.get("line_id"):
        item = order.item(line_id)
        if item is not None:
            item.discount_percent = percent
    else:
        order.discount_percent = percent
        order.discount_reason = payload.get("reason", "")


def _order_fired(order: FoldedOrder, payload: dict) -> None:
    fired_at = payload.get("fired_at", "")
    for item in order.unfired_items:
        item.fired_at = fired_at or "fired"

    if order.status in (OrderStatus.DRAFT, OrderStatus.OPEN):
        order.status = OrderStatus.IN_KITCHEN


def _table_assigned(order: FoldedOrder, payload: dict) -> None:
    order.table_id = payload.get("table_id")


def _payment_taken(order: FoldedOrder, payload: dict) -> None:
    order.paid_total += Decimal(str(payload.get("amount", "0")))


def _order_closed(order: FoldedOrder, payload: dict) -> None:
    order.status = OrderStatus.PAID


def _order_voided(order: FoldedOrder, payload: dict) -> None:
    order.status = OrderStatus.CANCELLED


_HANDLERS = {
    EventType.ORDER_OPENED: _order_opened,
    EventType.ITEM_ADDED: _item_added,
    EventType.ITEM_QUANTITY_CHANGED: _quantity_changed,
    EventType.ITEM_VOIDED: _item_voided,
    EventType.ITEM_NOTE_SET: _note_set,
    EventType.DISCOUNT_APPLIED: _discount_applied,
    EventType.ORDER_FIRED: _order_fired,
    EventType.TABLE_ASSIGNED: _table_assigned,
    EventType.PAYMENT_TAKEN: _payment_taken,
    EventType.ORDER_CLOSED: _order_closed,
    EventType.ORDER_VOIDED: _order_voided,
}
