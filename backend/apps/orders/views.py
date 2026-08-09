"""
Order API.

`POST /orders/{id}/events/` is the primary mutation path. There is deliberately
no `PATCH /orders/{id}/` that lets a client set a total — totals are computed,
never received.
"""

from __future__ import annotations

import logging

from django.db.models import Count, Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit import services as audit
from apps.authz.approval import consume_approval_token
from apps.authz.drf import HasPermission, IsAuthenticatedPrincipal, RequiresHuman, auth_context
from apps.core.exceptions import AppError, NotFoundError, PermissionDeniedError

from . import services, state
from .models import ItemStatus, Order
from .serializers import (
    ApplyResultSerializer,
    EventBatchSerializer,
    OpenOrderSerializer,
    OrderEventSerializer,
    OrderSerializer,
    OrderSummarySerializer,
    VoidOrderSerializer,
)

logger = logging.getLogger(__name__)


def _acting_user(request: Request):
    principal = auth_context(request)
    if principal.user_id is None:
        return None
    from apps.accounts.models import User

    return User.objects.filter(id=principal.user_id).first()


def _branch(request: Request):
    from apps.organizations.models import Branch

    principal = auth_context(request)
    branch = (
        Branch.objects.filter(id=principal.require_branch()).select_related("organization").first()
    )
    if branch is None:
        raise AppError("يجب اختيار الفرع أولاً", code="BRANCH_REQUIRED")
    return branch


def _get_order(request: Request, pk) -> Order:
    principal = auth_context(request)
    order = (
        Order.objects.filter(id=pk, branch_id=principal.branch_id)
        .select_related("table_session__table", "opened_by")
        .prefetch_related("items__modifiers")
        .first()
    )
    if order is None:
        raise NotFoundError("الطلب غير موجود", code="ORDER_NOT_FOUND")
    return order


