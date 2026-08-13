"""
Reports, rollups, and the business day.

The load-bearing test is `TestReconciliation`: a rollup is a cache of
arithmetic, so the only thing that makes it trustworthy is that rebuilding it
reproduces what the transactional tables say. If a rollup and the ledger ever
disagree, the ledger is right and the rollup is a bug — and this file is where
that would be caught.

`TestBusinessDay` is the second: an off-by-one-day report is the classic
reporting bug, invisible until somebody reconciles a month by hand.
"""

from __future__ import annotations

import itertools
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.catalog.models import Category, Product, ProductVariant
from apps.configuration import resolver as config_resolver
from apps.configuration.registry import Scope
from apps.inventory.models import InventoryItem, MovementType, Unit
from apps.orders import services as order_services
from apps.orders.models import EventType, Order, OrderStatus
from apps.payments import services as payment_services
from apps.payments.models import Payment, PaymentMethod
from apps.reporting import business_day, exports, reports, rollups
from apps.reporting.models import HourlyDaily, ProductDaily, SalesDaily

pytestmark = pytest.mark.django_db


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def menu(organization, branch):
    category = Category.objects.create(organization=organization, branch=branch, name_ar="مشروبات")
    dessert = Category.objects.create(organization=organization, branch=branch, name_ar="حلويات")

    def make(sku, name, price, cost, cat):
        product = Product.objects.create(
            organization=organization, branch=branch, category=cat, sku=sku, name_ar=name
        )
        return ProductVariant.objects.create(
            product=product,
            sku=f"{sku}-D",
            price=Decimal(price),
            cost=Decimal(cost),
            is_default=True,
        )

    return {
        "cappuccino": make("CAPP", "كابتشينو", "60.00", "18.00", category),
        "tea": make("TEA", "شاي", "25.00", "4.00", category),
        "cake": make("CAKE", "تشيز كيك", "75.00", "30.00", dessert),
    }


@pytest.fixture
def cash(organization, branch) -> PaymentMethod:
    # `counts_as_cash` defaults to False, deliberately: a new payment method an
    # admin invents is not assumed to end up in the drawer. Cash has to say so.
    return PaymentMethod.objects.create(
        organization=organization,
        branch=branch,
        code="CASH",
        name_ar="نقدي",
        counts_as_cash=True,
    )


@pytest.fixture
def card(organization, branch) -> PaymentMethod:
    return PaymentMethod.objects.create(
        organization=organization,
        branch=branch,
        code="CARD",
        name_ar="بطاقة",
        counts_as_cash=False,
    )


@pytest.fixture(autouse=True)
def _no_tax(branch):
    """
    VAT and service off for most of this file.

    Not because they do not matter — they have their own tests in test_money —
    but because a report test that also exercises tax arithmetic tells you two
    things at once and neither one clearly.
    """
    config_resolver.set_value("finance.vat_enabled", False, scope=Scope.BRANCH, scope_id=branch.id)
    config_resolver.set_value(
        "finance.service_enabled", False, scope=Scope.BRANCH, scope_id=branch.id
    )


#: `_next_local_number` counts TODAY's orders, so back-dating one and then
#: opening another produces the same number twice. Real terminals never do this;
#: only a test that rewrites `opened_at` can. A counter keeps them distinct.
_sequence = itertools.count(1)


@pytest.fixture
def at_hour(monkeypatch):
    """
    Pin how far into the business day "now" is.

    The same-hour comparison reads the wall clock, so a test that does not fix
    it passes or fails depending on when the suite runs — which is the worst
    kind of flake, because it looks like a real regression at 3am and like
    nothing at noon.

    The hour given is an OFFSET from the branch's business-day boundary, since
    that is what the comparison actually measures.
    """

    def _set(hours_into_the_day: int):
        from apps.reporting import business_day

        def _now(branch):
            start, _ = business_day.day_window(branch, business_day.today(branch))
            return start + timedelta(hours=hours_into_the_day)

        # `_net_up_to_same_hour` imports `timezone` inside the function, so the
        # patch has to land on the module it reaches for.
        holder = {}

        def fake_now():
            return holder["value"]

        from apps.organizations.models import Branch

        holder["value"] = _now(Branch.objects.first())
        monkeypatch.setattr(timezone, "now", fake_now)

    return _set


