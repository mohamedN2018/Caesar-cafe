"""
Purchase orders and goods receipts.

The rule this app exists to enforce (docs/02 §25): a PURCHASE ORDER moves no
stock. Only a GOODS RECEIPT does.

A PO is an intention; a GRN is a fact. Cafes routinely receive partial
deliveries at prices that differ from what was ordered, and receiving at the
ACTUAL invoiced cost is what keeps weighted-average cost — and therefore gross
profit — honest.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models

from apps.core.models import BaseModel, TenantScopedModel

MONEY = {"max_digits": 12, "decimal_places": 2}
QUANTITY = {"max_digits": 14, "decimal_places": 3}
UNIT_COST = {"max_digits": 12, "decimal_places": 4}


class POStatus(models.TextChoices):
    DRAFT = "DRAFT", "DRAFT"
    SUBMITTED = "SUBMITTED", "SUBMITTED"
    PARTIAL = "PARTIAL", "PARTIAL"
    RECEIVED = "RECEIVED", "RECEIVED"
    CANCELLED = "CANCELLED", "CANCELLED"


class PurchaseOrder(TenantScopedModel):
    supplier = models.ForeignKey(
        "suppliers.Supplier", on_delete=models.PROTECT, related_name="purchase_orders"
    )
    po_number = models.CharField(max_length=32)
    status = models.CharField(max_length=16, choices=POStatus.choices, default=POStatus.DRAFT)
    expected_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "purchase_orders"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "po_number"], name="uniq_po_number_per_branch"
            )
        ]
        indexes = [models.Index(fields=["branch", "status"], name="idx_po_branch_status")]

    def __str__(self) -> str:
        return f"{self.po_number} ({self.status})"

    @property
    def subtotal(self) -> Decimal:
        return sum((line.line_total for line in self.lines.all()), Decimal("0.00"))

    @property
    def is_fully_received(self) -> bool:
        lines = list(self.lines.all())
        return bool(lines) and all(
            line.quantity_received >= line.quantity_ordered for line in lines
        )

    @property
    def is_partially_received(self) -> bool:
        return any(line.quantity_received > 0 for line in self.lines.all())


class POLine(BaseModel):
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name="lines"
    )
    item = models.ForeignKey(
        "inventory.InventoryItem", on_delete=models.PROTECT, related_name="po_lines"
    )
    unit = models.ForeignKey("inventory.Unit", on_delete=models.PROTECT, related_name="po_lines")

    quantity_ordered = models.DecimalField(**QUANTITY)
    quantity_received = models.DecimalField(**QUANTITY, default=Decimal("0"))
    unit_price = models.DecimalField(**UNIT_COST)

    class Meta:
        db_table = "purchase_order_lines"
        constraints = [
            models.UniqueConstraint(fields=["purchase_order", "item"], name="uniq_item_per_po")
        ]

    def __str__(self) -> str:
        return f"{self.quantity_ordered} {self.unit.code} {self.item.code}"

    @property
    def line_total(self) -> Decimal:
        return (self.quantity_ordered * self.unit_price).quantize(Decimal("0.01"))

    @property
    def outstanding(self) -> Decimal:
        return max(Decimal("0"), self.quantity_ordered - self.quantity_received)


class GoodsReceipt(TenantScopedModel):
    """
    Goods physically arrived. THIS is what moves stock.

    A receipt may exist without a purchase order — cafes buy milk from the shop
    down the road when they run out, and that has to be recordable.
    """

    purchase_order = models.ForeignKey(
        PurchaseOrder,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="receipts",
    )
    supplier = models.ForeignKey(
        "suppliers.Supplier", on_delete=models.PROTECT, related_name="receipts"
    )

    grn_number = models.CharField(max_length=32)
    supplier_invoice_no = models.CharField(max_length=64, blank=True)
    received_date = models.DateField()
    notes = models.TextField(blank=True)

    received_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    posted_at = models.DateTimeField(
        null=True, blank=True, help_text="When stock and the supplier ledger were written."
    )

    class Meta:
        db_table = "goods_receipts"
        ordering = ["-received_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["branch", "grn_number"], name="uniq_grn_per_branch")
        ]

    def __str__(self) -> str:
        return f"{self.grn_number} — {self.supplier.name}"

    @property
    def grand_total(self) -> Decimal:
        return sum((line.line_total for line in self.lines.all()), Decimal("0.00"))

    @property
    def is_posted(self) -> bool:
        return self.posted_at is not None


class GRLine(BaseModel):
    receipt = models.ForeignKey(GoodsReceipt, on_delete=models.CASCADE, related_name="lines")
    po_line = models.ForeignKey(
        POLine, null=True, blank=True, on_delete=models.SET_NULL, related_name="receipt_lines"
    )
    item = models.ForeignKey(
        "inventory.InventoryItem", on_delete=models.PROTECT, related_name="gr_lines"
    )
    unit = models.ForeignKey("inventory.Unit", on_delete=models.PROTECT, related_name="gr_lines")

    quantity_received = models.DecimalField(**QUANTITY)
    unit_cost = models.DecimalField(
        **UNIT_COST, help_text="ACTUAL invoiced cost, which may differ from the PO price."
    )
    expiry_date = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "goods_receipt_lines"

    def __str__(self) -> str:
        return f"{self.quantity_received} {self.unit.code} {self.item.code}"

    @property
    def line_total(self) -> Decimal:
        return (self.quantity_received * self.unit_cost).quantize(Decimal("0.01"))


class PurchaseReturn(TenantScopedModel):
    """Goods sent back: stock out, supplier balance down."""

    supplier = models.ForeignKey(
        "suppliers.Supplier", on_delete=models.PROTECT, related_name="returns"
    )
    receipt = models.ForeignKey(
        GoodsReceipt, null=True, blank=True, on_delete=models.PROTECT, related_name="returns"
    )
    reference = models.CharField(max_length=32)
    reason = models.TextField()
    returned_date = models.DateField()
    posted_at = models.DateTimeField(null=True, blank=True)
    created_by_user = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        db_table = "purchase_returns"
        ordering = ["-returned_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "reference"], name="uniq_return_ref_per_branch"
            )
        ]

    def __str__(self) -> str:
        return f"{self.reference} — {self.supplier.name}"

    @property
    def grand_total(self) -> Decimal:
        return sum((line.line_total for line in self.lines.all()), Decimal("0.00"))


class PurchaseReturnLine(BaseModel):
    purchase_return = models.ForeignKey(
        PurchaseReturn, on_delete=models.CASCADE, related_name="lines"
    )
    item = models.ForeignKey(
        "inventory.InventoryItem", on_delete=models.PROTECT, related_name="return_lines"
    )
    unit = models.ForeignKey(
        "inventory.Unit", on_delete=models.PROTECT, related_name="return_lines"
    )
    quantity = models.DecimalField(**QUANTITY)
    unit_cost = models.DecimalField(**UNIT_COST)

    class Meta:
        db_table = "purchase_return_lines"

    @property
    def line_total(self) -> Decimal:
        return (self.quantity * self.unit_cost).quantize(Decimal("0.01"))
