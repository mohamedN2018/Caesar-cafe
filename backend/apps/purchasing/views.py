"""
Purchasing API.

The rule this app exists to enforce shows up directly in the routes: ordering
and receiving are different endpoints with different permissions, because a
purchase order moves no stock and a goods receipt does.

    POST /purchase-orders/            an intention
    POST /purchase-orders/{id}/submit/  still an intention, now committed to
    POST /receipts/{id}/post/         ← the only thing here that touches stock

`purchasing.create_po` and `purchasing.receive` are separate for the same
reason: the person who orders the milk is often not the person who signs for it,
and a system where they must be is a system where one person can invent a
delivery.
"""

from __future__ import annotations

from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authz.drf import HasPermission, IsAuthenticatedPrincipal, auth_context
from apps.core.exceptions import AppError, NotFoundError
from apps.core.viewsets import BranchScopedViewSet

from . import services
from .models import GoodsReceipt, POStatus, PurchaseOrder, PurchaseReturn
from .serializers import (
    GoodsReceiptSerializer,
    PurchaseOrderSerializer,
    PurchaseReturnSerializer,
    ReorderSuggestionSerializer,
    ValuationSerializer,
)


def _acting_user(request: Request):
    principal = auth_context(request)
    if principal.user_id is None:
        return None
    from apps.accounts.models import User

    return User.objects.filter(id=principal.user_id).first()


def _branch(request: Request):
    from apps.organizations.models import Branch

    branch = Branch.objects.filter(id=auth_context(request).require_branch()).first()
    if branch is None:
        raise NotFoundError("الفرع غير موجود", code="BRANCH_NOT_FOUND")
    return branch


class PurchaseOrderViewSet(BranchScopedViewSet):
    queryset = PurchaseOrder.objects.select_related("supplier").prefetch_related(
        "lines__item", "lines__unit"
    )
    serializer_class = PurchaseOrderSerializer
    required_permissions = {"GET": "purchasing.view", "default": "purchasing.create_po"}
    filterset_fields = ["status", "supplier"]
    ordering_fields = ["created_at", "expected_date"]

    def perform_update(self, serializer) -> None:
        """
        A submitted order is not editable.

        Once it has gone to the supplier, changing the quantities here would
        make the paperwork disagree with what was actually ordered — and the
        goods receipt is where a difference is supposed to be recorded.
        """
        if serializer.instance.status != POStatus.DRAFT:
            raise AppError(
                f"لا يمكن تعديل أمر شراء في حالة {serializer.instance.status}",
                code="PO_NOT_EDITABLE",
                status_code=409,
            )
        super().perform_update(serializer)

    def perform_destroy(self, instance) -> None:
        if instance.status != POStatus.DRAFT:
            raise AppError(
                "لا يمكن حذف أمر شراء بعد إرساله — ألغِه بدلاً من ذلك",
                code="PO_NOT_DELETABLE",
                status_code=409,
            )
        instance.delete()

    @extend_schema(
        summary="Submit a purchase order (moves NO stock)",
        request=None,
        responses={200: PurchaseOrderSerializer},
    )
    @action(detail=True, methods=["post"])
    def submit(self, request: Request, pk=None) -> Response:
        order = services.submit_purchase_order(self.get_object(), user=_acting_user(request))
        return Response(PurchaseOrderSerializer(order).data)

    @extend_schema(
        summary="Cancel a purchase order", request=None, responses={200: PurchaseOrderSerializer}
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request: Request, pk=None) -> Response:
        order = self.get_object()
        if order.status in (POStatus.RECEIVED, POStatus.CANCELLED):
            raise AppError(
                f"لا يمكن إلغاء أمر شراء في حالة {order.status}",
                code="INVALID_PO_STATUS",
                status_code=409,
            )
        # Partially received orders CAN be cancelled: the delivered half stays
        # on the shelf and in the ledger, and the outstanding half stops being
        # expected. Refusing would leave the order open forever.
        order.status = POStatus.CANCELLED
        order.updated_by_id = auth_context(request).user_id
        order.save(update_fields=["status", "updated_by", "updated_at"])
        return Response(PurchaseOrderSerializer(order).data)


