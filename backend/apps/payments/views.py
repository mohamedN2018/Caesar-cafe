"""
Payment API.

Every money-moving endpoint requires an `Idempotency-Key` header. That is not a
nicety: a payment retried after a timeout must charge the customer once, and the
retry semantics of every client depend on it (§51).
"""

from __future__ import annotations

import logging

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authz.drf import HasPermission, IsAuthenticatedPrincipal, RequiresHuman, auth_context
from apps.core.exceptions import AppError, NotFoundError
from apps.core.viewsets import BranchScopedViewSet
from apps.orders.models import Order
from apps.orders.serializers import OrderSerializer

from . import services
from .models import Invoice, Payment, PaymentMethod, Refund
from .serializers import (
    InvoiceSerializer,
    PaymentMethodSerializer,
    PaymentRequestSerializer,
    PaymentSerializer,
    RefundRequestSerializer,
    RefundSerializer,
)

logger = logging.getLogger(__name__)

IDEMPOTENCY_HEADER = OpenApiParameter(
    name="Idempotency-Key",
    location=OpenApiParameter.HEADER,
    required=True,
    description="Client-generated UUID. A replay returns the original result.",
    type=str,
)


def _idempotency_key(request: Request) -> str:
    key = request.headers.get("Idempotency-Key")
    if not key:
        raise AppError(
            "ترويسة Idempotency-Key مطلوبة لهذه العملية",
            code="IDEMPOTENCY_KEY_REQUIRED",
        )
    return key[:64]


def _acting_user(request: Request):
    principal = auth_context(request)
    if principal.user_id is None:
        return None
    from apps.accounts.models import User

    return User.objects.filter(id=principal.user_id).first()


def _get_order(request: Request, order_id) -> Order:
    principal = auth_context(request)
    order = Order.objects.filter(id=order_id, branch_id=principal.branch_id).first()
    if order is None:
        raise NotFoundError("الطلب غير موجود", code="ORDER_NOT_FOUND")
    return order


class PaymentMethodViewSet(BranchScopedViewSet):
    """Admin-managed rows, not an enum — adding InstaPay needs no deployment."""

    queryset = PaymentMethod.all_objects.all()
    serializer_class = PaymentMethodSerializer
    required_permissions = {"GET": "payments.view_all", "default": "branch.edit_settings"}
    pagination_class = None

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.method == "GET":
            return queryset.filter(is_active=True)
        return queryset


