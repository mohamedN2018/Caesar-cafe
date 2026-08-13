"""Floor API: areas, tables, sessions, and the live board."""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authz.drf import HasPermission, IsAuthenticatedPrincipal, auth_context
from apps.core.exceptions import AppError, NotFoundError
from apps.core.viewsets import BranchScopedViewSet

from . import services
from .models import Area, Table, TableSession, TableStatus
from .serializers import (
    AreaSerializer,
    FloorStatusSerializer,
    MergeSerializer,
    OpenSessionSerializer,
    TableSerializer,
    TableSessionSerializer,
    TransferSerializer,
)


def _acting_user(request: Request):
    principal = auth_context(request)
    if principal.user_id is None:
        return None
    from apps.accounts.models import User

    return User.objects.filter(id=principal.user_id).first()


def _tables(request: Request):
    return Table.objects.filter(area__branch_id=auth_context(request).require_branch())


class AreaViewSet(BranchScopedViewSet):
    queryset = Area.all_objects.all()
    serializer_class = AreaSerializer
    required_permissions = {"GET": "floor.view", "default": "branch.manage_tables"}
    pagination_class = None


class TableViewSet(BranchScopedViewSet):
    """`pos_x`/`pos_y` come from the Web Admin's drag-and-drop canvas."""

    queryset = Table.objects.select_related("area")
    serializer_class = TableSerializer
    required_permissions = {"GET": "floor.view", "default": "branch.manage_tables"}
    filterset_fields = ["area", "status", "is_active"]
    pagination_class = None

    def get_queryset(self):
        return self.queryset.filter(area__branch_id=auth_context(self.request).branch_id)

    def perform_create(self, serializer) -> None:
        serializer.save(created_by_id=auth_context(self.request).user_id)


