"""Cashier shifts and the cash they are accountable for."""

from __future__ import annotations

from decimal import Decimal

from django.db import models

from apps.core.models import BaseModel, TenantScopedModel

MONEY = {"max_digits": 12, "decimal_places": 2}


class ShiftStatus(models.TextChoices):
    OPEN = "OPEN", "OPEN"
    CLOSING = "CLOSING", "CLOSING"
    CLOSED = "CLOSED", "CLOSED"


class Shift(TenantScopedModel):
    device_id = models.UUIDField(null=True, blank=True)
    user = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.PROTECT, related_name="shifts"
    )
    status = models.CharField(max_length=8, choices=ShiftStatus.choices, default=ShiftStatus.OPEN)

    opening_cash = models.DecimalField(**MONEY, default=Decimal("0"))
    counted_cash = models.DecimalField(**MONEY, null=True, blank=True)
    variance = models.DecimalField(**MONEY, null=True, blank=True)
    variance_reason = models.CharField(max_length=200, blank=True)

    opened_at = models.DateTimeField(auto_now_add=True, db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    z_report = models.JSONField(default=dict, blank=True, help_text="Frozen at close.")

    class Meta:
        db_table = "shifts"
        ordering = ["-opened_at"]
        constraints = [
            # One open shift per device: two would make the cash count meaningless.
            models.UniqueConstraint(
                fields=["device_id"],
                condition=models.Q(status="OPEN"),
                name="one_open_shift_per_device",
            )
        ]
        indexes = [models.Index(fields=["branch", "status"], name="idx_shift_branch_status")]

    def __str__(self) -> str:
        who = self.user.full_name_ar if self.user else "—"
        return f"{who} @ {self.opened_at:%Y-%m-%d %H:%M} ({self.status})"

    @property
    def is_open(self) -> bool:
        return self.status == ShiftStatus.OPEN


class CashMovementType(models.TextChoices):
    IN = "IN", "IN"
    OUT = "OUT", "OUT"
    EXPENSE = "EXPENSE", "EXPENSE"
    DROP = "DROP", "DROP"


class CashMovement(BaseModel):
    """Cash in or out of the drawer that is not a sale."""

    shift = models.ForeignKey(Shift, on_delete=models.PROTECT, related_name="cash_movements")
    movement_type = models.CharField(max_length=8, choices=CashMovementType.choices)
    amount = models.DecimalField(**MONEY, help_text="Always positive; the type carries direction.")
    reason = models.CharField(max_length=200)
    user = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "cash_movements"
        ordering = ["-occurred_at"]

    def __str__(self) -> str:
        return f"{self.movement_type} {self.amount}"

    @property
    def signed_amount(self) -> Decimal:
        return self.amount if self.movement_type == CashMovementType.IN else -self.amount