def sell(branch, variant, quantity=1, *, method=None, user=None, at=None):
    """One complete, paid order. Optionally back-dated."""
    import uuid

    order = order_services.open_order(
        branch=branch, user=user, local_number=f"T-{next(_sequence):05d}"
    )
    order_services.apply_events(
        order,
        [
            {
                "id": str(uuid.uuid4()),
                "type": EventType.ITEM_ADDED,
                "payload": {
                    "line_id": str(uuid.uuid4()),
                    "variant_id": str(variant.id),
                    "quantity": str(quantity),
                },
            }
        ],
    )
    order.refresh_from_db()

    if method is not None:
        payment_services.take_payment(
            order=order,
            method=method,
            amount=order.grand_total,
            idempotency_key=str(uuid.uuid4()),
            user=user,
        )
        order.refresh_from_db()

    if at is not None:
        # Move the payment too. A real closed day was opened AND paid on that
        # day, and the rollup attributes cash by when it entered the drawer —
        # shifting only the order would be simulating something impossible.
        Order.objects.filter(pk=order.pk).update(opened_at=at)
        Payment.objects.filter(order=order).update(created_at=at)
        order.refresh_from_db()

    return order


# ── the business day ─────────────────────────────────────────────────────────


class TestBusinessDay:
    def test_after_midnight_still_belongs_to_yesterday(self, branch) -> None:
        """
        A cafe that closes at 2am does not think of 01:30 as tomorrow, and
        neither does the cashier standing there.
        """
        tz = timezone.get_current_timezone()
        late = timezone.make_aware(datetime(2026, 8, 8, 1, 30), tz)

        assert business_day.business_date_of(branch, late) == date(2026, 8, 7)

    def test_after_the_boundary_is_the_new_day(self, branch) -> None:
        tz = timezone.get_current_timezone()
        morning = timezone.make_aware(datetime(2026, 8, 8, 4, 30), tz)

        assert business_day.business_date_of(branch, morning) == date(2026, 8, 8)

    def test_the_boundary_instant_belongs_to_the_new_day(self, branch) -> None:
        """Half-open: exactly 04:00 is tomorrow, so no order lands on both days."""
        tz = timezone.get_current_timezone()
        exact = timezone.make_aware(datetime(2026, 8, 8, 4, 0), tz)

        assert business_day.business_date_of(branch, exact) == date(2026, 8, 8)

    def test_the_boundary_is_configurable(self, branch) -> None:
        config_resolver.set_value(
            "finance.business_day_start", "06:00", scope=Scope.BRANCH, scope_id=branch.id
        )
        tz = timezone.get_current_timezone()
        early = timezone.make_aware(datetime(2026, 8, 8, 5, 0), tz)

        assert business_day.business_date_of(branch, early) == date(2026, 8, 7)

    def test_a_day_window_is_exactly_24_hours(self, branch) -> None:
        start, end = business_day.day_window(branch, date(2026, 8, 7))
        assert end - start == timedelta(days=1)
        assert timezone.localtime(start).time() == time(4, 0)

    def test_a_range_is_inclusive_of_both_ends(self, branch) -> None:
        """A human asking for "1st to 7th" means seven days, not six."""
        start, end = business_day.range_window(branch, date(2026, 8, 1), date(2026, 8, 7))
        assert end - start == timedelta(days=7)

    def test_a_late_night_sale_reports_on_the_right_day(self, branch, menu, cash) -> None:
        tz = timezone.get_current_timezone()
        late = timezone.make_aware(datetime(2026, 8, 8, 1, 30), tz)
        sell(branch, menu["cappuccino"], method=cash, at=late)

        on_the_7th = reports.sales_summary(branch, date(2026, 8, 7), date(2026, 8, 7))
        on_the_8th = reports.sales_summary(branch, date(2026, 8, 8), date(2026, 8, 8))

        assert on_the_7th["net_sales"] == "60.00"
        assert on_the_8th["net_sales"] == "0.00"


# ── reconciliation ───────────────────────────────────────────────────────────


