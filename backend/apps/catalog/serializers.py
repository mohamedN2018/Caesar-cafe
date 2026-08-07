from __future__ import annotations

from rest_framework import serializers

from .models import Category, Modifier, ModifierGroup, Product, ProductVariant


class CategorySerializer(serializers.ModelSerializer):
    product_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Category
        fields = [
            "id",
            "parent",
            "name_ar",
            "name_en",
            "color",
            "sort_order",
            "is_active",
            "product_count",
        ]
        read_only_fields = ["id", "product_count"]


class ProductVariantSerializer(serializers.ModelSerializer):
    margin = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    margin_percent = serializers.DecimalField(max_digits=6, decimal_places=2, read_only=True)

    class Meta:
        model = ProductVariant
        fields = [
            "id",
            "name_ar",
            "sku",
            "price",
            "cost",
            "margin",
            "margin_percent",
            "is_default",
            "sort_order",
            "is_active",
        ]
        # `cost` is computed from the recipe — never entered by hand.
        read_only_fields = ["id", "cost", "margin", "margin_percent"]


class ProductSerializer(serializers.ModelSerializer):
    variants = ProductVariantSerializer(many=True, read_only=True)
    category_name = serializers.CharField(source="category.name_ar", read_only=True)
    station_name = serializers.CharField(source="station.name_ar", read_only=True, default=None)

    class Meta:
        model = Product
        fields = [
            "id",
            "category",
            "category_name",
            "station",
            "station_name",
            "sku",
            "barcode",
            "name_ar",
            "name_en",
            "description_ar",
            "image",
            "tax_percent",
            "is_tax_exempt",
            "track_inventory",
            "is_sellable",
            "is_active",
            "sort_order",
            "variants",
        ]
        read_only_fields = ["id", "variants", "category_name", "station_name"]


class ModifierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Modifier
        fields = [
            "id",
            "name_ar",
            "price_delta",
            "inventory_item",
            "quantity_consumed",
            "sort_order",
            "is_active",
        ]
        read_only_fields = ["id"]


class ModifierGroupSerializer(serializers.ModelSerializer):
    modifiers = ModifierSerializer(many=True, read_only=True)

    class Meta:
        model = ModifierGroup
        fields = [
            "id",
            "name_ar",
            "min_select",
            "max_select",
            "is_required",
            "sort_order",
            "modifiers",
        ]
        read_only_fields = ["id", "modifiers"]


class PriceChangeSerializer(serializers.Serializer):
    variant = serializers.UUIDField()
    new_price = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    reason = serializers.CharField(max_length=200, required=False, allow_blank=True)
