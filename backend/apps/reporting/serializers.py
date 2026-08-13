from __future__ import annotations

from rest_framework import serializers


class SalesSummarySerializer(serializers.Serializer):
    """
    Money is a string here, as everywhere else in this API.

    A JSON number would be a float on the way through, and a float is how a
    total that reconciled yesterday stops reconciling today.
    """

    date_from = serializers.DateField()
    date_to = serializers.DateField()
    boundary = serializers.CharField(help_text="The business-day start these numbers were cut on.")
    gross_sales = serializers.CharField()
    discounts = serializers.CharField()
    service = serializers.CharField()
    tax = serializers.CharField()
    refunds = serializers.CharField()
    net_sales = serializers.CharField()
    cash_sales = serializers.CharField()
    non_cash_sales = serializers.CharField()
    cogs = serializers.CharField()
    gross_profit = serializers.CharField()
    margin_percent = serializers.CharField()
    order_count = serializers.IntegerField()
    void_count = serializers.IntegerField()
    average_ticket = serializers.CharField()


class HourBucketSerializer(serializers.Serializer):
    hour = serializers.IntegerField()
    order_count = serializers.IntegerField()
    net_sales = serializers.CharField()


class TopProductSerializer(serializers.Serializer):
    variant_id = serializers.UUIDField()
    name = serializers.CharField()
    category = serializers.CharField()
    quantity = serializers.CharField()
    revenue = serializers.CharField()
    profit = serializers.CharField()
    void_count = serializers.IntegerField()


class WeekSerializer(serializers.Serializer):
    net_sales = serializers.CharField()
    order_count = serializers.IntegerField()
    average_ticket = serializers.CharField()


class DashboardSerializer(serializers.Serializer):
    """One call, because the owner opens this on a phone over a mobile connection."""

    business_date = serializers.DateField()
    boundary = serializers.CharField()
    today = SalesSummarySerializer()
    yesterday_net = serializers.CharField()
    change_percent = serializers.CharField(allow_null=True)
    week = WeekSerializer()
    open_orders = serializers.IntegerField()
    open_orders_value = serializers.CharField()
    open_tickets = serializers.IntegerField()
    open_shifts = serializers.IntegerField()
    kids_inside = serializers.IntegerField()
    top_products = TopProductSerializer(many=True)
    by_hour = HourBucketSerializer(many=True)


class RollupRebuildSerializer(serializers.Serializer):
    date_from = serializers.DateField()
    date_to = serializers.DateField()

    def validate(self, attrs: dict) -> dict:
        if attrs["date_from"] > attrs["date_to"]:
            raise serializers.ValidationError({"date_from": "تاريخ البداية بعد تاريخ النهاية."})
        return attrs