class TestReconciliation:
    """
    The rollups must reproduce the transactional tables exactly.

    Every other report in this file reads rollups, so if this class passes the
    rest are reporting on real numbers, and if it fails none of them mean
    anything.
    """

    def _yesterday_sales(self, branch, menu, cash, card):
        """Three paid orders, back-dated into a closed business day."""
        yesterday = business_day.today(branch) - timedelta(days=1)
        start, _ = business_day.day_window(branch, yesterday)
        at = start + timedelta(hours=10)

        sell(branch, menu["cappuccino"], 2, method=cash, at=at)
        sell(branch, menu["tea"], 1, method=cash, at=at + timedelta(hours=1))
        sell(branch, menu["cake"], 1, method=card, at=at + timedelta(hours=2))
        return yesterday

    def test_the_rollup_matches_the_raw_orders(self, branch, menu, cash, card) -> None:
        yesterday = self._yesterday_sales(branch, menu, cash, card)
        row = rollups.build_day(branch, yesterday)

        start, end = business_day.day_window(branch, yesterday)
        raw = Order.objects.filter(
            branch=branch, opened_at__gte=start, opened_at__lt=end, status=OrderStatus.PAID
        )

        assert row.order_count == raw.count() == 3
        assert row.net_sales == sum(o.grand_total for o in raw)
        assert row.gross_sales == sum(o.subtotal for o in raw)
        # 2×60 + 25 + 75
        assert row.net_sales == Decimal("220.00")

    def test_cogs_comes_from_the_cost_snapshots(self, branch, menu, cash, card) -> None:
        yesterday = self._yesterday_sales(branch, menu, cash, card)
        row = rollups.build_day(branch, yesterday)

        # 2×18 + 4 + 30
        assert row.cogs == Decimal("70.00")
        assert row.gross_profit == Decimal("150.00")

    def test_cash_and_card_are_split_correctly(self, branch, menu, cash, card) -> None:
        """A card payment must never inflate what the drawer should hold."""
        yesterday = self._yesterday_sales(branch, menu, cash, card)
        row = rollups.build_day(branch, yesterday)

        assert row.cash_sales == Decimal("145.00")
        assert row.non_cash_sales == Decimal("75.00")

    def test_rebuilding_is_idempotent(self, branch, menu, cash, card) -> None:
        """
        A beat that fires twice, or a backfill overlapping the nightly job, must
        not double a day's revenue.
        """
        yesterday = self._yesterday_sales(branch, menu, cash, card)

        first = rollups.build_day(branch, yesterday)
        second = rollups.build_day(branch, yesterday)

        assert SalesDaily.objects.filter(branch=branch, business_date=yesterday).count() == 1
        assert first.net_sales == second.net_sales
        assert ProductDaily.objects.filter(branch=branch, business_date=yesterday).count() == 3

    def test_a_report_over_closed_and_open_days_sums_both(self, branch, menu, cash, card) -> None:
        """The seam that makes the whole design work: rollups plus today, once each."""
        yesterday = self._yesterday_sales(branch, menu, cash, card)
        today = business_day.today(branch)
        sell(branch, menu["tea"], 2, method=cash)  # today, still open

        summary = reports.sales_summary(branch, yesterday, today)
        assert summary["net_sales"] == "270.00"  # 220 + 50
        assert summary["order_count"] == 4

    def test_today_is_never_rolled_up(self, branch, menu, cash) -> None:
        """A cached row for a day in progress is wrong the moment the next order lands."""
        today = business_day.today(branch)
        sell(branch, menu["cappuccino"], method=cash)

        reports.sales_summary(branch, today, today)
        assert not SalesDaily.objects.filter(business_date=today).exists()

    def test_a_missing_closed_day_is_built_on_demand(self, branch, menu, cash, card) -> None:
        yesterday = self._yesterday_sales(branch, menu, cash, card)
        assert not SalesDaily.objects.exists()

        reports.sales_summary(branch, yesterday, yesterday)
        assert SalesDaily.objects.filter(business_date=yesterday).exists()

    def test_a_rollup_records_the_boundary_it_used(self, branch, menu, cash, card) -> None:
        """Changing the setting must not silently re-cut last month."""
        yesterday = self._yesterday_sales(branch, menu, cash, card)
        row = rollups.build_day(branch, yesterday)
        assert row.boundary == time(4, 0)

        config_resolver.set_value(
            "finance.business_day_start", "06:00", scope=Scope.BRANCH, scope_id=branch.id
        )
        row.refresh_from_db()
        assert row.boundary == time(4, 0), "an existing row keeps its meaning"

    def test_an_unpaid_order_is_not_revenue(self, branch, menu) -> None:
        yesterday = business_day.today(branch) - timedelta(days=1)
        start, _ = business_day.day_window(branch, yesterday)
        sell(branch, menu["cappuccino"], at=start + timedelta(hours=10))  # never paid

        assert rollups.build_day(branch, yesterday).net_sales == Decimal("0.00")

    def test_a_refund_lands_on_the_day_it_happened(self, branch, menu, cash) -> None:
        """
        A refund on Tuesday for Monday's sale reduces TUESDAY. That is what came
        out of the drawer, and it matches how the shift's Z-report treats it —
        back-dating it into a closed day would rewrite a report already printed.
        """
        import uuid

        yesterday = business_day.today(branch) - timedelta(days=1)
        today = business_day.today(branch)
        start, _ = business_day.day_window(branch, yesterday)
        order = sell(branch, menu["cappuccino"], method=cash, at=start + timedelta(hours=10))

        payment_services.refund(
            order=order,
            amount=Decimal("20.00"),
            reason="عميل غير راضٍ",
            idempotency_key=str(uuid.uuid4()),
        )

        assert rollups.build_day(branch, yesterday).net_sales == Decimal("60.00")

        summary = reports.sales_summary(branch, today, today)
        assert summary["refunds"] == "20.00"
        assert summary["net_sales"] == "-20.00", "the refund is today's outflow"


