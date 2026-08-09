"""
Materialized daily rollups.

A twelve-month product report that scans every order line is a report nobody
runs twice. The rollups are built once per closed business day and every ad-hoc
range reads them, touching raw tables only for the day still in progress.

Two properties make that safe rather than merely fast:

  * **A rollup is derived, never authoritative.** It can be deleted and rebuilt
    from the transactional tables at any time, and a test asserts the rebuilt
    numbers match the raw ones exactly. If a rollup and the ledger ever disagree,
    the ledger is right and the rollup is a bug.

  * **Each row records the boundary it was computed under.** Changing
    `finance.business_day_start` must not silently re-cut last month; the old
    rows keep their label and their meaning.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models

from apps.core.precision import QUANTITY
from apps.core.precision import WIDE_MONEY as MONEY


class RollupBase(models.Model):
    branch = models.ForeignKey("organizations.Branch", on_delete=models.CASCADE, related_name="+")
    business_date = models.DateField(db_index=True)
    boundary = models.TimeField(
        help_text="The finance.business_day_start this row was computed under (A5)."
    )
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SalesDaily(RollupBase):
    """One branch, one business day, everything the money reports need."""

    id = models.BigAutoField(primary_key=True)

    gross_sales = models.DecimalField(**MONEY, default=Decimal("0"))
    """Sum of line nets before order-level discount — what was rung up."""
    discounts = models.DecimalField(**MONEY, default=Decimal("0"))
    service = models.DecimalField(**MONEY, default=Decimal("0"))
    tax = models.DecimalField(**MONEY, default=Decimal("0"))
    net_sales = models.DecimalField(**MONEY, default=Decimal("0"))
    """Grand totals of settled orders, minus refunds. What the business earned."""
    refunds = models.DecimalField(**MONEY, default=Decimal("0"))

    cash_sales = models.DecimalField(**MONEY, default=Decimal("0"))
    non_cash_sales = models.DecimalField(**MONEY, default=Decimal("0"))

    cogs = models.DecimalField(
        **MONEY, default=Decimal("0"), help_text="Recipe cost of what was sold."
    )
    gross_profit = models.DecimalField(**MONEY, default=Decimal("0"))

    order_count = models.PositiveIntegerField(default=0)
    void_count = models.PositiveIntegerField(default=0)
    item_count = models.DecimalField(**QUANTITY, default=Decimal("0"))
    average_ticket = models.DecimalField(**MONEY, default=Decimal("0"))

    class Meta:
        db_table = "rollup_sales_daily"
        ordering = ["-business_date"]
        constraints = [
            models.UniqueConstraint(fields=["branch", "business_date"], name="uniq_sales_daily")
        ]

    def __str__(self) -> str:
        return f"{self.branch_id} {self.business_date}: {self.net_sales}"


class ProductDaily(RollupBase):
    """Per-variant sales for the product and profitability reports."""

    id = models.BigAutoField(primary_key=True)
    variant = models.ForeignKey(
        "catalog.ProductVariant", on_delete=models.CASCADE, related_name="+"
    )
    category = models.ForeignKey(
        "catalog.Category", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    name_snapshot = models.CharField(
        max_length=250,
        help_text="As sold. A later rename must not rewrite what last month's report says.",
    )
    quantity = models.DecimalField(**QUANTITY, default=Decimal("0"))
    revenue = models.DecimalField(**MONEY, default=Decimal("0"))
    cost = models.DecimalField(**MONEY, default=Decimal("0"))
    profit = models.DecimalField(**MONEY, default=Decimal("0"))
    void_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "rollup_product_daily"
        ordering = ["-business_date", "-revenue"]
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "business_date", "variant"], name="uniq_product_daily"
            )
        ]
        indexes = [
            models.Index(fields=["branch", "business_date"], name="idx_product_daily_day"),
        ]

    def __str__(self) -> str:
        return f"{self.business_date} {self.name_snapshot}: {self.revenue}"


class HourlyDaily(RollupBase):
    """
    Sales per clock hour — the peak-staffing report.

    Bucketed by the LOCAL hour an order was opened, not by the business day
    offset, because "we are busy at 9pm" is a statement about the clock on the
    wall.
    """

    id = models.BigAutoField(primary_key=True)
    hour = models.PositiveSmallIntegerField()

    order_count = models.PositiveIntegerField(default=0)
    net_sales = models.DecimalField(**MONEY, default=Decimal("0"))

    class Meta:
        db_table = "rollup_hourly_daily"
        ordering = ["business_date", "hour"]
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "business_date", "hour"], name="uniq_hourly_daily"
            )
        ]

    def __str__(self) -> str:
        return f"{self.business_date} {self.hour:02d}:00 → {self.net_sales}"
