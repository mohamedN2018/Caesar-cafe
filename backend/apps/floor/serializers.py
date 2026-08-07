from __future__ import annotations

from rest_framework import serializers

from .models import Area, Table, TableSession


class AreaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Area
        fields = ["id", "name_ar", "sort_order", "is_active"]
        read_only_fields = ["id"]


class TableSerializer(serializers.ModelSerializer):
    area_name = serializers.CharField(source="area.name_ar", read_only=True)

    class Meta:
        model = Table
        fields = [
            "id",
            "area",
            "area_name",
            "number",
            "seats",
            "status",
            "pos_x",
            "pos_y",
            "is_active",
        ]
        read_only_fields = ["id", "area_name"]


class TableSessionSerializer(serializers.ModelSerializer):
    table_number = serializers.CharField(source="table.number", read_only=True)
    waiter_name = serializers.CharField(source="waiter.full_name_ar", read_only=True, default=None)

    class Meta:
        model = TableSession
        fields = [
            "id",
            "table",
            "table_number",
            "guest_count",
            "opened_at",
            "closed_at",
            "waiter",
            "waiter_name",
        ]
        read_only_fields = ["id", "opened_at", "table_number", "waiter_name"]


class OpenSessionSerializer(serializers.Serializer):
    table = serializers.UUIDField()
    guest_count = serializers.IntegerField(min_value=1, default=1)


class TransferSerializer(serializers.Serializer):
    target_table = serializers.UUIDField()


class FloorStatusSerializer(serializers.Serializer):
    """The live board: every table plus its open order summary."""

    table_id = serializers.UUIDField()
    number = serializers.CharField()
    area = serializers.CharField()
    seats = serializers.IntegerField()
    status = serializers.CharField()
    pos_x = serializers.IntegerField()
    pos_y = serializers.IntegerField()
    session_id = serializers.UUIDField(allow_null=True)
    guest_count = serializers.IntegerField(allow_null=True)
    opened_at = serializers.DateTimeField(allow_null=True)
    order_count = serializers.IntegerField()
    total_due = serializers.DecimalField(max_digits=12, decimal_places=2)
    waiter = serializers.CharField(allow_null=True)
