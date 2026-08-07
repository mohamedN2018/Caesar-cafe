"""
Bills of material — what a sold item consumes from stock.

This is the bridge between the catalog and the ledger. When a cappuccino sells,
its recipe is what tells the server to deduct 18g of beans and 150ml of milk.
Without it, inventory is a list someone updates by hand and then stops trusting.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models

from apps.core.models import BaseModel

QUANTITY = {"max_digits": 14, "decimal_places": 3}


class Recipe(BaseModel):
    variant = models.OneToOneField(
        "catalog.ProductVariant", on_delete=models.CASCADE, related_name="recipe"
    )
    yield_quantity = models.DecimalField(
        **QUANTITY,
        default=Decimal("1"),
        help_text="Portions produced. A batch of 10 divides its lines by 10 per sale.",
    )
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "recipes"

    def __str__(self) -> str:
        return f"Recipe for {self.variant}"


class RecipeLine(BaseModel):
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(
        "inventory.InventoryItem", on_delete=models.PROTECT, related_name="recipe_lines"
    )
    quantity = models.DecimalField(**QUANTITY)
    unit = models.ForeignKey(
        "inventory.Unit", on_delete=models.PROTECT, related_name="recipe_lines"
    )

    waste_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0"),
        help_text=(
            "Real shrinkage — beans lost in grinding, milk left in the pitcher. "
            "Without it, theoretical stock drifts from counted stock and staff "
            "stop trusting the numbers, which is how inventory systems die."
        ),
    )
    is_optional = models.BooleanField(default=False)

    class Meta:
        db_table = "recipe_lines"
        constraints = [
            models.UniqueConstraint(fields=["recipe", "item"], name="uniq_item_per_recipe")
        ]

    def __str__(self) -> str:
        return f"{self.quantity} {self.unit.code} {self.item.name_ar}"

    @property
    def effective_quantity(self) -> Decimal:
        """Quantity including expected waste — what actually leaves the shelf."""
        return self.quantity * (Decimal("1") + self.waste_percent / Decimal("100"))
