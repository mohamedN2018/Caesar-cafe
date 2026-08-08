"""
Purchasing serializers.

Lines are written nested with their parent, deliberately. A purchase order
without lines is not a smaller purchase order — it is an invalid one, and
`submit_purchase_order` refuses it. Making the client POST a header and then N
line requests would leave that invalid state reachable and observable between
calls.
"""

from __future__ import annotations

from rest_framework import serializers

from .models import (
    GoodsReceipt,
    GRLine,
    POLine,
    PurchaseOrder,
    PurchaseReturn,
    PurchaseReturnLine,
)


class POLineSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name_ar", read_only=True)
    unit_code = serializers.CharField(source="unit.code", read_only=True)
    line_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    outstanding = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True)

    class Meta:
        model = POLine
        fields = [
            "id",
            "item",
            "item_code",
            "item_name",
            "unit",
            "unit_code",
            "quantity_ordered",
            "quantity_received",
            "unit_price",
            "line_total",
            "outstanding",
        ]
        # `quantity_received` is written by posting a goods receipt, never by
        # editing the order. An order that could mark itself received would make
        # the PO/GRN distinction — the reason this app exists — decorative.
        read_only_fields = ["id", "quantity_received"]


class PurchaseOrderSerializer(serializers.ModelSerializer):
    lines = POLineSerializer(many=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    is_fully_received = serializers.BooleanField(read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = [
            "id",
            "supplier",
            "supplier_name",
            "po_number",
            "status",
            "expected_date",
            "notes",
            "submitted_at",
            "subtotal",
            "is_fully_received",
            "lines",
            "created_at",
        ]
        read_only_fields = ["id", "status", "submitted_at", "created_at"]

    def create(self, validated_data: dict) -> PurchaseOrder:
        lines = validated_data.pop("lines", [])
        order = PurchaseOrder.objects.create(**validated_data)
        POLine.objects.bulk_create(POLine(purchase_order=order, **line) for line in lines)
        return order

    def update(self, instance: PurchaseOrder, validated_data: dict) -> PurchaseOrder:
        lines = validated_data.pop("lines", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()

        if lines is not None:
            # Replaced wholesale rather than diffed. A draft order is a scratch
            # document; once it is SUBMITTED the view refuses the edit entirely,
            # which is where the real protection is.
            instance.lines.all().delete()
            POLine.objects.bulk_create(POLine(purchase_order=instance, **line) for line in lines)
        return instance


class GRLineSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name_ar", read_only=True)
    unit_code = serializers.CharField(source="unit.code", read_only=True)
    line_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = GRLine
        fields = [
            "id",
            "po_line",
            "item",
            "item_code",
            "item_name",
            "unit",
            "unit_code",
            "quantity_received",
            "unit_cost",
            "expiry_date",
            "line_total",
        ]
        read_only_fields = ["id"]


class GoodsReceiptSerializer(serializers.ModelSerializer):
    lines = GRLineSerializer(many=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    grand_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    is_posted = serializers.BooleanField(read_only=True)

    class Meta:
        model = GoodsReceipt
        fields = [
            "id",
            "purchase_order",
            "supplier",
            "supplier_name",
            "grn_number",
            "supplier_invoice_no",
            "received_date",
            "notes",
            "posted_at",
            "is_posted",
            "grand_total",
            "lines",
            "created_at",
        ]
        read_only_fields = ["id", "posted_at", "created_at"]

    def create(self, validated_data: dict) -> GoodsReceipt:
        lines = validated_data.pop("lines", [])
        receipt = GoodsReceipt.objects.create(**validated_data)
        GRLine.objects.bulk_create(GRLine(receipt=receipt, **line) for line in lines)
        return receipt

    def update(self, instance: GoodsReceipt, validated_data: dict) -> GoodsReceipt:
        lines = validated_data.pop("lines", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()

        if lines is not None:
            instance.lines.all().delete()
            GRLine.objects.bulk_create(GRLine(receipt=instance, **line) for line in lines)
        return instance


class PurchaseReturnLineSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name_ar", read_only=True)
    line_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = PurchaseReturnLine
        fields = [
            "id",
            "item",
            "item_code",
            "item_name",
            "unit",
            "quantity",
            "unit_cost",
            "line_total",
        ]
        read_only_fields = ["id"]


class PurchaseReturnSerializer(serializers.ModelSerializer):
    lines = PurchaseReturnLineSerializer(many=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    grand_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = PurchaseReturn
        fields = [
            "id",
            "supplier",
            "supplier_name",
            "receipt",
            "reference",
            "reason",
            "returned_date",
            "posted_at",
            "grand_total",
            "lines",
            "created_at",
        ]
        read_only_fields = ["id", "posted_at", "created_at"]

    def create(self, validated_data: dict) -> PurchaseReturn:
        lines = validated_data.pop("lines", [])
        purchase_return = PurchaseReturn.objects.create(**validated_data)
        PurchaseReturnLine.objects.bulk_create(
            PurchaseReturnLine(purchase_return=purchase_return, **line) for line in lines
        )
        return purchase_return


class ReorderSuggestionSerializer(serializers.Serializer):
    item_id = serializers.UUIDField()
    item_code = serializers.CharField()
    item_name = serializers.CharField()
    on_hand = serializers.DecimalField(max_digits=14, decimal_places=3)
    available = serializers.DecimalField(max_digits=14, decimal_places=3)
    reorder_level = serializers.DecimalField(max_digits=14, decimal_places=3)
    suggested_quantity = serializers.DecimalField(max_digits=14, decimal_places=3)
    supplier = serializers.CharField(allow_null=True)
    supplier_id = serializers.CharField(allow_null=True)


class ValuationSerializer(serializers.Serializer):
    total = serializers.DecimalField(max_digits=14, decimal_places=2)
    by_type = serializers.DictField(child=serializers.DecimalField(max_digits=14, decimal_places=2))
