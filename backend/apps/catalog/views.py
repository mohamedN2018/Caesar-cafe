"""Catalog API: categories, products, variants, modifiers."""

from __future__ import annotations

from django.db import transaction
from django.db.models import Count
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from apps.authz.drf import auth_context
from apps.core.exceptions import AppError, NotFoundError
from apps.core.viewsets import BranchScopedViewSet

from .models import Category, ModifierGroup, PriceHistory, Product, ProductVariant
from .serializers import (
    CategorySerializer,
    ModifierGroupSerializer,
    PriceChangeSerializer,
    ProductSerializer,
    ProductVariantSerializer,
)


class CategoryViewSet(BranchScopedViewSet):
    queryset = Category.all_objects.all()
    serializer_class = CategorySerializer
    required_permissions = {
        "GET": "catalog.view",
        "POST": "catalog.create",
        "default": "catalog.edit",
    }
    filterset_fields = ["parent", "is_active"]
    search_fields = ["name_ar", "name_en"]
    ordering_fields = ["sort_order", "name_ar", "created_at"]
    pagination_class = None  # a bounded list the POS grid loads whole

    def get_queryset(self):
        return super().get_queryset().annotate(product_count=Count("products"))


class ProductViewSet(BranchScopedViewSet):
    # `variants__channel_prices` and not just `variants`: the till reads a
    # channel price per variant, and without this a 43-product menu is a query
    # per variant on the one request a cashier waits for at open.
    queryset = Product.all_objects.select_related("category", "station").prefetch_related(
        "variants__channel_prices"
    )
    serializer_class = ProductSerializer
    required_permissions = {
        "GET": "catalog.view",
        "POST": "catalog.create",
        "default": "catalog.edit",
    }
    filterset_fields = ["category", "station", "is_active", "is_sellable"]
    search_fields = ["name_ar", "name_en", "sku", "barcode"]
    ordering_fields = ["sort_order", "name_ar", "created_at"]

    @extend_schema(
        summary="Add a variant",
        request=ProductVariantSerializer,
        responses={201: ProductVariantSerializer},
    )
    @action(detail=True, methods=["post"], url_path="variants")
    def add_variant(self, request: Request, pk=None) -> Response:
        product = self.get_object()
        serializer = ProductVariantSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # The first variant becomes the default, so a product is always sellable.
        is_first = not product.variants.exists()
        serializer.save(product=product, is_default=is_first)
        return Response(serializer.data, status=201)


class VariantPriceView(BranchScopedViewSet):
    """
    Price changes, always recorded.

    A receipt is a legal record of what was sold at what price, so every change
    leaves a trail that explains why last Monday's total differs from today's.
    """

    queryset = ProductVariant.objects.select_related("product")
    serializer_class = ProductVariantSerializer
    # Two different risks, two different permissions.
    #
    # Renaming a variant or removing one is ordinary catalogue work. Changing a
    # PRICE is not: it is marked sensitive in the permission catalogue and writes
    # a `PriceHistory` row, because a receipt is a legal record of what was sold
    # at what price and that trail is what explains last Monday's total.
    required_permissions = {
        "GET": "catalog.view",
        "PUT": "catalog.edit",
        "PATCH": "catalog.edit",
        "DELETE": "catalog.edit",
        "default": "catalog.change_price",
    }

    # PUT/PATCH/DELETE were missing entirely, so a variant created with the wrong
    # name or a duplicate size was permanent — the only way out was editing the
    # database by hand. DELETE deactivates rather than removes (see
    # `BranchScopedViewSet.perform_destroy`): a variant that has ever been sold
    # must survive, or the line items pointing at it lose their name.
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]

    def get_queryset(self):
        principal = auth_context(self.request)
        return self.queryset.filter(product__organization_id=principal.organization_id)

    @extend_schema(
        summary="Change a variant's price",
        request=PriceChangeSerializer,
        responses={200: ProductVariantSerializer},
    )
    @action(detail=False, methods=["post"], url_path="change")
    @transaction.atomic
    def change(self, request: Request) -> Response:
        serializer = PriceChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        variant = self.get_queryset().filter(id=data["variant"]).first()
        if variant is None:
            raise NotFoundError("الصنف غير موجود", code="VARIANT_NOT_FOUND")

        old_price = variant.price
        if old_price == data["new_price"]:
            raise AppError("السعر لم يتغير", code="PRICE_UNCHANGED")

        variant.price = data["new_price"]
        variant.save(update_fields=["price", "updated_at"])

        principal = auth_context(request)
        PriceHistory.objects.create(
            variant=variant,
            old_price=old_price,
            new_price=variant.price,
            changed_by_id=principal.user_id,
            reason=data.get("reason", ""),
        )
        return Response(ProductVariantSerializer(variant).data)


class ModifierGroupViewSet(BranchScopedViewSet):
    queryset = ModifierGroup.all_objects.prefetch_related("modifiers")
    serializer_class = ModifierGroupSerializer
    required_permissions = {
        "GET": "catalog.view",
        "POST": "catalog.create",
        "default": "catalog.edit",
    }
    pagination_class = None
