"""
Recipe API.

A recipe is the bridge between the catalog and the ledger: it is what turns a
sold cappuccino into 18g of beans and 150ml of milk leaving the shelf. Without
one, inventory is a list somebody updates by hand and then stops trusting.

The cost endpoint is the reason this is worth a screen rather than a fixture.
Ingredient costs move every time a supplier delivers, so a margin typed in once
is a margin that is wrong by the end of the month — and `refresh_costs_for_item`
already re-costs every affected recipe when goods are received. This just lets
somebody look.
"""

from __future__ import annotations

from decimal import Decimal

from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authz.drf import HasPermission, IsAuthenticatedPrincipal, auth_context
from apps.core.exceptions import NotFoundError
from apps.core.viewsets import BranchScopedViewSet

from . import services
from .models import Recipe
from .serializers import RecipeCostSerializer, RecipeSerializer


class RecipeViewSet(BranchScopedViewSet):
    queryset = Recipe.objects.select_related("variant", "variant__product").prefetch_related(
        "lines__item", "lines__unit"
    )
    serializer_class = RecipeSerializer
    required_permissions = {"GET": "catalog.view", "default": "catalog.manage_recipes"}
    filterset_fields = ["variant", "is_active"]
    pagination_class = None

    def get_queryset(self):
        # A Recipe hangs off a variant and carries no branch column of its own,
        # so it is scoped through the product that owns it rather than through
        # the base class's `branch_id` filter.
        principal = auth_context(self.request)
        return self.queryset.filter(variant__product__branch_id=principal.branch_id)

    def perform_create(self, serializer) -> None:
        recipe = serializer.save(created_by_id=auth_context(self.request).user_id)
        # Cost the variant immediately. A recipe saved without this leaves the
        # product showing the margin it had before the ingredients existed.
        services.refresh_variant_cost(recipe)

    def perform_update(self, serializer) -> None:
        recipe = serializer.save(updated_by_id=auth_context(self.request).user_id)
        services.refresh_variant_cost(recipe)

    @extend_schema(
        summary="What one portion costs, and what the figure omits",
        responses={200: RecipeCostSerializer},
    )
    @action(detail=True, methods=["get"])
    def cost(self, request: Request, pk=None) -> Response:
        recipe = self.get_object()
        cost = services.compute_cost(recipe)
        price = recipe.variant.price

        margin = price - cost.total
        margin_percent = (
            (margin / price * Decimal("100")).quantize(Decimal("0.01")) if price > 0 else None
        )

        return Response(
            RecipeCostSerializer(
                {
                    "total": cost.total,
                    "lines": [
                        {
                            "item_code": line.item_code,
                            "item_name": line.item_name,
                            "quantity": line.quantity,
                            "unit_code": line.unit_code,
                            "unit_cost": line.unit_cost,
                            "line_cost": line.line_cost,
                        }
                        for line in cost.lines
                    ],
                    "missing_costs": list(cost.missing_costs),
                    "price": price,
                    "margin": margin,
                    "margin_percent": margin_percent,
                }
            ).data
        )

    @extend_schema(
        summary="Recompute and store the variant's cost",
        request=None,
        responses={200: RecipeSerializer},
    )
    @action(detail=True, methods=["post"], url_path="refresh-cost")
    def refresh_cost(self, request: Request, pk=None) -> Response:
        recipe = self.get_object()
        services.refresh_variant_cost(recipe)
        recipe.refresh_from_db()
        return Response(RecipeSerializer(recipe).data)


class VariantRecipeView(APIView):
    """
    The recipe for one variant, by variant id.

    A convenience the product screen needs: it holds a variant and wants to know
    whether a recipe exists without listing every recipe in the branch.
    """

    permission_classes = [IsAuthenticatedPrincipal, HasPermission]
    required_permission = "catalog.view"

    @extend_schema(summary="Recipe for a variant", responses={200: RecipeSerializer})
    def get(self, request: Request, variant_id) -> Response:
        principal = auth_context(request)
        recipe = (
            Recipe.objects.filter(
                variant_id=variant_id, variant__product__branch_id=principal.branch_id
            )
            .select_related("variant")
            .prefetch_related("lines__item", "lines__unit")
            .first()
        )
        if recipe is None:
            raise NotFoundError("لا توجد وصفة لهذا الصنف", code="RECIPE_NOT_FOUND")
        return Response(RecipeSerializer(recipe).data)
