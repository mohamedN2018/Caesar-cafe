"""Inventory API: items, levels, the ledger, and the operations that write to it."""

from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authz.drf import HasPermission, IsAuthenticatedPrincipal, auth_context
from apps.core.exceptions import NotFoundError
from apps.core.viewsets import BranchScopedViewSet

from . import services
from .models import InventoryItem, StockCount, StockLevel, StockMovement
from .serializers import (
    AdjustmentSerializer,
    DriftSerializer,
    InventoryItemSerializer,
    StockCountSerializer,
    StockLevelSerializer,
    StockMovementSerializer,
    WasteSerializer,
)


def _acting_user(request: Request):
    principal = auth_context(request)
    if principal.user_id is None:
        return None
    from apps.accounts.models import User

    return User.objects.filter(id=principal.user_id).first()


class InventoryItemViewSet(BranchScopedViewSet):
    queryset = InventoryItem.all_objects.select_related("base_unit", "level", "default_supplier")
    serializer_class = InventoryItemSerializer
    required_permissions = {
        "GET": "inventory.view",
        "POST": "inventory.adjust",
        "default": "inventory.adjust",
    }
    filterset_fields = ["item_type", "is_active", "default_supplier"]
    search_fields = ["code", "name_ar", "name_en"]
    ordering_fields = ["name_ar", "code", "created_at"]


class StockLevelView(APIView):
    """Current stock. `?low_stock=true` for the reorder list."""

    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "inventory.view"

    @extend_schema(summary="Current stock levels", responses={200: StockLevelSerializer(many=True)})
    def get(self, request: Request) -> Response:
        principal = auth_context(request)
        levels = StockLevel.objects.select_related("item", "item__base_unit").filter(
            item__branch_id=principal.branch_id, item__is_active=True
        )

        if request.query_params.get("low_stock") == "true":
            levels = [level for level in levels if level.is_low]

        return Response(StockLevelSerializer(levels, many=True).data)


class StockMovementView(APIView):
    """The ledger. Read-only by design — movements are written by services."""

    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "inventory.view"

    @extend_schema(summary="Stock ledger", responses={200: StockMovementSerializer(many=True)})
    def get(self, request: Request) -> Response:
        principal = auth_context(request)
        movements = StockMovement.objects.select_related("item", "user").filter(
            branch_id=principal.branch_id
        )

        if item_id := request.query_params.get("item"):
            movements = movements.filter(item_id=item_id)
        if movement_type := request.query_params.get("type"):
            movements = movements.filter(movement_type=movement_type)
        if date_from := request.query_params.get("date_from"):
            movements = movements.filter(occurred_at__gte=date_from)
        if date_to := request.query_params.get("date_to"):
            movements = movements.filter(occurred_at__lte=date_to)

        return Response(StockMovementSerializer(movements[:500], many=True).data)


class AdjustmentView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "inventory.adjust"

    @extend_schema(
        summary="Set stock to an absolute quantity",
        request=AdjustmentSerializer,
        responses={200: StockMovementSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = AdjustmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        item = _get_item(request, data["item"])
        result = services.adjust(
            item=item,
            new_quantity=data["new_quantity"],
            reason=data["reason"],
            user=_acting_user(request),
        )
        if result is None:
            return Response({"detail": "لا يوجد فرق — لم تُسجَّل حركة"})
        return Response(StockMovementSerializer(result.movement).data)


class WasteView(APIView):
    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "inventory.waste"

    @extend_schema(
        summary="Record waste",
        request=WasteSerializer,
        responses={200: StockMovementSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = WasteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        result = services.record_waste(
            item=_get_item(request, data["item"]),
            quantity=data["quantity"],
            reason=data["reason"],
            user=_acting_user(request),
        )
        return Response(StockMovementSerializer(result.movement).data)


class StockCountViewSet(BranchScopedViewSet):
    queryset = StockCount.all_objects.prefetch_related("lines")
    serializer_class = StockCountSerializer
    required_permissions = {"GET": "inventory.view", "default": "inventory.count"}

    @extend_schema(
        summary="Post a count, turning variances into movements",
        request=None,
        responses={200: StockCountSerializer},
    )
    @action(detail=True, methods=["post"], url_path="post")
    def post_count(self, request: Request, pk=None) -> Response:
        count = self.get_object()

        # Posting is separately permissioned: a count is an observation, and
        # turning one into a stock adjustment is a financial decision.
        principal = auth_context(request)
        if not principal.has("inventory.post_count"):
            from apps.core.exceptions import PermissionDeniedError

            raise PermissionDeniedError("يتطلب صلاحية: inventory.post_count")

        services.post_count(count, user=_acting_user(request))
        count.refresh_from_db()
        return Response(StockCountSerializer(count).data)


class ReconciliationView(APIView):
    """
    Replay the ledger and report any drift.

    A clean result proves the projection still matches the ledger; a drift is a
    bug in a write path, not a stock problem.
    """

    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "inventory.view"

    @extend_schema(
        summary="Reconcile levels against the ledger", responses={200: DriftSerializer(many=True)}
    )
    def get(self, request: Request) -> Response:
        from apps.organizations.models import Branch

        principal = auth_context(request)
        branch = Branch.objects.filter(id=principal.branch_id).first()
        drifts = services.reconcile(branch)

        return Response(
            {
                "clean": not drifts,
                "drifts": DriftSerializer(
                    [
                        {
                            "item_code": d.item_code,
                            "item_name": d.item_name,
                            "level_quantity": d.level_quantity,
                            "ledger_quantity": d.ledger_quantity,
                            "difference": d.difference,
                        }
                        for d in drifts
                    ],
                    many=True,
                ).data,
            }
        )


def _get_item(request: Request, item_id) -> InventoryItem:
    principal = auth_context(request)
    item = (
        InventoryItem.all_objects.filter(id=item_id, branch_id=principal.branch_id)
        .select_related("base_unit")
        .first()
    )
    if item is None:
        raise NotFoundError("الصنف غير موجود", code="ITEM_NOT_FOUND")
    return item