class OrderListView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permissions = {"GET": "orders.view", "POST": "orders.create"}

    @extend_schema(summary="List orders", responses={200: OrderSummarySerializer(many=True)})
    def get(self, request: Request) -> Response:
        principal = auth_context(request)
        orders = Order.objects.filter(branch_id=principal.branch_id).select_related(
            "table_session__table"
        )

        if request.query_params.get("open") == "true":
            orders = orders.filter(status__in=state.ACTIVE)
        if status_filter := request.query_params.get("status"):
            orders = orders.filter(status=status_filter)
        if shift_id := request.query_params.get("shift"):
            orders = orders.filter(shift_id=shift_id)
        if table_id := request.query_params.get("table"):
            orders = orders.filter(table_session__table_id=table_id)
        if date_from := request.query_params.get("date_from"):
            orders = orders.filter(opened_at__gte=date_from)
        if date_to := request.query_params.get("date_to"):
            orders = orders.filter(opened_at__lte=date_to)

        orders = orders.annotate(
            item_count=Count("items", filter=Q(items__status=ItemStatus.ACTIVE))
        )
        return Response(OrderSummarySerializer(orders[:200], many=True).data)

    @extend_schema(
        summary="Open an order",
        request=OpenOrderSerializer,
        responses={201: OrderSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = OpenOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        principal = auth_context(request)

        table_session = None
        if session_id := data.get("table_session"):
            from apps.floor.models import TableSession

            table_session = TableSession.objects.filter(
                id=session_id, table__area__branch_id=principal.require_branch()
            ).first()
            if table_session is None:
                raise NotFoundError("الجلسة غير موجودة", code="SESSION_NOT_FOUND")

        shift = _resolve_shift(request, data.get("shift"))

        order = services.open_order(
            branch=_branch(request),
            order_type=data["order_type"],
            order_id=data.get("order_id"),
            table_session=table_session,
            shift=shift,
            device_id=principal.device_id,
            user=_acting_user(request),
        )
        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)


def _resolve_shift(request: Request, shift_id):
    """
    Attach the order to a shift, enforcing `shifts.required_to_sell`.

    Without a shift, a sale belongs to nobody and the cash count at close has
    nothing to reconcile against.
    """
    from apps.configuration import resolver
    from apps.configuration.resolver import ScopeContext
    from apps.shifts.models import Shift, ShiftStatus

    principal = auth_context(request)
    shift = None

    if shift_id:
        shift = Shift.objects.filter(id=shift_id, branch_id=principal.branch_id).first()
    elif principal.device_id:
        shift = Shift.objects.filter(device_id=principal.device_id, status=ShiftStatus.OPEN).first()

    context = ScopeContext(organization_id=principal.organization_id, branch_id=principal.branch_id)
    if shift is None and resolver.get("shifts.required_to_sell", context):
        raise AppError("يجب فتح وردية قبل البيع", code="SHIFT_REQUIRED")
    return shift


class OrderDetailView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "orders.view"

    @extend_schema(summary="Order detail", responses={200: OrderSerializer})
    def get(self, request: Request, pk) -> Response:
        return Response(OrderSerializer(_get_order(request, pk)).data)


class OrderEventView(APIView):
    """
    Append events to an order — the primary mutation path.

    Events already recorded are reported as `skipped`, not rejected, so a
    Desktop whose push timed out can retry the whole batch safely.
    """

    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permissions = {"GET": "orders.view", "POST": "orders.edit_items"}

    @extend_schema(summary="Order event stream", responses={200: OrderEventSerializer(many=True)})
    def get(self, request: Request, pk) -> Response:
        order = _get_order(request, pk)
        events = order.events.select_related("actor", "approved_by").order_by("sequence")
        return Response(OrderEventSerializer(events, many=True).data)

    @extend_schema(
        summary="Append order events",
        request=EventBatchSerializer,
        responses={200: ApplyResultSerializer},
    )
    def post(self, request: Request, pk) -> Response:
        serializer = EventBatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        order = _get_order(request, pk)
        principal = auth_context(request)
        self._authorize(request, order, serializer.validated_data["events"])

        result = services.apply_events(
            order,
            serializer.validated_data["events"],
            actor=_acting_user(request),
            device_id=principal.device_id,
            approval=getattr(request, "approval_user", None),
        )
        return Response(
            {
                "applied": result.applied,
                "skipped": result.skipped,
                "order": OrderSerializer(result.order).data,
            }
        )

    @staticmethod
    def _authorize(request: Request, order: Order, events: list[dict]) -> None:
        """
        Per-event permission checks the ViewSet-level code cannot express.

        `orders.edit_items` covers adding a line. Discounting and voiding are
        separate capabilities because they are separate risks.
        """
        from apps.configuration.resolver import ScopeContext
        from apps.orders.models import EventType

        principal = auth_context(request)
        context = ScopeContext(
            organization_id=principal.organization_id, branch_id=principal.branch_id
        )

        for event in events:
            event_type = event.get("type")

            if event_type == EventType.DISCOUNT_APPLIED:
                if not principal.has("orders.discount"):
                    raise PermissionDeniedError("ليس لديك صلاحية: orders.discount")
                _assert_within_discount_limit(principal, context, event)

            elif event_type == EventType.ITEM_PRICE_OVERRIDDEN:
                # No ceiling to check, unlike a discount. There is no "10% of
                # arbitrary" — either somebody may set a price or they may not,
                # which is why it is its own permission and not a generous
                # discount limit.
                _assert_may_change_price(request, principal, order)

            elif event_type == EventType.ITEM_VOIDED:
                if not principal.has("orders.void_item"):
                    raise PermissionDeniedError("ليس لديك صلاحية: orders.void_item")
                if services.requires_void_approval(order) and not principal.has(
                    "orders.void_after_fire"
                ):
                    raise PermissionDeniedError(
                        "انتهت مهلة الإلغاء — يتطلب موافقة مدير.",
                        code="VOID_REQUIRES_APPROVAL",
                    )

            elif event_type == EventType.ORDER_FIRED:
                _assert_can_fire(principal, context)


def _assert_may_change_price(request, principal, order) -> None:
    """
    `orders.change_price` is a step-up permission by design.

    Not even a branch manager holds it in their role — the catalogue lists it as
    a deliberate absence. Setting an arbitrary price is the shortest path from
    the till to the drawer, so it is meant to be a decision somebody stands over
    and approves, not a button one person quietly has all shift.

    The route-level `HasPermission` cannot do this for us: this endpoint takes a
    BATCH, most events in it need only `orders.edit_items`, and gating the whole
    route on the rarest permission would stop ordinary selling.
    """
    if principal.has("orders.change_price"):
        return

    token = request.headers.get("X-Approval-Token")
    granted = (
        consume_approval_token(token, permission="orders.change_price", target=str(order.id))
        if token
        else None
    )
    if granted is None:
        raise PermissionDeniedError("ليس لديك صلاحية: orders.change_price")

    # Both identities end up on the event: who rang it, and who stood over it.
    from apps.accounts.models import User

    request.approval_user = User.objects.filter(id=granted.approver_id).first()


def _assert_within_discount_limit(principal, context, event) -> None:
    from decimal import Decimal

    from apps.authz.services import role_limit
    from apps.configuration import resolver

    if principal.has("orders.discount_unlimited"):
        return

    requested = Decimal(str(event.get("payload", {}).get("percent", 0)))
    ceiling = Decimal(
        str(
            role_limit(
                principal.user_id,
                context.branch_id,
                "discounts.max_percent",
                resolver.get("discounts.max_percent", context),
            )
        )
    )
    if requested > ceiling:
        raise PermissionDeniedError(
            f"الحد الأقصى للخصم {ceiling}% — يتطلب موافقة مدير.",
            code="DISCOUNT_EXCEEDS_LIMIT",
        )


def _assert_can_fire(principal, context) -> None:
    """Honours `floor.waiter_can_fire_to_kitchen` (the Q2 service model)."""
    from apps.configuration import resolver

    if principal.has("kitchen.update_status") or principal.has("orders.create"):
        if resolver.get("floor.waiter_can_fire_to_kitchen", context):
            return
        if not principal.has("payments.take"):
            raise PermissionDeniedError(
                "الإرسال للمطبخ من الكاشير فقط في الإعداد الحالي.",
                code="WAITER_CANNOT_FIRE",
            )


class OrderVoidView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, RequiresHuman, HasPermission]
    required_permission = "orders.void_order"

    @extend_schema(
        summary="Void an order",
        request=VoidOrderSerializer,
        responses={200: OrderSerializer},
    )
    def post(self, request: Request, pk) -> Response:
        serializer = VoidOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        order = _get_order(request, pk)
        services.void_order(
            order,
            reason=serializer.validated_data["reason"],
            actor=_acting_user(request),
            approval=getattr(request, "approval_user", None),
        )
        order.refresh_from_db()
        return Response(OrderSerializer(order).data)