# ── the reports ──────────────────────────────────────────────────────────────


class TestSalesReports:
    def test_the_summary_of_an_empty_day_is_zeros(self, branch) -> None:
        today = business_day.today(branch)
        summary = reports.sales_summary(branch, today, today)

        assert summary["net_sales"] == "0.00"
        assert summary["average_ticket"] == "0.00"
        assert summary["order_count"] == 0

    def test_the_average_ticket_is_net_over_orders(self, branch, menu, cash) -> None:
        sell(branch, menu["cappuccino"], method=cash)
        sell(branch, menu["tea"], method=cash)

        today = business_day.today(branch)
        summary = reports.sales_summary(branch, today, today)
        assert summary["net_sales"] == "85.00"
        assert summary["average_ticket"] == "42.50"

    def test_the_margin_is_reported(self, branch, menu, cash) -> None:
        sell(branch, menu["cappuccino"], method=cash)
        today = business_day.today(branch)
        summary = reports.sales_summary(branch, today, today)

        assert summary["cogs"] == "18.00"
        assert summary["gross_profit"] == "42.00"
        assert summary["margin_percent"] == "70.00"

    def test_by_hour_returns_every_hour_including_empty_ones(self, branch, menu, cash) -> None:
        """A missing hour on a chart reads as a gap in the data, not as zero sales."""
        sell(branch, menu["cappuccino"], method=cash)
        today = business_day.today(branch)

        payload = reports.sales_by_hour(branch, today, today)
        assert len(payload["hours"]) == 24
        assert payload["peak_hour"] == timezone.localtime().hour

    def test_by_category_shares_sum_to_a_hundred(self, branch, menu, cash) -> None:
        sell(branch, menu["cappuccino"], method=cash)
        sell(branch, menu["cake"], method=cash)

        today = business_day.today(branch)
        payload = reports.sales_by_category(branch, today, today)

        assert {row["category"] for row in payload["categories"]} == {"مشروبات", "حلويات"}
        total = sum(Decimal(row["share_percent"]) for row in payload["categories"])
        assert total == Decimal("100.00")

    def test_by_payment_method_names_each_method(self, branch, menu, cash, card) -> None:
        """ "Which of the two card machines took this?" is the question that gets asked."""
        sell(branch, menu["cappuccino"], method=cash)
        sell(branch, menu["cake"], method=card)

        today = business_day.today(branch)
        payload = reports.sales_by_payment_method(branch, today, today)

        by_name = {row["method"]: row for row in payload["methods"]}
        assert by_name["نقدي"]["amount"] == "60.00"
        assert by_name["بطاقة"]["counts_as_cash"] is False
        assert payload["total"] == "135.00"


