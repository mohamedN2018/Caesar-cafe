"""
Kitchen API.

Also the REST fallback for the display: WebSockets are an optimization, so the
KDS polls this on a slow interval and a dropped socket costs latency, not
tickets.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authz.drf import HasPermission, IsAuthenticatedPrincipal, auth_context
from apps.core.exceptions import NotFoundError
from apps.core.viewsets import BranchScopedViewSet

from . import services
from .models import OPEN_STATUSES, KitchenTicket, Station, TicketStatus
from .serializers import StationSerializer, TicketSerializer, TransitionSerializer


def _acting_user(request: Request):
    principal = auth_context(request)
    if principal.user_id is None:
        return None
    from apps.accounts.models import User

    return User.objects.filter(id=principal.user_id).first()


def _get_ticket(request: Request, pk) -> KitchenTicket:
    principal = auth_context(request)
    ticket = (
        KitchenTicket.objects.filter(id=pk, branch_id=principal.branch_id)
        .select_related("station", "order", "order__table_session__table")
        .prefetch_related("lines")
        .first()
    )
    if ticket is None:
        raise NotFoundError("التذكرة غير موجودة", code="TICKET_NOT_FOUND")
    return ticket


class StationViewSet(BranchScopedViewSet):
    queryset = Station.all_objects.all()
    serializer_class = StationSerializer
    required_permissions = {"GET": "kitchen.view", "default": "kitchen.manage_stations"}
    pagination_class = None


class TicketListView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "kitchen.view"

    @extend_schema(summary="Kitchen tickets", responses={200: TicketSerializer(many=True)})
    def get(self, request: Request) -> Response:
        principal = auth_context(request)
        tickets = (
            KitchenTicket.objects.filter(branch_id=principal.branch_id)
            .select_related("station", "order", "order__table_session__table")
            .prefetch_related("lines")
        )

        if station := request.query_params.get("station"):
            tickets = tickets.filter(station_id=station)
        if status_filter := request.query_params.get("status"):
            tickets = tickets.filter(status=status_filter)
        elif request.query_params.get("all") != "true":
            # The board shows work in progress plus what is waiting to be
            # collected; anything served drops off unless asked for.
            tickets = tickets.filter(status__in=[*OPEN_STATUSES, TicketStatus.READY])

        # Same payload the WebSocket pushes — one shape, one parser on the KDS.
        return Response([services.serialize_ticket(t) for t in tickets[:200]])


class TicketTransitionView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "kitchen.update_status"

    @extend_schema(
        summary="Advance a ticket (accept · start · ready · served)",
        request=TransitionSerializer,
        responses={200: TicketSerializer},
    )
    def post(self, request: Request, pk, action: str) -> Response:
        targets = {
            "accept": TicketStatus.ACCEPTED,
            "start": TicketStatus.PREPARING,
            "ready": TicketStatus.READY,
            "served": TicketStatus.SERVED,
            "cancel": TicketStatus.CANCELLED,
        }
        if action not in targets:
            raise NotFoundError("إجراء غير معروف", code="UNKNOWN_ACTION")

        ticket = services.transition(
            _get_ticket(request, pk), targets[action], user=_acting_user(request)
        )
        return Response(services.serialize_ticket(ticket))


class TicketRecallView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "kitchen.recall"

    @extend_schema(
        summary="Recall a served ticket", request=None, responses={200: TicketSerializer}
    )
    def post(self, request: Request, pk) -> Response:
        ticket = services.recall(_get_ticket(request, pk), user=_acting_user(request))
        return Response(services.serialize_ticket(ticket))


class LineReadyView(APIView):
    """Per-item readiness, for stations that plate in stages."""

    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "kitchen.update_status"

    @extend_schema(
        summary="Mark one ticket line ready", request=None, responses={200: TicketSerializer}
    )
    def post(self, request: Request, pk, line_id) -> Response:
        ticket = _get_ticket(request, pk)
        line = ticket.lines.filter(id=line_id).first()
        if line is None:
            raise NotFoundError("السطر غير موجود", code="LINE_NOT_FOUND")

        line.ready_at = timezone.now()
        line.save(update_fields=["ready_at", "updated_at"])

        # When every line is done the ticket follows, so the kitchen never has
        # to tick the same thing twice.
        if not ticket.lines.filter(ready_at__isnull=True).exists() and ticket.is_open:
            ticket = services.transition(ticket, TicketStatus.READY, user=_acting_user(request))
        else:
            services.broadcast_ticket(ticket, event="ticket.updated")

        return Response(services.serialize_ticket(ticket))


class PerformanceView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "kitchen.view"

    @extend_schema(summary="Prep times by station", responses={200: None})
    def get(self, request: Request) -> Response:
        from apps.organizations.models import Branch

        principal = auth_context(request)
        branch = Branch.objects.filter(id=principal.branch_id).first()
        if branch is None:
            raise NotFoundError("الفرع غير موجود", code="BRANCH_NOT_FOUND")

        days = int(request.query_params.get("days", 7))
        since = timezone.now() - timedelta(days=days)

        return Response({"days": days, "stations": services.performance(branch, since=since)})