class OrderReceiptView(APIView):
    """
    The receipt as structured data.

    Rendered identically to a thermal printer, a PDF or a preview pane — so
    what the cashier sees is what the customer receives.

    Reading the document is `orders.view`. Asking for a **duplicate copy** of an
    already-issued invoice is `orders.reprint`, and it writes an audit row.
    Reprinting is a known loss-prevention concern — a second copy of a paid
    receipt is the paperwork a refund fraud needs — so the matrix in docs/05
    separates the two, and this is where that separation is enforced.
    """

    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "orders.view"

    @extend_schema(
        summary="Receipt document",
        parameters=[
            OpenApiParameter(
                "reprint",
                OpenApiTypes.BOOL,
                description=(
                    "Request a duplicate of an already-issued invoice. "
                    "Requires `orders.reprint` and is audited."
                ),
            )
        ],
        responses={200: None},
    )
    def get(self, request: Request, pk) -> Response:
        from apps.payments.services import build_receipt

        order = _get_order(request, pk)
        invoice = getattr(order, "invoice", None)
        is_reprint = request.query_params.get("reprint") == "true"

        if is_reprint:
            principal = auth_context(request)
            if not principal.has("orders.reprint"):
                raise PermissionDeniedError("ليس لديك صلاحية: orders.reprint")
            if invoice is None:
                raise AppError(
                    "لا توجد فاتورة نهائية لإعادة طباعتها",
                    code="NO_FINAL_INVOICE",
                    status_code=409,
                )
            audit.record(
                "order.receipt_reprinted",
                obj=order,
                object_label=invoice.serial,
                detail={"serial": invoice.serial},
            )

        # A settled order returns its FROZEN snapshot, never a live rebuild:
        # that is what makes a reprint byte-identical years later.
        if invoice is not None:
            return Response(
                {
                    **invoice.snapshot,
                    "serial": invoice.serial,
                    "is_final": True,
                    "is_reprint": is_reprint,
                }
            )

        return Response({**build_receipt(order), "is_final": False, "is_reprint": False})
