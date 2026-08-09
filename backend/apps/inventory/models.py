"""
Stock, measured in an append-only ledger.

The rule that governs this whole app: `StockLevel` is a PROJECTION, never the
truth. Truth is the ordered sequence of `StockMovement` rows. Any code path that
changes stock appends a movement in the same transaction that updates the level
— see `services.apply_movement`.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models

from apps.core.models import BaseModel, SequentialBaseModel, SoftDeletableModel, TenantScopedModel
from apps.core.precision import QUANTITY, UNIT_COST


class Unit(BaseModel):
    """A unit of measure. Conversions live in `UnitConversion`."""

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="units"
    )
    code = models.CharField(max_length=16, help_text="KG, G, L, ML, PCS")
    name_ar = models.CharField(max_length=50)
    decimal_places = models.PositiveSmallIntegerField(default=3)

    class Meta:
        db_table = "units"
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], name="uniq_unit_per_org")
        ]

    def __str__(self) -> str:
        return self.code


class UnitConversion(BaseModel):
    """1 `from_unit` = `factor` × `to_unit`. e.g. 1 KG = 1000 G."""

    from_unit = models.ForeignKey(Unit, on_delete=models.CASCADE, related_name="conversions_from")
    to_unit = models.ForeignKey(Unit, on_delete=models.CASCADE, related_name="conversions_to")
    factor = models.DecimalField(max_digits=16, decimal_places=6)

    class Meta:
        db_table = "unit_conversions"
        constraints = [
            models.UniqueConstraint(fields=["from_unit", "to_unit"], name="uniq_unit_conversion"),
            models.CheckConstraint(
                condition=~models.Q(from_unit=models.F("to_unit")),
                name="conversion_between_different_units",
            ),
        ]

    def __str__(self) -> str:
        return f"1 {self.from_unit.code} = {self.factor} {self.to_unit.code}"


class ItemType(models.TextChoices):
    RAW = "RAW", "RAW"
    CONSUMABLE = "CONSUMABLE", "CONSUMABLE"
    PACKAGING = "PACKAGING", "PACKAGING"
    FINISHED = "FINISHED", "FINISHED"


class CostingMethod(models.TextChoices):
    WEIGHTED_AVG = "WEIGHTED_AVG", "WEIGHTED_AVG"
    FIFO = "FIFO", "FIFO"


class InventoryItem(TenantScopedModel, SoftDeletableModel):
    code = models.CharField(max_length=32)
    name_ar = models.CharField(max_length=200)
    name_en = models.CharField(max_length=200, blank=True)
    item_type = models.CharField(max_length=16, choices=ItemType.choices, default=ItemType.RAW)

    base_unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="items")
    default_supplier = models.ForeignKey(
        "suppliers.Supplier",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="default_for_items",
    )

    minimum_stock = models.DecimalField(**QUANTITY, default=Decimal("0"))
    reorder_level = models.DecimalField(**QUANTITY, default=Decimal("0"))
    reorder_quantity = models.DecimalField(**QUANTITY, default=Decimal("0"))

    costing_method = models.CharField(
        max_length=16, choices=CostingMethod.choices, default=CostingMethod.WEIGHTED_AVG
    )

    class Meta:
        db_table = "inventory_items"
        ordering = ["name_ar"]
        constraints = [
            models.UniqueConstraint(fields=["branch", "code"], name="uniq_item_code_per_branch")
        ]
        indexes = [
            models.Index(fields=["branch", "is_active"], name="idx_item_branch_active"),
        ]

    def __str__(self) -> str:
        return f"{self.name_ar} ({self.code})"


class StockLevel(models.Model):
    """
    Current quantity and value — derived, never authoritative.

    A nightly Celery task replays each item's movements and alarms on drift.
    That reconciliation is how we discover whether a code path ever bypassed
    `apply_movement`.
    """

    item = models.OneToOneField(
        InventoryItem, on_delete=models.CASCADE, primary_key=True, related_name="level"
    )
    quantity_on_hand = models.DecimalField(**QUANTITY, default=Decimal("0"))
    quantity_reserved = models.DecimalField(
        **QUANTITY,
        default=Decimal("0"),
        help_text="Committed to open, unpaid orders — so low-stock alerts reflect reality.",
    )
    weighted_avg_cost = models.DecimalField(**UNIT_COST, default=Decimal("0"))
    last_movement_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "stock_levels"

    def __str__(self) -> str:
        return f"{self.item.name_ar}: {self.quantity_on_hand}"

    @property
    def total_value(self) -> Decimal:
        return (self.quantity_on_hand * self.weighted_avg_cost).quantize(Decimal("0.01"))

    @property
    def quantity_available(self) -> Decimal:
        return self.quantity_on_hand - self.quantity_reserved

    @property
    def is_low(self) -> bool:
        return self.quantity_available <= self.item.minimum_stock


class MovementType(models.TextChoices):
    OPENING = "OPENING", "OPENING"
    PURCHASE = "PURCHASE", "PURCHASE"
    SALE = "SALE", "SALE"
    WASTE = "WASTE", "WASTE"
    ADJUSTMENT = "ADJUSTMENT", "ADJUSTMENT"
    RETURN = "RETURN", "RETURN"
    TRANSFER = "TRANSFER", "TRANSFER"
    COUNT = "COUNT", "COUNT"


class StockMovement(SequentialBaseModel):
    """
    One append-only entry in the stock ledger.

    `balance_after` is a snapshot taken under the same row lock that produced
    it, so the ledger can be audited without replaying every prior row.
    """

    branch = models.ForeignKey(
        "organizations.Branch", on_delete=models.PROTECT, related_name="stock_movements"
    )
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name="movements")

    movement_type = models.CharField(max_length=16, choices=MovementType.choices)
    quantity_delta = models.DecimalField(**QUANTITY, help_text="Signed: negative consumes stock.")
    unit_cost = models.DecimalField(**UNIT_COST, default=Decimal("0"))
    balance_after = models.DecimalField(**QUANTITY)

    ref_type = models.CharField(max_length=40, blank=True)
    ref_id = models.UUIDField(null=True, blank=True)

    user = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    device_id = models.UUIDField(null=True, blank=True)
    reason = models.TextField(blank=True)
    occurred_at = models.DateTimeField(db_index=True)

    class Meta:
        db_table = "stock_movements"
        ordering = ["-occurred_at", "-created_at"]
        indexes = [
            models.Index(fields=["item", "-occurred_at"], name="idx_movement_item_time"),
            models.Index(fields=["branch", "movement_type"], name="idx_movement_branch_type"),
            models.Index(fields=["ref_type", "ref_id"], name="idx_movement_ref"),
        ]

    def __str__(self) -> str:
        return f"{self.movement_type} {self.quantity_delta:+} {self.item.code}"

    @property
    def value_delta(self) -> Decimal:
        return (self.quantity_delta * self.unit_cost).quantize(Decimal("0.01"))


class CountStatus(models.TextChoices):
    DRAFT = "DRAFT", "DRAFT"
    COUNTING = "COUNTING", "COUNTING"
    REVIEW = "REVIEW", "REVIEW"
    POSTED = "POSTED", "POSTED"
    CANCELLED = "CANCELLED", "CANCELLED"


class StockCount(TenantScopedModel):
    """
    A physical count session.

    Posting is a separate, permissioned step: a count is an observation, and
    turning an observation into a stock adjustment is a financial decision.
    """

    reference = models.CharField(max_length=32)
    status = models.CharField(max_length=16, choices=CountStatus.choices, default=CountStatus.DRAFT)
    notes = models.TextField(blank=True)
    counted_at = models.DateTimeField(null=True, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        db_table = "stock_counts"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "reference"], name="uniq_count_ref_per_branch"
            )
        ]

    def __str__(self) -> str:
        return f"{self.reference} ({self.status})"


class CountLine(BaseModel):
    count = models.ForeignKey(StockCount, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name="count_lines")

    system_quantity = models.DecimalField(**QUANTITY, help_text="Snapshot when the line was added.")
    counted_quantity = models.DecimalField(**QUANTITY, null=True, blank=True)
    reason = models.TextField(blank=True)

    class Meta:
        db_table = "stock_count_lines"
        constraints = [
            models.UniqueConstraint(fields=["count", "item"], name="uniq_item_per_count")
        ]

    def __str__(self) -> str:
        return f"{self.item.code}: {self.counted_quantity}"

    @property
    def variance(self) -> Decimal | None:
        if self.counted_quantity is None:
            return None
        return self.counted_quantity - self.system_quantity
