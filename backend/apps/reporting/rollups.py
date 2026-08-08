"""
Building the daily rollups.

`build_day` is idempotent: running it again for the same date replaces that
date's rows rather than adding to them. That matters more than it sounds — a
Celery beat that fires twice, a manual backfill overlapping the nightly job, or
a retry after a timeout must not double a day's revenue.

Everything here reads the transactional tables and writes only rollup rows. The
rollups are a cache of arithmetic, and the arithmetic is defined in exactly one
place so a rebuild always reproduces the same numbers.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from apps.orders.models import ItemStatus, Order, OrderStatus
from apps.payments.models import Payment, Refund

from . import business_day
from .models import HourlyDaily, ProductDaily, SalesDaily

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")

#: Orders that represent money actually earned. A cancelled order is not a sale,
#: and a still-open one is not yet revenue.
SETTLED = (OrderStatus.PAID, OrderStatus.REFUNDED)


@transaction.atomic
def build_day(branch, business_date) -> SalesDaily:
    """
    Recompute one business day for one branch, from the transactional tables.

    Deliberately delete-then-insert rather than incremental: an incremental
    rollup that drifts is undetectable, and this is cheap enough at cafe volume
    that the simple, always-correct version wins.
    """
    boundary = business_day.boundary_for(branch)
    start, end = business_day.day_window(branch, business_date, boundary=boundary)

    orders = Order.objects.filter(branch=branch, opened_at__gte=start, opened_at__lt=end)
    settled = orders.filter(status__in=SETTLED)

    totals = settled.aggregate(
        gross=Sum("subtotal"),
        discounts=Sum("discount_total"),
        service=Sum("service_total"),
        tax=Sum("tax_total"),
        grand=Sum("grand_total"),
    )

    refunds = (
        Refund.objects.filter(
            order__branch=branch, created_at__gte=start, created_at__lt=end
        ).aggregate(total=Sum("amount"))["total"]
        or ZERO
    )

    payments = Payment.objects.filter(
        order__branch=branch, created_at__gte=start, created_at__lt=end
    ).select_related("method")
    cash = sum((p.amount for p in payments if p.method.counts_as_cash), ZERO)
    non_cash = sum((p.amount for p in payments if not p.method.counts_as_cash), ZERO)

    products = _build_products(branch, business_date, boundary, start, end)
    _build_hours(branch, business_date, boundary, settled)

    cogs = sum((row.cost for row in products), ZERO)
    order_count = settled.count()
    net = (totals["grand"] or ZERO) - refunds

    SalesDaily.objects.filter(branch=branch, business_date=business_date).delete()
    row = SalesDaily.objects.create(
        branch=branch,
        business_date=business_date,
        boundary=boundary,
        gross_sales=totals["gross"] or ZERO,
        discounts=totals["discounts"] or ZERO,
        service=totals["service"] or ZERO,
        tax=totals["tax"] or ZERO,
        refunds=refunds,
        net_sales=net,
        cash_sales=cash,
        non_cash_sales=non_cash,
        cogs=cogs,
        gross_profit=net - cogs,
        order_count=order_count,
        void_count=orders.filter(status=OrderStatus.CANCELLED).count(),
        item_count=sum((row.quantity for row in products), Decimal("0")),
        average_ticket=(
            ((totals["grand"] or ZERO) / order_count).quantize(Decimal("0.01"))
            if order_count
            else ZERO
        ),
    )

    logger.info(
        "Rollup built",
        extra={
            "branch": branch.code,
            "date": str(business_date),
            "orders": order_count,
            "net": str(net),
        },
    )
    return row


def _build_products(branch, business_date, boundary, start, end) -> list[ProductDaily]:
    from apps.orders.models import OrderItem

    items = (
        OrderItem.objects.filter(
            order__branch=branch,
            order__status__in=SETTLED,
            order__opened_at__gte=start,
            order__opened_at__lt=end,
        )
        .select_related("variant", "variant__product", "variant__product__category")
        .prefetch_related("modifiers")
    )

    buckets: dict = {}
    for item in items:
        bucket = buckets.setdefault(
            item.variant_id,
            {
                "variant": item.variant,
                "category": item.variant.product.category,
                # Snapshotted, like the receipt: a rename must not rewrite what
                # last month's report says was sold.
                "name": item.name_snapshot,
                "quantity": Decimal("0"),
                "revenue": ZERO,
                "cost": ZERO,
                "voids": 0,
            },
        )
        if item.status == ItemStatus.VOIDED:
            bucket["voids"] += 1
            continue

        bucket["quantity"] += item.quantity
        bucket["revenue"] += item.line_total
        bucket["cost"] += (item.cost_snapshot * item.quantity).quantize(Decimal("0.01"))

    ProductDaily.objects.filter(branch=branch, business_date=business_date).delete()
    rows = [
        ProductDaily(
            branch=branch,
            business_date=business_date,
            boundary=boundary,
            variant=data["variant"],
            category=data["category"],
            name_snapshot=data["name"],
            quantity=data["quantity"],
            revenue=data["revenue"],
            cost=data["cost"],
            profit=data["revenue"] - data["cost"],
            void_count=data["voids"],
        )
        for data in buckets.values()
    ]
    return ProductDaily.objects.bulk_create(rows)


def _build_hours(branch, business_date, boundary, settled) -> None:
    from django.utils import timezone

    buckets: dict[int, dict] = {}
    for order in settled.only("opened_at", "grand_total"):
        hour = timezone.localtime(order.opened_at).hour
        bucket = buckets.setdefault(hour, {"orders": 0, "net": ZERO})
        bucket["orders"] += 1
        bucket["net"] += order.grand_total

    HourlyDaily.objects.filter(branch=branch, business_date=business_date).delete()
    HourlyDaily.objects.bulk_create(
        [
            HourlyDaily(
                branch=branch,
                business_date=business_date,
                boundary=boundary,
                hour=hour,
                order_count=data["orders"],
                net_sales=data["net"],
            )
            for hour, data in buckets.items()
        ]
    )


def backfill(branch, date_from, date_to) -> int:
    """Rebuild a range. Used after a fold fix, or to seed a new deployment."""
    built = 0
    for day in business_day.split_days(date_from, date_to):
        build_day(branch, day)
        built += 1
    return built


def build_yesterday(branch) -> SalesDaily:
    """
    What the nightly job runs.

    Yesterday rather than today because today is still open — rolling up a day
    in progress would produce a row that is wrong the moment the next order
    lands, and a wrong cached number is worse than no cached number.
    """
    from datetime import timedelta

    return build_day(branch, business_day.today(branch) - timedelta(days=1))
