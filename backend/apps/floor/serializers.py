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
    seated_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Table
        fields = [
            "id",
            "area",
            "area_name",
            "number",
            "seats",
            "seated_count",
            "status",
            "pos_x",
            "pos_y",
            "shape",
            "span_x",
            "span_y",
            "rotation",
            "is_active",
        ]
        read_only_fields = ["id", "area_name", "seated_count"]


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


class MergeSerializer(serializers.Serializer):
    """`into` survives; the session in the URL is folded into it and closes."""

    into = serializers.UUIDField(help_text="The session that keeps the combined bill.")


class FloorStatusSerializer(serializers.Serializer):
    """The live board: every table plus its open order summary."""

    table_id = serializers.UUIDField()
    number = serializers.CharField()
    area = serializers.CharField()
    seats = serializers.IntegerField()
    seated_count = serializers.IntegerField(help_text="People at the table now, not its capacity.")
    status = serializers.CharField()
    pos_x = serializers.IntegerField()
    pos_y = serializers.IntegerField()
    shape = serializers.CharField()
    span_x = serializers.IntegerField()
    span_y = serializers.IntegerField()
    rotation = serializers.IntegerField()
    session_id = serializers.UUIDField(allow_null=True)
    guest_count = serializers.IntegerField(allow_null=True)
    seated_minutes = serializers.IntegerField(
        allow_null=True, help_text="How long this party has been sitting."
    )
    opened_at = serializers.DateTimeField(allow_null=True)
    order_count = serializers.IntegerField()
    total_due = serializers.DecimalField(max_digits=12, decimal_places=2)
    waiter = serializers.CharField(allow_null=True)
