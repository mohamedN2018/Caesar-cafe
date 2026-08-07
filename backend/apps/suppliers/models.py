"""Suppliers and their running balance."""

from __future__ import annotations

from decimal import Decimal

from django.db import models

from apps.core.models import SequentialBaseModel, SoftDeletableModel, TenantScopedModel

MONEY = {"max_digits": 12, "decimal_places": 2}


class Supplier(TenantScopedModel, SoftDeletableModel):
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    tax_number = models.CharField(max_length=50, blank=True)
    payment_terms_days = models.PositiveSmallIntegerField(default=0)
    notes = models.TextField(blank=True)

    current_balance = models.DecimalField(
        **MONEY,
        default=Decimal("0"),
        help_text="Positive = we owe them. A projection of the ledger below.",
    )

    class Meta:
        db_table = "suppliers"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["branch", "name"], name="uniq_supplier_name_per_branch")
        ]

    def __str__(self) -> str:
        return self.name


class LedgerEntryType(models.TextChoices):
    INVOICE = "INVOICE", "INVOICE"
    PAYMENT = "PAYMENT", "PAYMENT"
    RETURN = "RETURN", "RETURN"
    ADJUSTMENT = "ADJUSTMENT", "ADJUSTMENT"


class SupplierLedgerEntry(SequentialBaseModel):
    """
    Append-only statement of account.

    `Supplier.current_balance` is derived from this, exactly as `StockLevel` is
    derived from `StockMovement`: the same discipline, for the same reason.
    """

    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="ledger")
    entry_type = models.CharField(max_length=16, choices=LedgerEntryType.choices)
    amount = models.DecimalField(**MONEY, help_text="Signed: positive increases what we owe.")
    balance_after = models.DecimalField(**MONEY)

    ref_type = models.CharField(max_length=40, blank=True)
    ref_id = models.UUIDField(null=True, blank=True)

    reference = models.CharField(max_length=64, blank=True, help_text="Supplier invoice number.")
    notes = models.TextField(blank=True)
    user = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    occurred_at = models.DateTimeField(db_index=True)

    class Meta:
        db_table = "supplier_ledger"
        ordering = ["-occurred_at", "-created_at"]
        indexes = [
            models.Index(fields=["supplier", "-occurred_at"], name="idx_ledger_supplier_time")
        ]

    def __str__(self) -> str:
        return f"{self.entry_type} {self.amount:+} → {self.balance_after}"