class FloorStatusView(APIView):
    """
    The live board.

    Honours `floor.waiter_sees_only_own_tables`: a waiter sees their own
    section, which is the point of the setting rather than a UI nicety.
    """

    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "floor.view"

    @extend_schema(summary="Live floor status", responses={200: FloorStatusSerializer(many=True)})
    def get(self, request: Request) -> Response:
        from apps.configuration import resolver
        from apps.configuration.resolver import ScopeContext
        from apps.orders import state as order_state

        principal = auth_context(request)
        context = ScopeContext(
            organization_id=principal.organization_id, branch_id=principal.branch_id
        )
        own_only = resolver.get("floor.waiter_sees_only_own_tables", context)
        restrict = own_only and not principal.has("payments.take")

        rows = []
        tables = (
            _tables(request)
            .filter(is_active=True)
            .select_related("area")
            .prefetch_related("sessions__orders", "sessions__waiter")
        )

        for table in tables:
            session = table.open_session
            if restrict and session is not None and session.waiter_id != principal.user_id:
                continue

            orders = (
                [o for o in session.orders.all() if o.status in order_state.ACTIVE]
                if session
                else []
            )
            rows.append(
                {
                    "table_id": table.id,
                    "number": table.number,
                    "area": table.area.name_ar,
                    "seats": table.seats,
                    # The party, not the furniture. Two people at a six-top is a
                    # table that looks busy on a board and is mostly empty in the
                    # room, and only one of those numbers seats a walk-in.
                    "seated_count": min(session.guest_count, table.seats) if session else 0,
                    "status": table.status,
                    "pos_x": table.pos_x,
                    "pos_y": table.pos_y,
                    "shape": table.shape,
                    "span_x": table.span_x,
                    "span_y": table.span_y,
                    "rotation": table.rotation,
                    "session_id": session.id if session else None,
                    "guest_count": session.guest_count if session else None,
                    "opened_at": session.opened_at if session else None,
                    "seated_minutes": (
                        int((timezone.now() - session.opened_at).total_seconds() // 60)
                        if session
                        else None
                    ),
                    "order_count": len(orders),
                    "total_due": sum(
                        (o.grand_total - o.paid_total for o in orders), Decimal("0.00")
                    ),
                    "waiter": (session.waiter.full_name_ar if session and session.waiter else None),
                }
            )

        return Response(FloorStatusSerializer(rows, many=True).data)


class SessionView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permissions = {"GET": "floor.view", "POST": "floor.open_table"}

    @extend_schema(
        summary="Open a table session",
        request=OpenSessionSerializer,
        responses={201: TableSessionSerializer},
    )
    @transaction.atomic
    def post(self, request: Request) -> Response:
        serializer = OpenSessionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        table = _tables(request).select_for_update().filter(id=data["table"]).first()
        if table is None:
            raise NotFoundError("الطاولة غير موجودة", code="TABLE_NOT_FOUND")
        if table.open_session is not None:
            raise AppError(
                "الطاولة مشغولة بالفعل",
                code="TABLE_OCCUPIED",
                extra={"session_id": str(table.open_session.id)},
            )

        session = TableSession.objects.create(
            table=table,
            guest_count=data["guest_count"],
            opened_by=_acting_user(request),
            waiter=_acting_user(request),
            created_by=_acting_user(request),
        )
        table.status = TableStatus.OCCUPIED
        table.save(update_fields=["status", "updated_at"])

        return Response(TableSessionSerializer(session).data, status=status.HTTP_201_CREATED)


class SessionCloseView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "floor.open_table"

    @extend_schema(
        summary="Close a table session", request=None, responses={200: TableSessionSerializer}
    )
    @transaction.atomic
    def post(self, request: Request, pk) -> Response:
        from apps.configuration import resolver
        from apps.configuration.resolver import ScopeContext
        from apps.orders import state as order_state

        principal = auth_context(request)
        session = (
            TableSession.objects.filter(
                id=pk, table__area__branch_id=principal.require_branch(), closed_at__isnull=True
            )
            .select_related("table")
            .first()
        )
        if session is None:
            raise NotFoundError("الجلسة غير موجودة", code="SESSION_NOT_FOUND")

        unsettled = session.orders.filter(status__in=order_state.ACTIVE).count()
        if unsettled:
            raise AppError(
                f"لا يمكن إغلاق الجلسة وبها {unsettled} طلب غير محصّل",
                code="UNSETTLED_ORDERS",
                extra={"unsettled": unsettled},
            )

        session.closed_at = timezone.now()
        session.save(update_fields=["closed_at", "updated_at"])

        context = ScopeContext(
            organization_id=principal.organization_id, branch_id=principal.branch_id
        )
        table = session.table
        table.status = (
            TableStatus.CLEANING
            if resolver.get("floor.auto_cleaning_status", context)
            else TableStatus.AVAILABLE
        )
        table.save(update_fields=["status", "updated_at"])

        return Response(TableSessionSerializer(session).data)


class SessionTransferView(APIView):
    """
    Move a session to another table.

    The SESSION moves, carrying every order with it — which is why the session
    exists as a separate concept from the order.
    """

    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "floor.transfer"

    @extend_schema(
        summary="Transfer a session to another table",
        request=TransferSerializer,
        responses={200: TableSessionSerializer},
    )
    @transaction.atomic
    def post(self, request: Request, pk) -> Response:
        serializer = TransferSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        principal = auth_context(request)
        session = (
            TableSession.objects.filter(
                id=pk, table__area__branch_id=principal.require_branch(), closed_at__isnull=True
            )
            .select_related("table")
            .first()
        )
        if session is None:
            raise NotFoundError("الجلسة غير موجودة", code="SESSION_NOT_FOUND")

        target = (
            _tables(request)
            .select_for_update()
            .filter(id=serializer.validated_data["target_table"])
            .first()
        )
        if target is None:
            raise NotFoundError("الطاولة الهدف غير موجودة", code="TABLE_NOT_FOUND")
        if target.id == session.table_id:
            raise AppError("الطاولة الهدف هي نفس الطاولة الحالية", code="SAME_TABLE")
        if target.open_session is not None:
            raise AppError("الطاولة الهدف مشغولة", code="TABLE_OCCUPIED")

        source = session.table
        session.table = target
        session.save(update_fields=["table", "updated_at"])

        target.status = TableStatus.OCCUPIED
        target.save(update_fields=["status", "updated_at"])
        source.status = TableStatus.AVAILABLE
        source.save(update_fields=["status", "updated_at"])

        return Response(TableSessionSerializer(session).data)


class SessionMergeView(APIView):
    """
    Fold one open session into another: one party, one bill, one table freed.

    Separately permissioned from `floor.transfer` because they are different
    acts. Transferring moves a party to a different table; merging combines two
    bills, and afterwards there is one payment where there would have been two.
    """

    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "floor.merge"

    @extend_schema(
        summary="Merge this session into another",
        request=MergeSerializer,
        responses={200: TableSessionSerializer},
    )
    def post(self, request: Request, pk) -> Response:
        serializer = MergeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        source = _open_session(request, pk)
        target = _open_session(request, serializer.validated_data["into"])

        merged = services.merge_sessions(source=source, target=target, user=_acting_user(request))
        return Response(TableSessionSerializer(merged).data)


def _open_session(request: Request, session_id) -> TableSession:
    session = (
        TableSession.objects.filter(
            id=session_id,
            table__area__branch_id=auth_context(request).require_branch(),
            closed_at__isnull=True,
        )
        .select_related("table")
        .first()
    )
    if session is None:
        raise NotFoundError("الجلسة غير موجودة", code="SESSION_NOT_FOUND")
    return session
