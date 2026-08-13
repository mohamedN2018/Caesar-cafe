"""
The order state machine (docs/02 §50).

One table, checked in one place, on the server. Terminal states have no outbound
edges — `PAID → OPEN` is unreachable by construction, which is the point.
Reopening a paid order is not an edit; it is a refund followed by a new order,
and it leaves two auditable records instead of one silently altered one.
"""

from __future__ import annotations

from apps.core.exceptions import InvalidStateTransition

from .models import OrderStatus

ALLOWED: dict[str, set[str]] = {
    OrderStatus.DRAFT: {OrderStatus.OPEN, OrderStatus.CANCELLED},
    OrderStatus.OPEN: {OrderStatus.IN_KITCHEN, OrderStatus.PAID, OrderStatus.CANCELLED},
    OrderStatus.IN_KITCHEN: {
        OrderStatus.OPEN,  # more items added after firing
        OrderStatus.READY,
        OrderStatus.PAID,
        OrderStatus.CANCELLED,
    },
    OrderStatus.READY: {OrderStatus.SERVED, OrderStatus.PAID, OrderStatus.CANCELLED},
    OrderStatus.SERVED: {OrderStatus.PAID},
    OrderStatus.PAID: {OrderStatus.REFUNDED},
    OrderStatus.CANCELLED: set(),
    OrderStatus.REFUNDED: set(),
}

#: States in which items may still be added or changed.
MUTABLE = {OrderStatus.DRAFT, OrderStatus.OPEN, OrderStatus.IN_KITCHEN, OrderStatus.READY}

#: States that count as "on the floor right now".
ACTIVE = {OrderStatus.OPEN, OrderStatus.IN_KITCHEN, OrderStatus.READY, OrderStatus.SERVED}

TERMINAL = {OrderStatus.PAID, OrderStatus.CANCELLED, OrderStatus.REFUNDED}


def can_transition(current: str, target: str) -> bool:
    return target in ALLOWED.get(current, set())


def assert_transition(current: str, target: str) -> None:
    """
    Raises 409 with the current state so a client can reconcile rather than
    retry blindly — which matters when the client may have been offline.
    """
    if not can_transition(current, target):
        raise InvalidStateTransition(
            f"لا يمكن الانتقال من {current} إلى {target}",
            extra={
                "current": current,
                "target": target,
                "allowed": sorted(ALLOWED.get(current, [])),
            },
        )


def assert_mutable(order) -> None:
    if order.status not in MUTABLE:
        raise InvalidStateTransition(
            f"لا يمكن تعديل طلب في حالة {order.status}",
            extra={"current": order.status},
        )
