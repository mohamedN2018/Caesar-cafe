"""Payments, refunds and invoices."""

from __future__ import annotations

from decimal import Decimal

from django.db import models

from apps.core.models import BaseModel, TenantScopedModel

MONEY = {"max_digits": 12, "decimal_places": 2}


class PaymentMethod(TenantScopedModel):
    """
    Admin-managed rows, not an enum (commitment C10).

    A cafe adding InstaPay should not need a deployment.
    """

    code = models.CharField(max_length=32)
    name_ar = models.CharField(max_length=100)
    opens_drawer = models.BooleanField(default=False)
    requires_reference = models.BooleanField(default=False)
    counts_as_cash = models.BooleanField(
        default=False, help_text="Included in the shift's expected cash."
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "payment_methods"
        ordering = ["sort_order", "name_ar"]
        constraints = [
            models.UniqueConstraint(fields=["branch", "code"], name="uniq_method_code_per_branch")
        ]

    def __str__(self) -> str:
        return self.name_ar


class Payment(BaseModel):
    """
    Money taken. Never edited, never deleted — reversed by a Refund.

    `idempotency_key` is what makes a retried request safe: a payment retried
    after a timeout charges the customer once.
    """

    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="payments")
    method = models.ForeignKey(PaymentMethod, on_delete=models.PROTECT, related_name="payments")
    shift = models.ForeignKey(
        "shifts.Shift", null=True, blank=True, on_delete=models.PROTECT, related_name="payments"
    )

    amount = models.DecimalField(**MONEY)
    tendered = models.DecimalField(**MONEY, null=True, blank=True)
    change_given = models.DecimalField(**MONEY, default=Decimal("0"))
    reference = models.CharField(max_length=64, blank=True)

    idempotency_key = models.CharField(max_length=64, unique=True)
    received_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    device_id = models.UUIDField(null=True, blank=True)
    paid_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "payments"
        ordering = ["-paid_at"]
        indexes = [models.Index(fields=["order"], name="idx_payment_order")]

    def __str__(self) -> str:
        return f"{self.amount} {self.method.code}"


class Refund(BaseModel):
    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="refunds")
    original_payment = models.ForeignKey(
        Payment, null=True, blank=True, on_delete=models.PROTECT, related_name="refunds"
    )
    shift = models.ForeignKey(
        "shifts.Shift", null=True, blank=True, on_delete=models.PROTECT, related_name="refunds"
    )

    amount = models.DecimalField(**MONEY)
    reason = models.CharField(max_length=200)
    idempotency_key = models.CharField(max_length=64, unique=True)

    approved_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    refunded_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    refunded_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "refunds"
        ordering = ["-refunded_at"]

    def __str__(self) -> str:
        return f"-{self.amount} ({self.reason})"


class Invoice(BaseModel):
    """
    The frozen receipt.

    `snapshot` holds the entire rendered document as JSON, so a reprint two
    years later is byte-identical even if the product was long since deleted
    and the price changed five times.
    """

    order = models.OneToOneField("orders.Order", on_delete=models.PROTECT, related_name="invoice")
    invoice_number = models.BigIntegerField(help_text="From the device's reserved block (C9).")
    serial = models.CharField(max_length=32, help_text="MB-2026-000123")
    issued_at = models.DateTimeField(auto_now_add=True, db_index=True)
    snapshot = models.JSONField(default=dict)

    class Meta:
        db_table = "invoices"
        ordering = ["-issued_at"]
        constraints = [
            models.UniqueConstraint(fields=["serial"], name="uniq_invoice_serial"),
        ]

    def __str__(self) -> str:
        return self.serial
