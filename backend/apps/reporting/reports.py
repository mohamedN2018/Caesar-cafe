"""
The reports.

One rule runs through all of them: **closed days come from rollups, the day
still in progress comes from the raw tables.** A report that read only rollups
would show an owner nothing until tomorrow; one that read only raw tables would
take a minute to answer a year-long question. `_ensure_rollups` is where that
seam lives, and it is the only place either decision is made.

The loss-prevention pair — void rates per user and cash variance by user — are
reported per person on purpose. Neither number accuses anybody of anything; both
are the questions a manager should be asking, and the system's job is to make
them cheap to ask rather than to draw conclusions.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum

from apps.orders.models import ItemStatus, Order, OrderStatus
from apps.payments.models import Payment, Refund

from . import business_day, rollups
from .models import HourlyDaily, ProductDaily, SalesDaily

ZERO = Decimal("0.00")


def _money(value) -> str:
    return str((value or ZERO).quantize(Decimal("0.01")))


def _ensure_rollups(branch, date_from: date, date_to: date) -> date:
    """
    Build any missing CLOSED day in the range, and return the first date that
    must still be read raw.

    Today is never rolled up: a row for a day in progress is wrong the moment
    the next order lands, and a wrong cached number is worse than none.
    """
    today = business_day.today(branch)
    last_closed = min(date_to, today - timedelta(days=1))

    if last_closed >= date_from:
        have = set(
            SalesDaily.objects.filter(
                branch=branch, business_date__range=(date_from, last_closed)
            ).values_list("business_date", flat=True)
        )
        for day in business_day.split_days(date_from, last_closed):
            if day not in have:
                rollups.build_day(branch, day)

    return max(date_from, today)


# ── sales ────────────────────────────────────────────────────────────────────


def sales_summary(branch, date_from: date, date_to: date) -> dict:
    raw_from = _ensure_rollups(branch, date_from, date_to)

    rolled = SalesDaily.objects.filter(
        branch=branch, business_date__range=(date_from, min(date_to, raw_from - timedelta(days=1)))
    ).aggregate(
        gross=Sum("gross_sales"),
        discounts=Sum("discounts"),
        service=Sum("service"),
        tax=Sum("tax"),
        refunds=Sum("refunds"),
        net=Sum("net_sales"),
        cash=Sum("cash_sales"),
        non_cash=Sum("non_cash_sales"),
        cogs=Sum("cogs"),
        orders=Sum("order_count"),
        voids=Sum("void_count"),
    )

    live = _live_totals(branch, raw_from, date_to)

    gross = (rolled["gross"] or ZERO) + live["gross"]
    net = (rolled["net"] or ZERO) + live["net"]
    cogs = (rolled["cogs"] or ZERO) + live["cogs"]
    orders = (rolled["orders"] or 0) + live["orders"]

    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "boundary": business_day.boundary_for(branch).strftime("%H:%M"),
        "gross_sales": _money(gross),
        "discounts": _money((rolled["discounts"] or ZERO) + live["discounts"]),
        "service": _money((rolled["service"] or ZERO) + live["service"]),
        "tax": _money((rolled["tax"] or ZERO) + live["tax"]),
        "refunds": _money((rolled["refunds"] or ZERO) + live["refunds"]),
        "net_sales": _money(net),
        "cash_sales": _money((rolled["cash"] or ZERO) + live["cash"]),
        "non_cash_sales": _money((rolled["non_cash"] or ZERO) + live["non_cash"]),
        "cogs": _money(cogs),
        "gross_profit": _money(net - cogs),
        "margin_percent": str(
            ((net - cogs) / net * 100).quantize(Decimal("0.01")) if net else ZERO
        ),
        "order_count": orders,
        "void_count": (rolled["voids"] or 0) + live["voids"],
        "average_ticket": _money(net / orders) if orders else "0.00",
    }


def _live_totals(branch, raw_from: date, date_to: date) -> dict:
    """The still-open day, straight from the transactional tables."""
    empty = {
        "gross": ZERO,
        "discounts": ZERO,
        "service": ZERO,
        "tax": ZERO,
        "refunds": ZERO,
        "net": ZERO,
        "cash": ZERO,
        "non_cash": ZERO,
        "cogs": ZERO,
        "orders": 0,
        "voids": 0,
    }
    if raw_from > date_to:
        return empty

    start, end = business_day.range_window(branch, raw_from, date_to)
    orders = Order.objects.filter(branch=branch, opened_at__gte=start, opened_at__lt=end)
    settled = orders.filter(status__in=rollups.SETTLED)

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

    cogs = ZERO
    for item in _settled_items(branch, start, end):
        cogs += (item.cost_snapshot * item.quantity).quantize(Decimal("0.01"))

    return {
        "gross": totals["gross"] or ZERO,
        "discounts": totals["discounts"] or ZERO,
        "service": totals["service"] or ZERO,
        "tax": totals["tax"] or ZERO,
        "refunds": refunds,
        "net": (totals["grand"] or ZERO) - refunds,
        "cash": sum((p.amount for p in payments if p.method.counts_as_cash), ZERO),
        "non_cash": sum((p.amount for p in payments if not p.method.counts_as_cash), ZERO),
        "cogs": cogs,
        "orders": settled.count(),
        "voids": orders.filter(status=OrderStatus.CANCELLED).count(),
    }


def _settled_items(branch, start, end):
    from apps.orders.models import OrderItem

    return OrderItem.objects.filter(
        order__branch=branch,
        order__status__in=rollups.SETTLED,
        order__opened_at__gte=start,
        order__opened_at__lt=end,
        status=ItemStatus.ACTIVE,
    ).select_related("variant", "variant__product", "variant__product__category")


def sales_by_hour(branch, date_from: date, date_to: date) -> dict:
    """Peak-hour staffing. Bucketed by the clock on the wall, not the day offset."""
    raw_from = _ensure_rollups(branch, date_from, date_to)

    buckets = {hour: {"orders": 0, "net": ZERO} for hour in range(24)}

    for row in HourlyDaily.objects.filter(
        branch=branch, business_date__range=(date_from, min(date_to, raw_from - timedelta(days=1)))
    ):
        buckets[row.hour]["orders"] += row.order_count
        buckets[row.hour]["net"] += row.net_sales

    if raw_from <= date_to:
        from django.utils import timezone

        start, end = business_day.range_window(branch, raw_from, date_to)
        for order in Order.objects.filter(
            branch=branch,
            status__in=rollups.SETTLED,
            opened_at__gte=start,
            opened_at__lt=end,
        ).only("opened_at", "grand_total"):
            hour = timezone.localtime(order.opened_at).hour
            buckets[hour]["orders"] += 1
            buckets[hour]["net"] += order.grand_total

    peak = (
        max(buckets, key=lambda h: buckets[h]["net"])
        if any(b["orders"] for b in buckets.values())
        else None
    )

    return {
        "peak_hour": peak,
        "hours": [
            {"hour": hour, "order_count": data["orders"], "net_sales": _money(data["net"])}
            for hour, data in sorted(buckets.items())
        ],
    }


def sales_by_category(branch, date_from: date, date_to: date) -> dict:
    rows = _product_rows(branch, date_from, date_to)

    buckets: dict = {}
    for row in rows:
        key = row["category_name"]
        bucket = buckets.setdefault(key, {"quantity": Decimal("0"), "revenue": ZERO, "cost": ZERO})
        bucket["quantity"] += row["quantity"]
        bucket["revenue"] += row["revenue"]
        bucket["cost"] += row["cost"]

    total = sum((b["revenue"] for b in buckets.values()), ZERO)

    return {
        "total_revenue": _money(total),
        "categories": sorted(
            (
                {
                    "category": name,
                    "quantity": str(data["quantity"]),
                    "revenue": _money(data["revenue"]),
                    "profit": _money(data["revenue"] - data["cost"]),
                    "share_percent": str(
                        (data["revenue"] / total * 100).quantize(Decimal("0.01")) if total else ZERO
                    ),
                }
                for name, data in buckets.items()
            ),
            key=lambda r: Decimal(r["revenue"]),
            reverse=True,
        ),
    }


def sales_by_payment_method(branch, date_from: date, date_to: date) -> dict:
    """
    Cash-versus-card reconciliation.

    Reads payments directly rather than the rollup's cash/non-cash split,
    because the useful version of this report names each method — "which of the
    two card machines took this?" is the question that gets asked.
    """
    start, end = business_day.range_window(branch, date_from, date_to)

    buckets: dict = {}
    for payment in Payment.objects.filter(
        order__branch=branch, created_at__gte=start, created_at__lt=end
    ).select_related("method"):
        bucket = buckets.setdefault(
            payment.method.name_ar,
            {"count": 0, "amount": ZERO, "counts_as_cash": payment.method.counts_as_cash},
        )
        bucket["count"] += 1
        bucket["amount"] += payment.amount

    return {
        "methods": [
            {
                "method": name,
                "count": data["count"],
                "amount": _money(data["amount"]),
                "counts_as_cash": data["counts_as_cash"],
            }
            for name, data in sorted(buckets.items(), key=lambda kv: -kv[1]["amount"])
        ],
        "total": _money(sum((b["amount"] for b in buckets.values()), ZERO)),
    }


# ── products ─────────────────────────────────────────────────────────────────


def _product_rows(branch, date_from: date, date_to: date) -> list[dict]:
    """Merged rollup + live rows, keyed by variant."""
    raw_from = _ensure_rollups(branch, date_from, date_to)
    merged: dict = {}

    for row in ProductDaily.objects.filter(
        branch=branch, business_date__range=(date_from, min(date_to, raw_from - timedelta(days=1)))
    ).select_related("category"):
        bucket = merged.setdefault(
            row.variant_id,
            {
                "variant_id": str(row.variant_id),
                "name": row.name_snapshot,
                "category_name": row.category.name_ar if row.category else "—",
                "quantity": Decimal("0"),
                "revenue": ZERO,
                "cost": ZERO,
                "voids": 0,
            },
        )
        bucket["quantity"] += row.quantity
        bucket["revenue"] += row.revenue
        bucket["cost"] += row.cost
        bucket["voids"] += row.void_count

    if raw_from <= date_to:
        start, end = business_day.range_window(branch, raw_from, date_to)
        for item in _settled_items(branch, start, end):
            bucket = merged.setdefault(
                item.variant_id,
                {
                    "variant_id": str(item.variant_id),
                    "name": item.name_snapshot,
                    "category_name": (
                        item.variant.product.category.name_ar
                        if item.variant.product.category
                        else "—"
                    ),
                    "quantity": Decimal("0"),
                    "revenue": ZERO,
                    "cost": ZERO,
                    "voids": 0,
                },
            )
            bucket["quantity"] += item.quantity
            bucket["revenue"] += item.line_total
            bucket["cost"] += (item.cost_snapshot * item.quantity).quantize(Decimal("0.01"))

    return list(merged.values())


def products_top(branch, date_from: date, date_to: date, *, limit: int = 20) -> dict:
    """
    Best and worst together, in one call.

    The worst sellers are the more actionable half — a product nobody orders is
    stock sitting in a fridge and a line on a menu doing nothing.
    """
    rows = sorted(_product_rows(branch, date_from, date_to), key=lambda r: r["revenue"])

    def present(row: dict) -> dict:
        return {
            "variant_id": row["variant_id"],
            "name": row["name"],
            "category": row["category_name"],
            "quantity": str(row["quantity"]),
            "revenue": _money(row["revenue"]),
            "profit": _money(row["revenue"] - row["cost"]),
            "void_count": row["voids"],
        }

    return {
        "top": [present(row) for row in reversed(rows[-limit:])],
        "bottom": [present(row) for row in rows[:limit]],
        "product_count": len(rows),
    }


def products_profitability(branch, date_from: date, date_to: date) -> dict:
    """
    Revenue minus recipe cost, per product.

    Margin percent, not just profit: the highest-revenue item is often not the
    one worth promoting, and that gap is the entire point of this report.
    """
    rows = []
    for row in _product_rows(branch, date_from, date_to):
        profit = row["revenue"] - row["cost"]
        rows.append(
            {
                "variant_id": row["variant_id"],
                "name": row["name"],
                "category": row["category_name"],
                "quantity": str(row["quantity"]),
                "revenue": _money(row["revenue"]),
                "cost": _money(row["cost"]),
                "profit": _money(profit),
                "margin_percent": str(
                    (profit / row["revenue"] * 100).quantize(Decimal("0.01"))
                    if row["revenue"]
                    else ZERO
                ),
            }
        )
    return {"products": sorted(rows, key=lambda r: Decimal(r["profit"]), reverse=True)}


# ── inventory ────────────────────────────────────────────────────────────────


def inventory_movements(branch, date_from: date, date_to: date, *, limit: int = 500) -> dict:
    from apps.inventory.models import StockMovement

    start, end = business_day.range_window(branch, date_from, date_to)
    movements = (
        StockMovement.objects.filter(branch=branch, occurred_at__gte=start, occurred_at__lt=end)
        .select_related("item", "item__base_unit", "user")
        .order_by("-occurred_at")[:limit]
    )

    return {
        "movements": [
            {
                "id": str(m.id),
                "item": m.item.name_ar,
                "item_code": m.item.code,
                "type": m.movement_type,
                "quantity_delta": str(m.quantity_delta),
                "unit": m.item.base_unit.code,
                "balance_after": str(m.balance_after),
                "unit_cost": str(m.unit_cost),
                "reason": m.reason,
                "user": m.user.full_name_ar if m.user else None,
                "occurred_at": m.occurred_at.isoformat(),
            }
            for m in movements
        ]
    }


def inventory_waste(branch, date_from: date, date_to: date) -> dict:
    from apps.inventory.models import MovementType, StockMovement

    start, end = business_day.range_window(branch, date_from, date_to)
    movements = StockMovement.objects.filter(
        branch=branch,
        movement_type=MovementType.WASTE,
        occurred_at__gte=start,
        occurred_at__lt=end,
    ).select_related("item", "user")

    buckets: dict = {}
    for movement in movements:
        bucket = buckets.setdefault(
            movement.item_id,
            {"item": movement.item.name_ar, "quantity": Decimal("0"), "value": ZERO, "count": 0},
        )
        quantity = abs(movement.quantity_delta)
        bucket["quantity"] += quantity
        bucket["value"] += (quantity * movement.unit_cost).quantize(Decimal("0.01"))
        bucket["count"] += 1

    by_user: dict = {}
    for movement in movements:
        name = movement.user.full_name_ar if movement.user else "—"
        by_user[name] = by_user.get(name, 0) + 1

    return {
        "total_value": _money(sum((b["value"] for b in buckets.values()), ZERO)),
        "items": sorted(
            (
                {
                    "item": data["item"],
                    "quantity": str(data["quantity"]),
                    "value": _money(data["value"]),
                    "events": data["count"],
                }
                for data in buckets.values()
            ),
            key=lambda r: Decimal(r["value"]),
            reverse=True,
        ),
        "by_user": [{"user": name, "events": count} for name, count in sorted(by_user.items())],
    }


def inventory_variance(branch, date_from: date, date_to: date) -> dict:
    """
    Theoretical versus counted — the shrinkage report.

    The one report whose findings are uncomfortable, so it is deliberately plain
    about what it is showing: a variance is a difference, not an accusation, and
    the reasons are usually a mis-scaled recipe or a miscount before they are
    anything worse.
    """
    from apps.inventory.models import CountLine, CountStatus, StockCount

    start, end = business_day.range_window(branch, date_from, date_to)
    counts = StockCount.objects.filter(
        branch=branch,
        status=CountStatus.POSTED,
        posted_at__gte=start,
        posted_at__lt=end,
    )

    lines = (
        CountLine.objects.filter(count__in=counts, counted_quantity__isnull=False)
        .select_related("item", "count")
        .order_by("item__name_ar")
    )

    rows = []
    total_value = ZERO
    for line in lines:
        variance = line.variance or Decimal("0")
        if variance == 0:
            continue
        value = (variance * line.item.level.weighted_avg_cost).quantize(Decimal("0.01"))
        total_value += value
        rows.append(
            {
                "item": line.item.name_ar,
                "item_code": line.item.code,
                "count_reference": line.count.reference,
                "system_quantity": str(line.system_quantity),
                "counted_quantity": str(line.counted_quantity),
                "variance": str(variance),
                "value": _money(value),
                "reason": line.reason,
            }
        )

    return {
        "counts": counts.count(),
        "lines_with_variance": len(rows),
        "net_value": _money(total_value),
        "shrinkage_value": _money(
            sum((Decimal(r["value"]) for r in rows if Decimal(r["value"]) < 0), ZERO)
        ),
        "items": sorted(rows, key=lambda r: Decimal(r["value"])),
    }


# ── purchasing ───────────────────────────────────────────────────────────────


def purchases_summary(branch, date_from: date, date_to: date) -> dict:
    from apps.purchasing.models import GoodsReceipt, PurchaseOrder

    start, end = business_day.range_window(branch, date_from, date_to)

    orders = PurchaseOrder.objects.filter(branch=branch, created_at__gte=start, created_at__lt=end)
    receipts = GoodsReceipt.objects.filter(
        branch=branch, created_at__gte=start, created_at__lt=end
    ).select_related("supplier")

    by_supplier: dict = {}
    for receipt in receipts:
        name = receipt.supplier.name
        bucket = by_supplier.setdefault(name, {"receipts": 0, "value": ZERO})
        bucket["receipts"] += 1
        bucket["value"] += receipt.grand_total

    return {
        "purchase_orders": orders.count(),
        "goods_receipts": receipts.count(),
        "total_received": _money(sum((b["value"] for b in by_supplier.values()), ZERO)),
        "by_supplier": [
            {"supplier": name, "receipts": data["receipts"], "value": _money(data["value"])}
            for name, data in sorted(by_supplier.items(), key=lambda kv: -kv[1]["value"])
        ],
    }


def supplier_balances(branch) -> dict:
    from apps.suppliers.models import Supplier

    suppliers = Supplier.objects.filter(branch=branch, is_active=True)
    rows = [
        {
            "supplier_id": str(s.id),
            "name": s.name,
            "phone": s.phone,
            "balance": _money(s.current_balance),
        }
        for s in suppliers
    ]
    return {
        "suppliers": sorted(rows, key=lambda r: Decimal(r["balance"]), reverse=True),
        "total_owed": _money(sum((Decimal(r["balance"]) for r in rows), ZERO)),
    }


# ── people ───────────────────────────────────────────────────────────────────


def employees_sales(branch, date_from: date, date_to: date) -> dict:
    start, end = business_day.range_window(branch, date_from, date_to)

    rows = (
        Order.objects.filter(
            branch=branch,
            status__in=rollups.SETTLED,
            opened_at__gte=start,
            opened_at__lt=end,
            opened_by__isnull=False,
        )
        .values("opened_by", "opened_by__full_name_ar")
        .annotate(orders=Count("id"), net=Sum("grand_total"))
        .order_by("-net")
    )

    return {
        "employees": [
            {
                "user_id": str(row["opened_by"]),
                "name": row["opened_by__full_name_ar"],
                "order_count": row["orders"],
                "net_sales": _money(row["net"]),
                "average_ticket": _money(row["net"] / row["orders"]) if row["orders"] else "0.00",
            }
            for row in rows
        ]
    }


def employees_voids(branch, date_from: date, date_to: date) -> dict:
    """
    Void and discount rates per user.

    Half of the loss-prevention pair. A rate, not a count: a cashier who ran
    three times as many orders will void more, and comparing raw counts would
    point at the busiest person rather than the interesting one.
    """
    from apps.orders.models import OrderItem

    start, end = business_day.range_window(branch, date_from, date_to)

    totals = (
        Order.objects.filter(
            branch=branch, opened_at__gte=start, opened_at__lt=end, opened_by__isnull=False
        )
        .values("opened_by", "opened_by__full_name_ar")
        .annotate(
            orders=Count("id"),
            voided_orders=Count("id", filter=Q(status=OrderStatus.CANCELLED)),
            discounted=Count("id", filter=Q(discount_percent__gt=0)),
        )
    )

    voided_items = dict(
        OrderItem.objects.filter(
            order__branch=branch,
            order__opened_at__gte=start,
            order__opened_at__lt=end,
            status=ItemStatus.VOIDED,
        )
        .values_list("order__opened_by")
        .annotate(count=Count("id"))
    )

    rows = []
    for row in totals:
        orders = row["orders"] or 1
        rows.append(
            {
                "user_id": str(row["opened_by"]),
                "name": row["opened_by__full_name_ar"],
                "order_count": row["orders"],
                "voided_orders": row["voided_orders"],
                "voided_items": voided_items.get(row["opened_by"], 0),
                "discounted_orders": row["discounted"],
                "void_rate_percent": str(
                    (Decimal(row["voided_orders"]) / orders * 100).quantize(Decimal("0.01"))
                ),
                "discount_rate_percent": str(
                    (Decimal(row["discounted"]) / orders * 100).quantize(Decimal("0.01"))
                ),
            }
        )

    return {
        "employees": sorted(rows, key=lambda r: Decimal(r["void_rate_percent"]), reverse=True),
        "note_ar": "المعدل وليس العدد — الكاشير الأكثر عملاً سيلغي أكثر بطبيعة الحال.",
    }


def shift_variance(branch, date_from: date, date_to: date) -> dict:
    """
    Cash variance by user, over time.

    The other half of the pair. One bad night is a mistake; a consistent
    direction is a pattern, so both the total and the run of individual closes
    are returned.
    """
    from apps.shifts.models import Shift, ShiftStatus

    start, end = business_day.range_window(branch, date_from, date_to)
    shifts = (
        Shift.objects.filter(
            branch=branch,
            status=ShiftStatus.CLOSED,
            closed_at__gte=start,
            closed_at__lt=end,
        )
        .select_related("user")
        .order_by("closed_at")
    )

    by_user: dict = {}
    closes = []
    for shift in shifts:
        name = shift.user.full_name_ar if shift.user else "—"
        bucket = by_user.setdefault(name, {"shifts": 0, "total": ZERO, "worst": ZERO})
        bucket["shifts"] += 1
        bucket["total"] += shift.variance
        bucket["worst"] = min(bucket["worst"], shift.variance)

        closes.append(
            {
                "shift_id": str(shift.id),
                "user": name,
                "closed_at": shift.closed_at.isoformat(),
                "variance": _money(shift.variance),
                "reason": shift.variance_reason,
            }
        )

    return {
        "closes": closes,
        "by_user": [
            {
                "user": name,
                "shifts": data["shifts"],
                "total_variance": _money(data["total"]),
                "average_variance": _money(data["total"] / data["shifts"]),
                "worst_variance": _money(data["worst"]),
            }
            for name, data in sorted(by_user.items(), key=lambda kv: kv[1]["total"])
        ],
    }


# ── financial ────────────────────────────────────────────────────────────────


def profit_and_loss(branch, date_from: date, date_to: date) -> dict:
    """
    Net sales − COGS = gross profit.

    Deliberately NOT called a P&L in the accounting sense: it stops at gross
    profit because the system knows what was sold and what it cost to make, and
    knows nothing about rent, salaries or electricity. Presenting a number that
    looked like net profit while omitting the largest costs would be worse than
    useless.
    """
    summary = sales_summary(branch, date_from, date_to)
    waste = inventory_waste(branch, date_from, date_to)

    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "net_sales": summary["net_sales"],
        "cogs": summary["cogs"],
        "gross_profit": summary["gross_profit"],
        "margin_percent": summary["margin_percent"],
        "waste_value": waste["total_value"],
        "refunds": summary["refunds"],
        "discounts": summary["discounts"],
        "tax_collected": summary["tax"],
        "service_collected": summary["service"],
        "scope_note_ar": (
            "يتوقف عند مجمل الربح: النظام يعرف تكلفة ما بِيع، ولا يعرف الإيجار والرواتب والكهرباء."
        ),
    }


# ── dashboard ────────────────────────────────────────────────────────────────


def dashboard(branch) -> dict:
    """
    Everything the home screen needs, in one call.

    One call because the owner opens this on a phone over a mobile connection,
    and eight round-trips to render one screen is the difference between a
    dashboard they check and one they stop opening.
    """
    from apps.kids import services as kids_services
    from apps.kitchen.models import OPEN_STATUSES, KitchenTicket
    from apps.shifts.models import Shift, ShiftStatus

    today = business_day.today(branch)
    yesterday = today - timedelta(days=1)
    week_start = today - timedelta(days=6)

    today_summary = sales_summary(branch, today, today)
    yesterday_summary = sales_summary(branch, yesterday, yesterday)
    week = sales_summary(branch, week_start, today)

    net_today = Decimal(today_summary["net_sales"])
    net_yesterday = Decimal(yesterday_summary["net_sales"])
    change = (
        ((net_today - net_yesterday) / net_yesterday * 100).quantize(Decimal("0.01"))
        if net_yesterday
        else None
    )

    open_orders = Order.objects.filter(
        branch=branch,
        status__in=[
            OrderStatus.OPEN,
            OrderStatus.IN_KITCHEN,
            OrderStatus.READY,
            OrderStatus.SERVED,
        ],
    )

    return {
        "business_date": today.isoformat(),
        "boundary": business_day.boundary_for(branch).strftime("%H:%M"),
        "today": today_summary,
        "yesterday_net": yesterday_summary["net_sales"],
        "change_percent": str(change) if change is not None else None,
        "week": {
            "net_sales": week["net_sales"],
            "order_count": week["order_count"],
            "average_ticket": week["average_ticket"],
        },
        "open_orders": open_orders.count(),
        "open_orders_value": _money(
            open_orders.aggregate(total=Sum("grand_total"))["total"] or ZERO
        ),
        "open_tickets": KitchenTicket.objects.filter(
            branch=branch, status__in=OPEN_STATUSES
        ).count(),
        "open_shifts": Shift.objects.filter(branch=branch, status=ShiftStatus.OPEN).count(),
        "kids_inside": len(kids_services.outstanding_sessions(branch)),
        "top_products": products_top(branch, today, today, limit=5)["top"],
        "by_hour": sales_by_hour(branch, today, today)["hours"],
    }