class PaymentView(APIView):
    """
    Take money.

    Requires a human principal: a bare device token can drain the outbox at 3am
    but cannot take a payment, because there would be nobody to name in the
    audit log.
    """

    permission_classes = [IsAuthenticatedPrincipal, RequiresHuman, HasPermission]
    # GET is authorized per-query below: reading the payments on ONE order you
    # can already see is `orders.view`; sweeping every payment in the branch is
    # `payments.view_all`. Requiring the broad code for both would either block
    # a cashier from their own till or hand them the whole day's takings.
    required_permissions = {"GET": "", "POST": "payments.take"}

    @extend_schema(summary="List payments", responses={200: PaymentSerializer(many=True)})
    def get(self, request: Request) -> Response:
        from apps.core.exceptions import PermissionDeniedError

        principal = auth_context(request)
        order_id = request.query_params.get("order")

        if order_id:
            if not principal.has("orders.view"):
                raise PermissionDeniedError("ليس لديك صلاحية: orders.view")
        elif not principal.has("payments.view_all"):
            raise PermissionDeniedError("ليس لديك صلاحية: payments.view_all")

        payments = Payment.objects.filter(order__branch_id=principal.branch_id).select_related(
            "method", "order", "received_by"
        )

        if order_id:
            payments = payments.filter(order_id=order_id)
        if shift_id := request.query_params.get("shift"):
            payments = payments.filter(shift_id=shift_id)

        return Response(PaymentSerializer(payments[:200], many=True).data)

    @extend_schema(
        summary="Take a payment",
        parameters=[IDEMPOTENCY_HEADER],
        request=PaymentRequestSerializer,
        responses={201: PaymentSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = PaymentRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        principal = auth_context(request)

        order = _get_order(request, data["order"])
        method = PaymentMethod.objects.filter(
            id=data["method"], branch_id=principal.branch_id, is_active=True
        ).first()
        if method is None:
            raise NotFoundError("طريقة الدفع غير موجودة", code="METHOD_NOT_FOUND")

        # Paying less than the balance IS a split payment — the second tender is
        # simply the rest of it. Charging it to `payments.take` alone would give
        # every role that can settle a bill the ability to leave one half-paid,
        # which is the state a walk-out hides in. A waiter has `payments.take`
        # and not `payments.split`, and docs/05 means that.
        if data["amount"] < order.balance_due and not principal.has("payments.split"):
            from apps.core.exceptions import PermissionDeniedError

            raise PermissionDeniedError("ليس لديك صلاحية: payments.split")

        payment = services.take_payment(
            order=order,
            method=method,
            amount=data["amount"],
            tendered=data.get("tendered"),
            reference=data.get("reference", ""),
            idempotency_key=_idempotency_key(request),
            shift=order.shift,
            user=_acting_user(request),
            device_id=principal.device_id,
        )
        order.refresh_from_db()

        return Response(
            {
                **PaymentSerializer(payment).data,
                "order": OrderSerializer(order).data,
            },
            status=status.HTTP_201_CREATED,
        )


class RefundView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, RequiresHuman, HasPermission]
    required_permissions = {"GET": "payments.view_all", "POST": "orders.refund"}

    @extend_schema(summary="List refunds", responses={200: RefundSerializer(many=True)})
    def get(self, request: Request) -> Response:
        principal = auth_context(request)
        refunds = Refund.objects.filter(order__branch_id=principal.branch_id).select_related(
            "order", "refunded_by", "approved_by"
        )
        return Response(RefundSerializer(refunds[:200], many=True).data)

    @extend_schema(
        summary="Refund a payment",
        parameters=[IDEMPOTENCY_HEADER],
        request=RefundRequestSerializer,
        responses={201: RefundSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = RefundRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        order = _get_order(request, data["order"])
        original = None
        if payment_id := data.get("original_payment"):
            original = Payment.objects.filter(id=payment_id, order=order).first()

        refund = services.refund(
            order=order,
            amount=data["amount"],
            reason=data["reason"],
            original_payment=original,
            idempotency_key=_idempotency_key(request),
            shift=order.shift,
            user=_acting_user(request),
            approved_by=getattr(request, "approval_user", None),
        )
        return Response(RefundSerializer(refund).data, status=status.HTTP_201_CREATED)


class InvoiceListView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "orders.view"

    @extend_schema(summary="List invoices", responses={200: InvoiceSerializer(many=True)})
    def get(self, request: Request) -> Response:
        principal = auth_context(request)
        invoices = Invoice.objects.filter(order__branch_id=principal.branch_id).select_related(
            "order"
        )

        if date_from := request.query_params.get("date_from"):
            invoices = invoices.filter(issued_at__gte=date_from)
        if date_to := request.query_params.get("date_to"):
            invoices = invoices.filter(issued_at__lte=date_to)

        return Response(InvoiceSerializer(invoices[:200], many=True).data)


class InvoiceDetailView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "orders.view"

    @extend_schema(summary="Invoice detail (frozen snapshot)", responses={200: InvoiceSerializer})
    def get(self, request: Request, pk) -> Response:
        principal = auth_context(request)
        invoice = (
            Invoice.objects.filter(id=pk, order__branch_id=principal.branch_id)
            .select_related("order")
            .first()
        )
        if invoice is None:
            raise NotFoundError("الفاتورة غير موجودة", code="INVOICE_NOT_FOUND")
        return Response(InvoiceSerializer(invoice).data)
