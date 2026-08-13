"""
Event types and the local state machine.

Mirrors `apps.orders.models.EventType` and `apps.orders.state`. The duplication
is deliberate and bounded: these are two short constant tables, and vendoring the
whole Django module to get them would drag the ORM into the client.

What is NOT duplicated is the arithmetic. That lives in `vendored/money.py`,
copied byte-for-byte, because a second implementation of a total is how a server
and a client start quietly disagreeing about a number.
"""

from __future__ import annotations


class EventType:
    ORDER_OPENED = "ORDER_OPENED"
    ITEM_ADDED = "ITEM_ADDED"
    ITEM_QUANTITY_CHANGED = "ITEM_QUANTITY_CHANGED"
    ITEM_VOIDED = "ITEM_VOIDED"
    ITEM_NOTE_SET = "ITEM_NOTE_SET"
    ITEM_PRICE_OVERRIDDEN = "ITEM_PRICE_OVERRIDDEN"
    DISCOUNT_APPLIED = "DISCOUNT_APPLIED"
    ORDER_FIRED = "ORDER_FIRED"
    TABLE_ASSIGNED = "TABLE_ASSIGNED"
    CUSTOMER_ASSIGNED = "CUSTOMER_ASSIGNED"
    PAYMENT_TAKEN = "PAYMENT_TAKEN"
    ORDER_CLOSED = "ORDER_CLOSED"
    ORDER_VOIDED = "ORDER_VOIDED"


class OrderStatus:
    DRAFT = "DRAFT"
    OPEN = "OPEN"
    IN_KITCHEN = "IN_KITCHEN"
    READY = "READY"
    SERVED = "SERVED"
    PAID = "PAID"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"


class ItemStatus:
    ACTIVE = "ACTIVE"
    VOIDED = "VOIDED"


#: `PAID → OPEN` is absent by construction. Reopening a paid order is a refund
#: plus a new order — two auditable records instead of one silently altered one.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    OrderStatus.DRAFT: {OrderStatus.OPEN, OrderStatus.CANCELLED},
    OrderStatus.OPEN: {OrderStatus.IN_KITCHEN, OrderStatus.PAID, OrderStatus.CANCELLED},
    OrderStatus.IN_KITCHEN: {OrderStatus.READY, OrderStatus.PAID, OrderStatus.CANCELLED},
    OrderStatus.READY: {OrderStatus.SERVED, OrderStatus.PAID, OrderStatus.CANCELLED},
    OrderStatus.SERVED: {OrderStatus.PAID, OrderStatus.CANCELLED},
    OrderStatus.PAID: {OrderStatus.REFUNDED},
    OrderStatus.CANCELLED: set(),
    OrderStatus.REFUNDED: set(),
}

#: An order in one of these takes no further edits. The UI greys the buttons;
#: the service refuses regardless, because a greyed button is not a rule.
TERMINAL = (OrderStatus.PAID, OrderStatus.CANCELLED, OrderStatus.REFUNDED)

#: What the floor board shows.
OPEN_STATUSES = (
    OrderStatus.OPEN,
    OrderStatus.IN_KITCHEN,
    OrderStatus.READY,
    OrderStatus.SERVED,
)


def can_transition(current: str, target: str) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, set())


class InvalidTransition(RuntimeError):
    """Refused locally, with the same rule the server applies on sync."""

    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"لا يمكن الانتقال من {current} إلى {target}")
        self.current = current
        self.target = target
