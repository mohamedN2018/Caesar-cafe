from __future__ import annotations

from rest_framework import serializers

from .models import InventoryItem, StockCount, StockLevel, StockMovement, Unit


class UnitSerializer(serializers.ModelSerializer):
    """
    A unit of measure — KG, G, L, ML, PC.

    Read-only, and that is the decision rather than an omission. Units are
    reference data with conversions hanging off them (`UnitConversion`: 1 KG =
    1000 G), so inventing "بوكس" from a form would produce a unit that converts to
    nothing — every recipe and every stock movement in it would be uncostable.
    Adding a real unit means adding its conversions too, which is a migration, not
    a text box.

    Exposed because an inventory item cannot be created without picking one, and
    there was no way to list them: the item endpoint had full CRUD and the form it
    needed was unbuildable.
    """

    class Meta:
        model = Unit
        fields = ["id", "code", "name_ar", "decimal_places"]
        read_only_fields = fields


class InventoryItemSerializer(serializers.ModelSerializer):
    base_unit_code = serializers.CharField(source="base_unit.code", read_only=True)
    quantity_on_hand = serializers.DecimalField(
        source="level.quantity_on_hand", max_digits=14, decimal_places=3, read_only=True
    )
    weighted_avg_cost = serializers.DecimalField(
        source="level.weighted_avg_cost", max_digits=12, decimal_places=4, read_only=True
    )

    class Meta:
        model = InventoryItem
        fields = [
            "id",
            "code",
            "name_ar",
            "name_en",
            "item_type",
            "base_unit",
            "base_unit_code",
            "default_supplier",
            "minimum_stock",
            "reorder_level",
            "reorder_quantity",
            "costing_method",
            "is_active",
            "quantity_on_hand",
            "weighted_avg_cost",
        ]
        read_only_fields = ["id", "quantity_on_hand", "weighted_avg_cost"]


class StockLevelSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name_ar", read_only=True)
    unit_code = serializers.CharField(source="item.base_unit.code", read_only=True)
    total_value = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    quantity_available = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True)
    is_low = serializers.BooleanField(read_only=True)

    class Meta:
        model = StockLevel
        fields = [
            "item",
            "item_code",
            "item_name",
            "unit_code",
            "quantity_on_hand",
            "quantity_reserved",
            "quantity_available",
            "weighted_avg_cost",
            "total_value",
            "is_low",
            "last_movement_at",
        ]
        read_only_fields = fields


class StockMovementSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name_ar", read_only=True)
    user_name = serializers.CharField(source="user.full_name_ar", read_only=True, default=None)
    value_delta = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = StockMovement
        fields = [
            "id",
            "item",
            "item_code",
            "item_name",
            "movement_type",
            "quantity_delta",
            "unit_cost",
            "balance_after",
            "value_delta",
            "ref_type",
            "ref_id",
            "user_name",
            "reason",
            "occurred_at",
        ]
        read_only_fields = fields


class AdjustmentSerializer(serializers.Serializer):
    item = serializers.UUIDField()
    new_quantity = serializers.DecimalField(max_digits=14, decimal_places=3)
    reason = serializers.CharField(max_length=500)


class WasteSerializer(serializers.Serializer):
    item = serializers.UUIDField()
    quantity = serializers.DecimalField(max_digits=14, decimal_places=3, min_value=0)
    reason = serializers.CharField(max_length=500)


class StockCountSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockCount
        fields = ["id", "reference", "status", "notes", "counted_at", "posted_at", "created_at"]
        read_only_fields = ["id", "status", "posted_at", "created_at"]


class DriftSerializer(serializers.Serializer):
    item_code = serializers.CharField()
    item_name = serializers.CharField()
    level_quantity = serializers.DecimalField(max_digits=14, decimal_places=3)
    ledger_quantity = serializers.DecimalField(max_digits=14, decimal_places=3)
    difference = serializers.DecimalField(max_digits=14, decimal_places=3)