class TestProductReports:
    def test_top_and_bottom_come_back_together(self, branch, menu, cash) -> None:
        """The worst sellers are the more actionable half."""
        for _ in range(3):
            sell(branch, menu["cake"], method=cash)
        sell(branch, menu["tea"], method=cash)

        today = business_day.today(branch)
        payload = reports.products_top(branch, today, today)

        assert payload["top"][0]["name"].startswith("تشيز كيك")
        assert payload["bottom"][0]["name"].startswith("شاي")
        assert payload["product_count"] == 2

    def test_profitability_ranks_by_profit_not_revenue(self, branch, menu, cash) -> None:
        """
        The highest-revenue item is often not the one worth promoting, and that
        gap is the entire point of this report.
        """
        sell(branch, menu["cake"], method=cash)  # 75 revenue, 45 profit
        sell(branch, menu["cappuccino"], method=cash)  # 60 revenue, 42 profit

        today = business_day.today(branch)
        rows = reports.products_profitability(branch, today, today)["products"]

        assert rows[0]["profit"] == "45.00"
        assert rows[0]["margin_percent"] == "60.00"
        assert rows[1]["margin_percent"] == "70.00", "the cheaper drink has the better margin"

    def test_a_renamed_product_keeps_its_reported_name(self, branch, menu, cash) -> None:
        yesterday = business_day.today(branch) - timedelta(days=1)
        start, _ = business_day.day_window(branch, yesterday)
        sell(branch, menu["cappuccino"], method=cash, at=start + timedelta(hours=10))
        rollups.build_day(branch, yesterday)

        menu["cappuccino"].product.name_ar = "كابتشينو دبل"
        menu["cappuccino"].product.save()

        rows = reports.products_top(branch, yesterday, yesterday)["top"]
        assert rows[0]["name"].startswith("كابتشينو"), "as sold, not as renamed"
        assert "دبل" not in rows[0]["name"]


class TestInventoryReports:
    @pytest.fixture
    def beans(self, organization, branch):
        from apps.inventory import services as inventory_services

        unit = Unit.objects.create(organization=organization, code="G", name_ar="جرام")
        item = InventoryItem.objects.create(
            organization=organization, branch=branch, code="BEANS", name_ar="بن", base_unit=unit
        )
        inventory_services.set_opening_balance(
            item=item, quantity=Decimal("10000"), unit_cost=Decimal("0.30")
        )
        return item

    def test_waste_is_valued_and_attributed(self, branch, beans, make_user) -> None:
        from apps.inventory import services as inventory_services

        user = make_user(email="staff@caesar.test")
        inventory_services.record_waste(
            item=beans, quantity=Decimal("500"), reason="بن محروق", user=user
        )

        today = business_day.today(branch)
        payload = reports.inventory_waste(branch, today, today)

        assert payload["total_value"] == "150.00"
        assert payload["items"][0]["quantity"] == "500.000"
        assert payload["by_user"][0]["user"] == "مستخدم"

    def test_movements_are_listed_newest_first(self, branch, beans) -> None:
        from apps.inventory import services as inventory_services

        inventory_services.record_waste(item=beans, quantity=Decimal("10"), reason="اختبار")

        today = business_day.today(branch)
        payload = reports.inventory_movements(branch, today, today)

        assert payload["movements"][0]["type"] == MovementType.WASTE
        assert payload["movements"][-1]["type"] == MovementType.OPENING

    def test_variance_surfaces_an_injected_discrepancy(self, branch, beans, make_user) -> None:
        """
        Phase 8 exit criterion. The count says 9,400 where the system says
        10,000 — 600g gone, worth 180.
        """
        from apps.inventory import services as inventory_services
        from apps.inventory.models import CountLine, StockCount

        count = StockCount.objects.create(
            organization=branch.organization, branch=branch, reference="C-001"
        )
        CountLine.objects.create(
            count=count,
            item=beans,
            system_quantity=Decimal("10000"),
            counted_quantity=Decimal("9400"),
            reason="فرق جرد",
        )
        inventory_services.post_count(count, user=make_user(email="mgr@caesar.test"))

        today = business_day.today(branch)
        payload = reports.inventory_variance(branch, today, today)

        assert payload["lines_with_variance"] == 1
        assert payload["items"][0]["variance"] == "-600.000"
        assert Decimal(payload["shrinkage_value"]) < 0

    def test_a_count_with_no_variance_reports_nothing(self, branch, beans, make_user) -> None:
        from apps.inventory import services as inventory_services
        from apps.inventory.models import CountLine, StockCount

        count = StockCount.objects.create(
            organization=branch.organization, branch=branch, reference="C-002"
        )
        CountLine.objects.create(
            count=count,
            item=beans,
            system_quantity=Decimal("10000"),
            counted_quantity=Decimal("10000"),
        )
        inventory_services.post_count(count, user=make_user(email="mgr@caesar.test"))

        today = business_day.today(branch)
        assert reports.inventory_variance(branch, today, today)["lines_with_variance"] == 0