class GoodsReceiptViewSet(BranchScopedViewSet):
    queryset = GoodsReceipt.objects.select_related("supplier", "purchase_order").prefetch_related(
        "lines__item", "lines__unit"
    )
    serializer_class = GoodsReceiptSerializer
    required_permissions = {"GET": "purchasing.view", "default": "purchasing.receive"}
    filterset_fields = ["supplier", "purchase_order"]
    ordering_fields = ["received_date", "created_at"]

    def perform_update(self, serializer) -> None:
        """
        A posted receipt is frozen.

        Its lines have already become stock movements and a supplier invoice.
        Editing them afterwards would leave the ledger describing a delivery
        that no longer matches the document it came from.
        """
        if serializer.instance.is_posted:
            raise services.AlreadyPosted()
        super().perform_update(serializer)

    def perform_destroy(self, instance) -> None:
        if instance.is_posted:
            raise services.AlreadyPosted()
        instance.delete()

    @extend_schema(
        summary="Post a receipt: stock in, supplier billed, costs refreshed",
        request=None,
        responses={200: GoodsReceiptSerializer},
    )
    @action(detail=True, methods=["post"], url_path="post")
    def post_receipt(self, request: Request, pk=None) -> Response:
        receipt = services.post_receipt(self.get_object(), user=_acting_user(request))
        receipt.refresh_from_db()
        return Response(GoodsReceiptSerializer(receipt).data)


class PurchaseReturnViewSet(BranchScopedViewSet):
    queryset = PurchaseReturn.objects.select_related("supplier", "receipt").prefetch_related(
        "lines__item", "lines__unit"
    )
    serializer_class = PurchaseReturnSerializer
    required_permissions = {"GET": "purchasing.view", "default": "purchasing.receive"}
    filterset_fields = ["supplier"]

    def perform_create(self, serializer) -> None:
        principal = auth_context(self.request)
        serializer.save(
            organization_id=principal.organization_id,
            branch_id=principal.branch_id,
            created_by_id=principal.user_id,
            created_by_user_id=principal.user_id,
        )

    @extend_schema(
        summary="Post a return: stock out, supplier balance down",
        request=None,
        responses={200: PurchaseReturnSerializer},
    )
    @action(detail=True, methods=["post"], url_path="post")
    def post_return(self, request: Request, pk=None) -> Response:
        purchase_return = services.post_return(self.get_object(), user=_acting_user(request))
        purchase_return.refresh_from_db()
        return Response(PurchaseReturnSerializer(purchase_return).data)


class ReorderSuggestionView(APIView):
    """
    What is at or below its reorder level.

    A suggestion, never an order. Turning this into an automatic PO would have
    the system spending money on a number somebody typed once.
    """

    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "purchasing.view"

    @extend_schema(
        summary="Items at or below their reorder level",
        responses={200: ReorderSuggestionSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        suggestions = services.reorder_suggestions(_branch(request))
        return Response(ReorderSuggestionSerializer(suggestions, many=True).data)


class ValuationView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "inventory.view"

    @extend_schema(
        summary="Stock value at weighted-average cost", responses={200: ValuationSerializer}
    )
    def get(self, request: Request) -> Response:
        result = services.valuation(_branch(request))
        return Response(
            ValuationSerializer(
                {
                    "total": result["total"],
                    "by_type": {k: v for k, v in result["by_type"].items()},
                }
            ).data
        )


class OutstandingOrdersView(APIView):
    """
    Submitted orders that have not fully arrived — the "where is my delivery"
    screen, and the one an owner checks before ordering the same thing twice.
    """

    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "purchasing.view"

    @extend_schema(
        summary="Submitted orders not yet fully received",
        responses={200: PurchaseOrderSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        principal = auth_context(request)
        orders = (
            PurchaseOrder.objects.filter(
                branch_id=principal.branch_id,
                status__in=[POStatus.SUBMITTED, POStatus.PARTIAL],
            )
            .select_related("supplier")
            .prefetch_related("lines__item", "lines__unit")
        )

        if request.query_params.get("overdue") == "true":
            orders = orders.filter(expected_date__lt=timezone.localdate())

        return Response(PurchaseOrderSerializer(orders[:200], many=True).data)
