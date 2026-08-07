"""
Routing a fired order to the kitchen, and moving tickets through their states.

Two properties matter here:
  * Firing is idempotent per item — an item already sent is never sent twice,
    however many times the client retries.
  * The order's status follows its tickets, not the other way round.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.configuration import resolver
from apps.configuration.resolver import ScopeContext
from apps.core.exceptions import AppError, InvalidStateTransition
from apps.orders.models import ItemStatus, Order, OrderStatus

from .models import ALLOWED_TRANSITIONS, KitchenTicket, TicketLine, TicketStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoutingResult:
    tickets: list[KitchenTicket]
    unrouted: list[str]
    """Item names with no station configured — reported, never silently dropped."""


@transaction.atomic
def route_order(order: Order, *, user=None) -> RoutingResult:
    """
    Create one ticket per station for the items not yet sent.

    Items with no station are REPORTED rather than skipped quietly. A drink that
    never reaches a station is a drink nobody makes, and the cashier needs to
    know that now — not when the customer asks where it is.
    """
    pending = list(
        order.items.filter(status=ItemStatus.ACTIVE, fired_at__isnull=True)
        .select_related("station", "variant")
        .prefetch_related("modifiers")
    )
    if not pending:
        return RoutingResult(tickets=[], unrouted=[])

    by_station: dict[str, list] = {}
    unrouted: list[str] = []

    for item in pending:
        if item.station_id is None:
            unrouted.append(item.name_snapshot)
            continue
        by_station.setdefault(item.station_id, []).append(item)

    tickets: list[KitchenTicket] = []
    now = timezone.now()

    for items in by_station.values():
        station = items[0].station
        ticket = KitchenTicket.objects.create(
            order=order,
            station=station,
            branch=order.branch,
            ticket_number=_next_ticket_number(order.branch),
            status=TicketStatus.ACCEPTED if station.auto_accept else TicketStatus.NEW,
            accepted_at=now if station.auto_accept else None,
            created_by=user,
        )
        for item in items:
            TicketLine.objects.create(
                ticket=ticket,
                order_item=item,
                name_snapshot=item.name_snapshot,
                quantity=item.quantity,
                modifiers_snapshot=[m.name_snapshot for m in item.modifiers.all()],
                note=item.note,
                created_by=user,
            )
        tickets.append(ticket)

    # Marking items fired is what makes a repeated fire idempotent.
    order.items.filter(id__in=[item.id for item in pending]).update(fired_at=now)

    if unrouted:
        logger.warning(
            "Fired items with no kitchen station",
            extra={"order": order.local_number, "items": unrouted},
        )

    for ticket in tickets:
        broadcast_ticket(ticket, event="ticket.created")

    return RoutingResult(tickets=tickets, unrouted=unrouted)


def _next_ticket_number(branch) -> int:
    """
    Per branch per day, starting at 1.

    Small numbers because staff read them aloud across a noisy kitchen —
    "ninety-four" works, a UUID does not.
    """
    today = timezone.now().date()
    highest = KitchenTicket.objects.filter(branch=branch, created_at__date=today).aggregate(
        highest=Max("ticket_number")
    )["highest"]
    return (highest or 0) + 1


@transaction.atomic
def transition(ticket: KitchenTicket, target: str, *, user=None) -> KitchenTicket:
    """Move a ticket through its lifecycle, timestamping as it goes."""
    locked = (
        KitchenTicket.objects.select_for_update()
        .select_related("station", "order")
        .get(pk=ticket.pk)
    )

    if target not in ALLOWED_TRANSITIONS.get(locked.status, set()):
        raise InvalidStateTransition(
            f"لا يمكن الانتقال من {locked.status} إلى {target}",
            extra={
                "current": locked.status,
                "target": target,
                "allowed": sorted(ALLOWED_TRANSITIONS.get(locked.status, [])),
            },
        )

    now = timezone.now()
    fields = ["status", "updated_at"]
    locked.status = target

    if target == TicketStatus.ACCEPTED:
        locked.accepted_at = now
        fields.append("accepted_at")
    elif target == TicketStatus.PREPARING:
        locked.started_at = locked.started_at or now
        # A recall clears readiness: the ticket is genuinely in progress again.
        locked.ready_at = None
        locked.prep_seconds = None
        fields += ["started_at", "ready_at", "prep_seconds"]
    elif target == TicketStatus.READY:
        locked.ready_at = now
        locked.prep_seconds = int((now - locked.created_at).total_seconds())
        fields += ["ready_at", "prep_seconds"]
    elif target == TicketStatus.SERVED:
        locked.served_at = now
        fields.append("served_at")

    locked.save(update_fields=fields)
    _sync_order_status(locked.order)
    broadcast_ticket(locked, event="ticket.updated")

    return locked


def recall(ticket: KitchenTicket, *, user=None) -> KitchenTicket:
    """
    Bring a served ticket back — a customer sent something back, or it was
    marked served by mistake. Bounded by `kitchen.allow_recall_minutes` so it
    cannot be used to quietly rewrite yesterday.
    """
    context = ScopeContext(organization_id=ticket.order.organization_id, branch_id=ticket.branch_id)
    window = resolver.get("kitchen.allow_recall_minutes", context)
    reference = ticket.served_at or ticket.ready_at

    if reference and timezone.now() - reference > timedelta(minutes=window):
        raise AppError(
            f"انتهت مهلة استرجاع التذكرة ({window} دقيقة)",
            code="RECALL_WINDOW_ELAPSED",
        )
    return transition(ticket, TicketStatus.PREPARING, user=user)


def _sync_order_status(order: Order) -> None:
    """
    The order follows its tickets.

    READY only when EVERY ticket is ready — a customer whose coffee is done but
    whose cake is not has not had their order completed.
    """
    tickets = list(order.kitchen_tickets.exclude(status=TicketStatus.CANCELLED))
    if not tickets:
        return
    if order.status in {OrderStatus.PAID, OrderStatus.CANCELLED, OrderStatus.REFUNDED}:
        return

    done = {TicketStatus.READY, TicketStatus.SERVED}
    target = None

    if all(ticket.status == TicketStatus.SERVED for ticket in tickets):
        target = OrderStatus.SERVED
    elif all(ticket.status in done for ticket in tickets):
        target = OrderStatus.READY
    elif any(ticket.is_open for ticket in tickets):
        target = OrderStatus.IN_KITCHEN

    if target and target != order.status:
        from apps.orders import state

        if state.can_transition(order.status, target):
            order.status = target
            order.save(update_fields=["status", "updated_at"])


# ── real-time ────────────────────────────────────────────────────────────────


def broadcast_ticket(ticket: KitchenTicket, *, event: str) -> None:
    """
    Push a ticket to the kitchen display.

    Best-effort by design: WebSockets are an optimization, never a correctness
    requirement. Every client also polls its REST fallback, so a dropped socket
    degrades the kitchen's latency rather than losing its tickets.
    """
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        layer = get_channel_layer()
        if layer is None:
            return

        payload = {
            "type": "kitchen.event",
            "event": event,
            "ticket": serialize_ticket(ticket),
        }
        async_to_sync(layer.group_send)(f"branch.{ticket.branch_id}.kitchen", payload)
        async_to_sync(layer.group_send)(
            f"branch.{ticket.branch_id}.station.{ticket.station_id}", payload
        )
        if event == "ticket.updated" and ticket.status == TicketStatus.READY:
            # The POS wants to know the moment something can be collected.
            async_to_sync(layer.group_send)(f"branch.{ticket.branch_id}.pos", payload)

    except Exception:
        logger.warning("Kitchen broadcast failed", exc_info=True)


def serialize_ticket(ticket: KitchenTicket) -> dict:
    return {
        "id": str(ticket.id),
        "ticket_number": ticket.ticket_number,
        "order_id": str(ticket.order_id),
        "order_number": ticket.order.local_number,
        "station_id": str(ticket.station_id),
        "station_name": ticket.station.name_ar,
        "status": ticket.status,
        "created_at": ticket.created_at.isoformat(),
        "elapsed_seconds": ticket.elapsed_seconds(),
        "is_late": ticket.is_late(),
        "target_minutes": ticket.station.target_prep_minutes,
        "table": (
            ticket.order.table_session.table.number if ticket.order.table_session_id else None
        ),
        "order_type": ticket.order.order_type,
        "lines": [
            {
                "id": str(line.id),
                "name": line.name_snapshot,
                "quantity": str(line.quantity),
                "modifiers": line.modifiers_snapshot,
                "note": line.note,
                "ready_at": line.ready_at.isoformat() if line.ready_at else None,
            }
            for line in ticket.lines.all()
        ],
    }


# ── reporting ────────────────────────────────────────────────────────────────


def performance(branch, *, since=None) -> dict:
    """
    Prep times per station.

    Costs nothing to capture — the timestamps are written at each transition
    anyway — and it is what tells an owner whether the coffee bar needs a second
    person at 8pm.
    """
    tickets = KitchenTicket.objects.filter(
        branch=branch, prep_seconds__isnull=False
    ).select_related("station")
    if since:
        tickets = tickets.filter(created_at__gte=since)

    by_station: dict[str, dict] = {}
    for ticket in tickets:
        bucket = by_station.setdefault(
            ticket.station.name_ar,
            {
                "count": 0,
                "total_seconds": 0,
                "late": 0,
                "target_minutes": ticket.station.target_prep_minutes,
            },
        )
        bucket["count"] += 1
        bucket["total_seconds"] += ticket.prep_seconds
        if ticket.prep_seconds > ticket.station.target_prep_minutes * 60:
            bucket["late"] += 1

    for bucket in by_station.values():
        bucket["average_seconds"] = round(bucket["total_seconds"] / bucket["count"])
        bucket["late_percent"] = round(bucket["late"] / bucket["count"] * 100, 1)

    return by_station