class TestPeopleReports:
    def test_sales_are_attributed_per_cashier(self, branch, menu, cash, make_user) -> None:
        first = make_user(email="a@caesar.test", full_name_ar="أحمد")
        second = make_user(email="b@caesar.test", full_name_ar="سارة")

        sell(branch, menu["cake"], method=cash, user=first)
        sell(branch, menu["tea"], method=cash, user=second)

        today = business_day.today(branch)
        rows = reports.employees_sales(branch, today, today)["employees"]

        assert rows[0]["name"] == "أحمد"
        assert rows[0]["net_sales"] == "75.00"
        assert rows[1]["name"] == "سارة"

    def test_voids_are_reported_as_a_rate_not_a_count(self, branch, menu, cash, make_user):
        """
        Comparing raw counts would point at the busiest person rather than the
        interesting one.
        """
        busy = make_user(email="busy@caesar.test", full_name_ar="مشغول")
        quiet = make_user(email="quiet@caesar.test", full_name_ar="هادئ")

        for _ in range(9):
            sell(branch, menu["tea"], method=cash, user=busy)
        order_services.void_order(sell(branch, menu["tea"], user=busy), reason="خطأ", actor=busy)

        order_services.void_order(sell(branch, menu["tea"], user=quiet), reason="خطأ", actor=quiet)

        today = business_day.today(branch)
        rows = {r["name"]: r for r in reports.employees_voids(branch, today, today)["employees"]}

        assert rows["مشغول"]["voided_orders"] == rows["هادئ"]["voided_orders"] == 1
        assert Decimal(rows["هادئ"]["void_rate_percent"]) == Decimal("100.00")
        assert Decimal(rows["مشغول"]["void_rate_percent"]) == Decimal("10.00")

    def test_shift_variance_reports_the_run_not_just_the_total(self, branch, make_user) -> None:
        """One bad night is a mistake; a consistent direction is a pattern."""
        from apps.shifts import services as shift_services

        user = make_user(email="cashier@caesar.test", full_name_ar="كاشير")
        config_resolver.set_value(
            "shifts.require_variance_reason", False, scope=Scope.BRANCH, scope_id=branch.id
        )

        for shortfall in ("10.00", "15.00"):
            shift = shift_services.open_shift(
                branch=branch, user=user, opening_cash=Decimal("500.00")
            )
            shift_services.close_shift(
                shift=shift,
                counted_cash=Decimal("500.00") - Decimal(shortfall),
                user=user,
            )

        today = business_day.today(branch)
        payload = reports.shift_variance(branch, today, today)

        assert len(payload["closes"]) == 2
        assert payload["by_user"][0]["total_variance"] == "-25.00"
        assert payload["by_user"][0]["average_variance"] == "-12.50"
        assert payload["by_user"][0]["worst_variance"] == "-15.00"


class TestFinancial:
    def test_the_pnl_stops_at_gross_profit_and_says_so(self, branch, menu, cash) -> None:
        """
        Presenting a number that looked like net profit while omitting rent and
        salaries would be worse than useless.
        """
        sell(branch, menu["cappuccino"], method=cash)
        today = business_day.today(branch)
        payload = reports.profit_and_loss(branch, today, today)

        assert payload["gross_profit"] == "42.00"
        assert "الإيجار" in payload["scope_note_ar"]
        assert "net_profit" not in payload


