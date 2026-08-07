"""
What the cafe sells.

Note the variant layer. A coffee shop adds sizes constantly (وسط / كبير), and
retrofitting variants after 3,000 orders reference `product_id` directly is a
painful migration touching every historical line item. A product with one
default variant costs almost nothing today and removes that migration entirely;
the POS hides the selector when there is only one.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import BaseModel, SoftDeletableModel, TenantScopedModel

MONEY = {"max_digits": 12, "decimal_places": 2}
PERCENT = {"max_digits": 5, "decimal_places": 2}


class Category(TenantScopedModel, SoftDeletableModel):
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )
    name_ar = models.CharField(max_length=100)
    name_en = models.CharField(max_length=100, blank=True)
    color = models.CharField(max_length=9, blank=True, help_text="#RRGGBB for the POS grid.")
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "categories"
        ordering = ["sort_order", "name_ar"]
        verbose_name_plural = "Categories"
        indexes = [models.Index(fields=["branch", "is_active"], name="idx_category_branch_active")]

    def __str__(self) -> str:
        return self.name_ar


class Product(TenantScopedModel, SoftDeletableModel):
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    station = models.ForeignKey(
        "kitchen.Station",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="products",
        help_text="Where this is prepared. Drives kitchen ticket routing.",
    )

    sku = models.CharField(max_length=32)
    barcode = models.CharField(max_length=64, blank=True)
    name_ar = models.CharField(max_length=200)
    name_en = models.CharField(max_length=200, blank=True)
    description_ar = models.TextField(blank=True)
    image = models.ImageField(upload_to="products/", null=True, blank=True)

    tax_percent = models.DecimalField(
        **PERCENT, null=True, blank=True, help_text="Null = use the branch VAT rate."
    )
    is_tax_exempt = models.BooleanField(default=False)
    track_inventory = models.BooleanField(
        default=True, help_text="False for items with no recipe, e.g. a bought-in bottle."
    )
    is_sellable = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "products"
        ordering = ["sort_order", "name_ar"]
        constraints = [
            models.UniqueConstraint(fields=["branch", "sku"], name="uniq_sku_per_branch")
        ]
        indexes = [
            models.Index(fields=["branch", "category", "sort_order"], name="idx_product_grid"),
            models.Index(fields=["branch", "barcode"], name="idx_product_barcode"),
        ]

    def __str__(self) -> str:
        return self.name_ar

    @property
    def default_variant(self):
        return self.variants.filter(is_default=True, is_active=True).first()


class ProductVariant(BaseModel, SoftDeletableModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    name_ar = models.CharField(max_length=100, blank=True, help_text="وسط / كبير. Blank if sole.")
    sku = models.CharField(max_length=32)
    price = models.DecimalField(**MONEY, validators=[MinValueValidator(Decimal("0"))])
    cost = models.DecimalField(
        **MONEY,
        default=Decimal("0"),
        help_text="Computed from the recipe. Never entered by hand.",
    )
    is_default = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "product_variants"
        ordering = ["sort_order", "name_ar"]
        constraints = [
            models.UniqueConstraint(fields=["product", "sku"], name="uniq_variant_sku_per_product"),
            models.UniqueConstraint(
                fields=["product"],
                condition=models.Q(is_default=True),
                name="one_default_variant_per_product",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product.name_ar} {self.name_ar}".strip()

    @property
    def margin(self) -> Decimal:
        return self.price - self.cost

    @property
    def margin_percent(self) -> Decimal:
        if self.price == 0:
            return Decimal("0")
        return ((self.price - self.cost) / self.price * 100).quantize(Decimal("0.01"))


class ModifierGroup(TenantScopedModel):
    name_ar = models.CharField(max_length=100)
    min_select = models.PositiveSmallIntegerField(default=0)
    max_select = models.PositiveSmallIntegerField(default=1)
    is_required = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "modifier_groups"
        ordering = ["sort_order"]

    def __str__(self) -> str:
        return self.name_ar


class Modifier(BaseModel, SoftDeletableModel):
    group = models.ForeignKey(ModifierGroup, on_delete=models.CASCADE, related_name="modifiers")
    name_ar = models.CharField(max_length=100)
    price_delta = models.DecimalField(**MONEY, default=Decimal("0"))

    inventory_item = models.ForeignKey(
        "inventory.InventoryItem",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="modifiers",
        help_text="Set when the modifier consumes stock, e.g. an extra shot.",
    )
    quantity_consumed = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0"))
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "modifiers"
        ordering = ["sort_order", "name_ar"]

    def __str__(self) -> str:
        return self.name_ar


class ProductModifierGroup(models.Model):
    id = models.BigAutoField(primary_key=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="modifier_groups")
    group = models.ForeignKey(ModifierGroup, on_delete=models.CASCADE, related_name="products")
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "product_modifier_groups"
        ordering = ["sort_order"]
        constraints = [
            models.UniqueConstraint(fields=["product", "group"], name="uniq_group_per_product")
        ]

    def __str__(self) -> str:
        return f"{self.product.name_ar} → {self.group.name_ar}"


class PriceHistory(models.Model):
    """
    Every price change, forever.

    A receipt is a legal record of what was sold at what price, so we keep the
    trail that explains why last Monday's total differs from today's.
    """

    id = models.BigAutoField(primary_key=True)
    variant = models.ForeignKey(
        ProductVariant, on_delete=models.CASCADE, related_name="price_history"
    )
    old_price = models.DecimalField(**MONEY)
    new_price = models.DecimalField(**MONEY)
    changed_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reason = models.CharField(max_length=200, blank=True)
    effective_from = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "price_history"
        ordering = ["-effective_from"]

    def __str__(self) -> str:
        return f"{self.variant}: {self.old_price} → {self.new_price}"
