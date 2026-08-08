from __future__ import annotations

from rest_framework import serializers

from .models import Supplier, SupplierLedgerEntry


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = [
            "id",
            "name",
            "phone",
            "email",
            "address",
            "tax_number",
            "payment_terms_days",
            "notes",
            "current_balance",
            "is_active",
        ]
        # `current_balance` is a projection of the ledger and is never written
        # directly — the same discipline `StockLevel` follows. A settable balance
        # would let a typo erase a debt with no entry to explain it.
        read_only_fields = ["id", "current_balance"]


class SupplierLedgerEntrySerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.full_name_ar", read_only=True, default=None)

    class Meta:
        model = SupplierLedgerEntry
        fields = [
            "id",
            "entry_type",
            "amount",
            "balance_after",
            "ref_type",
            "ref_id",
            "reference",
            "notes",
            "user_name",
            "occurred_at",
        ]
        read_only_fields = fields


class SupplierPaymentSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    reference = serializers.CharField(max_length=64, required=False, allow_blank=True)


class SupplierStatementSerializer(serializers.Serializer):
    """A statement of account, plus the proof that it still adds up."""

    supplier_id = serializers.UUIDField()
    supplier_name = serializers.CharField()
    current_balance = serializers.DecimalField(max_digits=12, decimal_places=2)
    drift = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Stored balance minus the replayed ledger. Non-zero is a bug in a write path.",
    )
    entries = SupplierLedgerEntrySerializer(many=True)