class TestDashboard:
    def test_it_answers_everything_in_one_call(self, branch, menu, cash) -> None:
        sell(branch, menu["cappuccino"], method=cash)
        order_services.open_order(branch=branch)  # left open

        payload = reports.dashboard(branch)

        assert payload["today"]["net_sales"] == "60.00"
        assert payload["open_orders"] == 1
        assert payload["kids_inside"] == 0
        assert len(payload["by_hour"]) == 24
        assert payload["top_products"][0]["name"].startswith("كابتشينو")

    def test_the_day_over_day_change_is_computed(self, branch, menu, cash, at_hour) -> None:
        at_hour(12)
        yesterday = business_day.today(branch) - timedelta(days=1)
        start, _ = business_day.day_window(branch, yesterday)
        sell(branch, menu["tea"], 2, method=cash, at=start + timedelta(hours=1))  # 50
        sell(branch, menu["cappuccino"], method=cash)  # 60 today

        payload = reports.dashboard(branch)
        assert payload["yesterday_net"] == "50.00"
        assert payload["change_percent"] == "20.00"

    def test_no_yesterday_means_no_percentage_rather_than_zero(self, branch, menu, cash) -> None:
        """A division by nothing is not a 0% change, and pretending it is misleads."""
        sell(branch, menu["cappuccino"], method=cash)
        assert reports.dashboard(branch)["change_percent"] is None

    def test_the_morning_is_compared_against_the_same_hour_yesterday(
        self, branch, menu, cash, at_hour
    ) -> None:
        """
        The bug this exists to prevent: three hours of trading measured against
        a full fourteen opened every morning with a collapse that had not
        happened, on the number the screen leads with. An owner learns within a
        week that it means nothing before closing — and then it means nothing.
        """
        at_hour(6)  # two hours past a 04:00 boundary
        yesterday = business_day.today(branch) - timedelta(days=1)
        start, _ = business_day.day_window(branch, yesterday)

        sell(branch, menu["tea"], 2, method=cash, at=start + timedelta(hours=1))  # 50, early
        sell(branch, menu["cappuccino"], 10, method=cash, at=start + timedelta(hours=9))  # evening
        sell(branch, menu["cappuccino"], method=cash)  # 60 today, so far

        payload = reports.dashboard(branch)

        # Yesterday's evening rush is excluded — it has not happened yet today.
        assert payload["yesterday_net_so_far"] == "50.00"
        assert payload["change_percent"] == "20.00"
        # The full day is still reported, for context.
        assert payload["yesterday_net"] == "650.00"

    def test_by_close_the_two_windows_are_the_whole_day(self, branch, menu, cash, at_hour) -> None:
        """Late enough in the day, the comparison is simply day against day."""
        at_hour(23)
        yesterday = business_day.today(branch) - timedelta(days=1)
        start, _ = business_day.day_window(branch, yesterday)
        sell(branch, menu["tea"], 2, method=cash, at=start + timedelta(hours=1))
        sell(branch, menu["cappuccino"], 10, method=cash, at=start + timedelta(hours=9))

        payload = reports.dashboard(branch)

        assert payload["yesterday_net_so_far"] == payload["yesterday_net"] == "650.00"


# ── export ───────────────────────────────────────────────────────────────────


class TestExport:
    def test_csv_starts_with_a_bom(self, branch, menu, cash) -> None:
        """
        Without it, Excel on a Windows machine — which is every machine this
        cafe owns — renders the Arabic headers as mojibake.
        """
        today = business_day.today(branch)
        payload = reports.sales_by_category(branch, today, today)
        body = exports.to_csv("sales/by-category", payload)

        assert body.startswith("﻿")
        assert "القسم" in body

    def test_a_summary_falls_back_to_key_value_rows(self, branch) -> None:
        """An owner asking for the summary as a file should get a file."""
        today = business_day.today(branch)
        body = exports.to_csv("sales/summary", reports.sales_summary(branch, today, today))

        assert "net_sales" in body
        assert "البند" in body

    def test_money_stays_a_string(self, branch, menu, cash) -> None:
        sell(branch, menu["cappuccino"], method=cash)
        today = business_day.today(branch)
        body = exports.to_csv("products/top", reports.products_top(branch, today, today))

        assert "60.00" in body


# ── API ──────────────────────────────────────────────────────────────────────


