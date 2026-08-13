from __future__ import annotations

from rest_framework import serializers

from .models import Invoice, Payment, PaymentMethod, Refund


class PaymentMethodSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentMethod
        fields = [
            "id",
            "code",
            "name_ar",
            "opens_drawer",
            "requires_reference",
            "counts_as_cash",
            "is_active",
            "sort_order",
        ]
        read_only_fields = ["id"]


class PaymentSerializer(serializers.ModelSerializer):
    method_name = serializers.CharField(source="method.name_ar", read_only=True)
    received_by_name = serializers.CharField(
        source="received_by.full_name_ar", read_only=True, default=None
    )
    order_number = serializers.CharField(source="order.local_number", read_only=True)

    class Meta:
        model = Payment
        fields = [
            "id",
            "order",
            "order_number",
            "method",
            "method_name",
            "amount",
            "tendered",
            "change_given",
            "reference",
            "received_by_name",
            "paid_at",
        ]
        read_only_fields = fields


class PaymentRequestSerializer(serializers.Serializer):
    order = serializers.UUIDField()
    method = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    tendered = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True
    )
    reference = serializers.CharField(max_length=64, required=False, allow_blank=True)


class RefundSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source="order.local_number", read_only=True)
    refunded_by_name = serializers.CharField(
        source="refunded_by.full_name_ar", read_only=True, default=None
    )
    approved_by_name = serializers.CharField(
        source="approved_by.full_name_ar", read_only=True, default=None
    )

    class Meta:
        model = Refund
        fields = [
            "id",
            "order",
            "order_number",
            "original_payment",
            "amount",
            "reason",
            "refunded_by_name",
            "approved_by_name",
            "refunded_at",
        ]
        read_only_fields = fields


class RefundRequestSerializer(serializers.Serializer):
    order = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    reason = serializers.CharField(max_length=200)
    original_payment = serializers.UUIDField(required=False, allow_null=True)


class InvoiceSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source="order.local_number", read_only=True)
    grand_total = serializers.DecimalField(
        source="order.grand_total", max_digits=12, decimal_places=2, read_only=True
    )

    class Meta:
        model = Invoice
        fields = [
            "id",
            "order",
            "order_number",
            "invoice_number",
            "serial",
            "issued_at",
            "grand_total",
            "snapshot",
        ]
        read_only_fields = fields
