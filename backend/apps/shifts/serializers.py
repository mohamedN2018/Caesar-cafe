from __future__ import annotations

from rest_framework import serializers

from .models import CashMovement, CashMovementType, Shift


class ShiftSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.full_name_ar", read_only=True, default=None)
    closed_by_name = serializers.CharField(
        source="closed_by.full_name_ar", read_only=True, default=None
    )

    class Meta:
        model = Shift
        fields = [
            "id",
            "status",
            "user",
            "user_name",
            "device_id",
            "opening_cash",
            "counted_cash",
            "variance",
            "variance_reason",
            "opened_at",
            "closed_at",
            "closed_by_name",
            "z_report",
        ]
        read_only_fields = fields


class OpenShiftSerializer(serializers.Serializer):
    opening_cash = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0, default=0)


class CloseShiftSerializer(serializers.Serializer):
    counted_cash = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    reason = serializers.CharField(max_length=200, required=False, allow_blank=True)


class CashMovementSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.full_name_ar", read_only=True, default=None)

    class Meta:
        model = CashMovement
        fields = ["id", "movement_type", "amount", "reason", "user_name", "occurred_at"]
        read_only_fields = ["id", "user_name", "occurred_at"]


class CashMovementRequestSerializer(serializers.Serializer):
    movement_type = serializers.ChoiceField(choices=CashMovementType.choices)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    reason = serializers.CharField(max_length=200)


class ShiftReportSerializer(serializers.Serializer):
    """
    The X and Z reports share a shape.

    Money fields are strings: these are Decimals serialized exactly, and a JSON
    float would reintroduce the imprecision the whole system avoids.
    """

    shift_id = serializers.CharField()
    opened_at = serializers.CharField()
    user = serializers.CharField(allow_null=True)
    is_final = serializers.BooleanField()
    gross_sales = serializers.CharField()
    discounts = serializers.CharField()
    refunds = serializers.CharField()
    net_sales = serializers.CharField()
    tax = serializers.CharField()
    service = serializers.CharField()
    cash_sales = serializers.CharField()
    non_cash_sales = serializers.CharField()
    cash_in = serializers.CharField()
    cash_out = serializers.CharField()
    expected_cash = serializers.CharField()
    order_count = serializers.IntegerField()
    void_count = serializers.IntegerField()