class TestAPI:
    def test_the_dashboard_endpoint_works(self, branch, menu, cash, make_user, authed) -> None:
        sell(branch, menu["cappuccino"], method=cash)
        client = authed(make_user(email="mgr@caesar.test", role="BRANCH_MANAGER"), branch=branch)

        response = client.get("/api/v1/reports/dashboard/")
        assert response.status_code == 200
        assert response.json()["data"]["today"]["net_sales"] == "60.00"

    def test_the_range_defaults_to_the_last_thirty_days(self, branch, make_user, authed) -> None:
        client = authed(make_user(email="mgr@caesar.test", role="BRANCH_MANAGER"), branch=branch)
        data = client.get("/api/v1/reports/sales/summary/").json()["data"]

        assert (
            date.fromisoformat(data["date_to"]) - date.fromisoformat(data["date_from"])
        ).days == 29

    def test_an_inverted_range_is_refused(self, branch, make_user, authed) -> None:
        client = authed(make_user(email="mgr@caesar.test", role="BRANCH_MANAGER"), branch=branch)
        response = client.get(
            "/api/v1/reports/sales/summary/?date_from=2026-08-08&date_to=2026-08-01"
        )
        assert response.json()["code"] == "INVALID_RANGE"

    def test_a_decade_long_range_is_refused(self, branch, make_user, authed) -> None:
        """One mistyped year must not ask the server to assemble ten of them."""
        client = authed(make_user(email="mgr@caesar.test", role="BRANCH_MANAGER"), branch=branch)
        response = client.get(
            "/api/v1/reports/sales/summary/?date_from=2016-01-01&date_to=2026-01-01"
        )
        assert response.json()["code"] == "RANGE_TOO_LONG"

    def test_a_malformed_date_says_what_the_format_is(self, branch, make_user, authed) -> None:
        client = authed(make_user(email="mgr@caesar.test", role="BRANCH_MANAGER"), branch=branch)
        response = client.get("/api/v1/reports/sales/summary/?date_from=08-08-2026")

        assert response.json()["code"] == "INVALID_DATE"
        assert "YYYY-MM-DD" in response.json()["message"]

    def test_csv_comes_back_as_a_download(self, branch, menu, cash, make_user, authed) -> None:
        sell(branch, menu["cappuccino"], method=cash)
        client = authed(make_user(email="mgr@caesar.test", role="BRANCH_MANAGER"), branch=branch)

        response = client.get("/api/v1/reports/products/top/?export=csv")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/csv")
        assert "attachment" in response["Content-Disposition"]
        assert "كابتشينو" in response.content.decode("utf-8")

    def test_a_cashier_cannot_read_the_financial_reports(self, branch, make_user, authed) -> None:
        client = authed(make_user(email="c@caesar.test", role="CASHIER"), branch=branch)
        assert client.get("/api/v1/reports/financial/pnl/").status_code == 403
        assert client.get("/api/v1/reports/employees/voids/").status_code == 403

    def test_an_export_is_not_a_lesser_form_of_access(self, branch, make_user, authed) -> None:
        """The permission is checked identically — CSV is not a side door."""
        client = authed(make_user(email="c@caesar.test", role="CASHIER"), branch=branch)
        assert client.get("/api/v1/reports/financial/pnl/?export=csv").status_code == 403

    def test_another_branch_sees_none_of_it(
        self, branch, menu, cash, other_branch, other_organization, make_user, authed
    ) -> None:
        sell(branch, menu["cappuccino"], method=cash)
        outsider = make_user(
            email="other@caesar.test", role="BRANCH_MANAGER", org=other_organization
        )
        client = authed(outsider, branch=other_branch)

        data = client.get("/api/v1/reports/sales/summary/").json()["data"]
        assert data["net_sales"] == "0.00"

    def test_rollups_can_be_rebuilt_on_demand(
        self, branch, menu, cash, card, make_user, authed
    ) -> None:
        """A cache you cannot rebuild on demand is a liability."""
        yesterday = business_day.today(branch) - timedelta(days=1)
        start, _ = business_day.day_window(branch, yesterday)
        sell(branch, menu["cappuccino"], method=cash, at=start + timedelta(hours=10))

        client = authed(make_user(email="mgr@caesar.test", role="BRANCH_MANAGER"), branch=branch)
        response = client.post(
            "/api/v1/reports/rollups/rebuild/",
            {"date_from": yesterday.isoformat(), "date_to": yesterday.isoformat()},
            format="json",
        )

        assert response.status_code == 200
        assert response.json()["data"]["days_rebuilt"] == 1
        assert SalesDaily.objects.get(business_date=yesterday).net_sales == Decimal("60.00")


def test_the_nightly_task_builds_every_branch(branch, other_branch, menu, cash) -> None:
    from apps.reporting import tasks

    yesterday = business_day.today(branch) - timedelta(days=1)
    start, _ = business_day.day_window(branch, yesterday)
    sell(branch, menu["cappuccino"], method=cash, at=start + timedelta(hours=10))

    built = tasks.build_rollups(days=1)

    assert set(built) == {branch.code, other_branch.code}
    assert SalesDaily.objects.get(branch=branch, business_date=yesterday).net_sales == Decimal(
        "60.00"
    )
    assert HourlyDaily.objects.filter(branch=branch, business_date=yesterday).exists()
