from __future__ import annotations

from rest_framework import serializers

from .models import Order, OrderEvent, OrderItem, OrderItemModifier, OrderType


class OrderItemModifierSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItemModifier
        fields = ["id", "name_snapshot", "price_delta_snapshot"]
        read_only_fields = fields


class OrderItemSerializer(serializers.ModelSerializer):
    modifiers = OrderItemModifierSerializer(many=True, read_only=True)
    station_name = serializers.CharField(source="station.name_ar", read_only=True, default=None)

    class Meta:
        model = OrderItem
        fields = [
            "id",
            "line_id",
            "variant",
            "name_snapshot",
            "unit_price_snapshot",
            # Both, always. The override alone cannot answer "what was it
            # supposed to be", and the snapshot alone hides that anything
            # happened at all.
            "price_override",
            "price_override_reason",
            "quantity",
            "discount_percent",
            "line_gross",
            "line_discount",
            "line_total",
            "status",
            "note",
            "fired_at",
            "voided_at",
            "void_reason",
            "station",
            "station_name",
            "modifiers",
        ]
        # Every money field is computed server-side. A client that sent one
        # would be ignored; making them read-only says so out loud.
        read_only_fields = fields


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    table_number = serializers.CharField(
        source="table_session.table.number", read_only=True, default=None
    )
    balance_due = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    opened_by_name = serializers.CharField(
        source="opened_by.full_name_ar", read_only=True, default=None
    )

    class Meta:
        model = Order
        fields = [
            "id",
            "local_number",
            "order_type",
            "status",
            "table_session",
            "table_number",
            "customer_name",
            "customer_phone",
            "subtotal",
            "discount_total",
            "discount_percent",
            "discount_reason",
            "service_total",
            "tax_total",
            "rounding_adjustment",
            "grand_total",
            "paid_total",
            "balance_due",
            "vat_percent",
            "service_percent",
            "vat_inclusive",
            "opened_at",
            "closed_at",
            "opened_by_name",
            "void_reason",
            "shift",
            "items",
        ]
        read_only_fields = fields


class OrderSummarySerializer(serializers.ModelSerializer):
    """Lighter payload for the open-orders board, which polls frequently."""

    table_number = serializers.CharField(
        source="table_session.table.number", read_only=True, default=None
    )
    item_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Order
        fields = [
            "id",
            "local_number",
            "order_type",
            "status",
            "table_number",
            "grand_total",
            "paid_total",
            "opened_at",
            "item_count",
        ]
        read_only_fields = fields


class OpenOrderSerializer(serializers.Serializer):
    order_id = serializers.UUIDField(
        required=False,
        help_text="Client-minted, so an offline device can name an order before syncing.",
    )
    order_type = serializers.ChoiceField(choices=OrderType.choices, default=OrderType.DINE_IN)
    table_session = serializers.UUIDField(required=False, allow_null=True)
    shift = serializers.UUIDField(required=False, allow_null=True)


class EventSerializer(serializers.Serializer):
    id = serializers.UUIDField(required=False, help_text="Idempotency key. Client-minted.")
    type = serializers.CharField()
    payload = serializers.DictField(required=False, default=dict)
    occurred_at = serializers.DateTimeField(required=False)


class EventBatchSerializer(serializers.Serializer):
    events = EventSerializer(many=True)


class ApplyResultSerializer(serializers.Serializer):
    applied = serializers.ListField(child=serializers.CharField())
    skipped = serializers.ListField(
        child=serializers.CharField(), help_text="Already recorded — a replay, not an error."
    )
    order = OrderSerializer()


class OrderEventSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.full_name_ar", read_only=True, default=None)
    approved_by_name = serializers.CharField(
        source="approved_by.full_name_ar", read_only=True, default=None
    )

    class Meta:
        model = OrderEvent
        fields = [
            "id",
            "sequence",
            "event_type",
            "payload",
            "actor_name",
            "approved_by_name",
            "occurred_at",
            "recorded_at",
        ]
        read_only_fields = fields


class VoidOrderSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=200)
