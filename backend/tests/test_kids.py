"""
The kids area: capacity, age policy, guardian handover, and billing.

The properties under test are not all financial. Capacity failing closed and a
child never being released without a recorded recipient matter more than the
charge does, and they are tested first for that reason.
"""

from __future__ import annotations

import threading
from datetime import date, time, timedelta
from decimal import Decimal

import pytest
from django.db import connections
from django.utils import timezone

from apps.catalog.models import Category, Product, ProductVariant
from apps.configuration import resolver as config_resolver
from apps.configuration.registry import Scope
from apps.core.exceptions import BusinessRuleError
from apps.kids import services
from apps.kids.models import (
    Guardian,
    IncidentType,
    PlayArea,
    PlaySession,
    PlayTariff,
    SessionStatus,
    TariffMode,
)
from apps.orders.models import ItemStatus, OrderStatus

pytestmark = pytest.mark.django_db


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def billing_variant(organization, branch):
    """
    The service product a session bills as.

    Its price is irrelevant — the tariff decides — but it gives play revenue a
    place in the ordinary sales reports beside the coffee.
    """
    category = Category.objects.create(organization=organization, branch=branch, name_ar="خدمات")
    product = Product.objects.create(
        organization=organization,
        branch=branch,
        category=category,
        sku="KIDS",
        name_ar="صالة الأطفال",
        track_inventory=False,
    )
    return ProductVariant.objects.create(
        product=product, sku="KIDS-D", price=Decimal("0.00"), is_default=True
    )


@pytest.fixture
def area(organization, branch, billing_variant):
    return PlayArea.objects.create(
        organization=organization,
        branch=branch,
        name_ar="صالة الأطفال",
        max_capacity=3,
        min_age_months=12,
        max_age_months=144,
        billing_variant=billing_variant,
    )


@pytest.fixture
def meter(area):
    """The docs/12 worked example: 25 for 30 min, then 15 per 15, 5 grace, cap 120."""
    return PlayTariff.objects.create(
        area=area,
        name_ar="عداد",
        mode=TariffMode.TIMED,
        entry_fee=Decimal("25.00"),
        included_minutes=30,
        block_minutes=15,
        block_rate=Decimal("15.00"),
        grace_minutes=5,
        daily_cap=Decimal("120.00"),
        is_default=True,
    )


@pytest.fixture
def package(area):
    return PlayTariff.objects.create(
        area=area,
        name_ar="ساعة لعب",
        mode=TariffMode.PACKAGE,
        entry_fee=Decimal("40.00"),
        package_minutes=60,
        block_minutes=30,
        block_rate=Decimal("15.00"),
        grace_minutes=5,
    )


def admit(area, tariff=None, *, tag="1", name="يوسف", phone="01001234567", **kwargs):
    return services.check_in(
        area=area,
        child_name=name,
        guardian_name="أحمد محمود",
        guardian_phone=phone,
        age_months=kwargs.pop("age_months", 60),
        tariff=tariff,
        tag_number=tag,
        **kwargs,
    )


# ── safety ───────────────────────────────────────────────────────────────────


class TestCapacity:
    def test_the_area_refuses_the_child_past_capacity(self, area, meter) -> None:
        for index in range(area.max_capacity):
            admit(area, meter, tag=str(index + 1), name=f"طفل {index}", phone=f"010000000{index}")

        with pytest.raises(services.CapacityExceeded) as raised:
            admit(area, meter, tag="99", name="زائد", phone="01099999999")

        assert "3/3" in str(raised.value.detail) or "٣" in str(raised.value.detail)

    def test_checking_a_child_out_frees_the_place(self, area, meter) -> None:
        first = admit(area, meter, tag="1", phone="01000000001").session
        for index in range(2):
            admit(area, meter, tag=str(index + 2), name=f"طفل {index}", phone=f"010000001{index}")

        services.check_out(first, verified=True)
        assert admit(area, meter, tag="4", name="جديد", phone="01044444444").session is not None

    def test_capacity_counts_only_children_still_inside(self, area, meter) -> None:
        session = admit(area, meter, tag="1", phone="01000000001").session
        services.check_out(session, verified=True)
        assert area.occupancy() == 0


@pytest.mark.django_db(transaction=True)
class TestConcurrentCheckIn:
    """
    Capacity is a safety limit, so it must hold under a race, not merely under
    a single-threaded test. Without the row lock on the area, four terminals all
    read "2 inside" and all four admit a child.
    """

    def test_four_simultaneous_check_ins_respect_a_capacity_of_two(
        self, organization, branch, billing_variant
    ) -> None:
        area = PlayArea.objects.create(
            organization=organization,
            branch=branch,
            name_ar="صالة",
            max_capacity=2,
            billing_variant=billing_variant,
        )
        tariff = PlayTariff.objects.create(
            area=area,
            name_ar="عداد",
            mode=TariffMode.TIMED,
            entry_fee=Decimal("25.00"),
            included_minutes=30,
            block_minutes=15,
            block_rate=Decimal("15.00"),
            is_default=True,
        )

        admitted: list[str] = []
        refused: list[str] = []
        lock = threading.Lock()
        start = threading.Barrier(4)

        def attempt(index: int) -> None:
            try:
                start.wait(timeout=20)
                result = services.check_in(
                    area=area,
                    child_name=f"طفل {index}",
                    guardian_name=f"ولي {index}",
                    guardian_phone=f"0100000000{index}",
                    age_months=60,
                    tariff=tariff,
                    tag_number=str(index),
                )
                with lock:
                    admitted.append(str(result.session.id))
            except Exception as exc:
                with lock:
                    refused.append(f"{type(exc).__name__}: {exc}")
            finally:
                connections.close_all()

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        assert len(admitted) == 2, f"capacity leaked: {len(admitted)} admitted, refused={refused}"
        assert PlaySession.objects.filter(area=area, status=SessionStatus.ACTIVE).count() == 2


class TestAgePolicy:
    def test_warn_is_the_default_and_does_not_block(self, area, meter) -> None:
        """
        The staff member can see the child; the software has a number a parent
        said out loud. Blocking would overrule the person who can actually see.
        """
        result = admit(area, meter, age_months=6)
        assert result.session.status == SessionStatus.ACTIVE
        assert result.warnings and "خارج حدود" in result.warnings[0]

    def test_block_refuses(self, area, meter, branch) -> None:
        config_resolver.set_value(
            "kids.enforce_age_limits", "block", scope=Scope.BRANCH, scope_id=branch.id
        )
        with pytest.raises(services.AgeNotAllowed):
            admit(area, meter, age_months=6)

    def test_off_says_nothing_about_age(self, area, meter, branch) -> None:
        config_resolver.set_value(
            "kids.enforce_age_limits", "off", scope=Scope.BRANCH, scope_id=branch.id
        )
        warnings = admit(area, meter, age_months=6).warnings
        assert not any("خارج حدود" in w for w in warnings)

    def test_the_age_snapshot_is_retaken_every_visit(self, area, meter) -> None:
        """A stale snapshot silently ages a child out of the limits in silence."""
        first = admit(area, meter, age_months=30).session
        services.check_out(first, verified=True)

        second = admit(area, meter, age_months=42, tag="2").session
        assert second.child_id == first.child_id
        second.child.refresh_from_db()
        assert second.child.age_months_snapshot == 42


class TestGuardianHandover:
    def test_checkout_requires_verification_by_default(self, area, meter) -> None:
        session = admit(area, meter).session
        with pytest.raises(services.GuardianNotVerified):
            services.check_out(session)

        session.refresh_from_db()
        assert session.status == SessionStatus.ACTIVE, "a failed handover must not end the session"

    def test_verification_can_be_turned_off_per_branch(self, area, meter, branch) -> None:
        config_resolver.set_value(
            "kids.require_guardian_verification", False, scope=Scope.BRANCH, scope_id=branch.id
        )
        session = services.check_out(admit(area, meter).session)
        assert session.status == SessionStatus.CHECKED_OUT

    def test_who_collected_is_recorded(self, area, meter) -> None:
        session = admit(area, meter).session
        closed = services.check_out(session, verified=True)
        assert closed.released_to_guardian_id == session.guardian_id

    def test_release_to_someone_else_requires_approval(self, area, meter, make_user) -> None:
        """The father drops off, the mother collects — legitimate, and auditable."""
        session = admit(area, meter).session
        mother = Guardian.objects.create(
            organization=session.organization,
            branch=session.branch,
            full_name="سارة",
            phone="01055555555",
        )

        with pytest.raises(services.GuardianNotVerified) as raised:
            services.check_out(session, released_to=mother, verified=True)
        assert raised.value.code == "RELEASE_APPROVAL_REQUIRED"

        manager = make_user(email="mgr@caesar.test", role="BRANCH_MANAGER")
        closed = services.check_out(session, released_to=mother, verified=True, approval=manager)
        assert closed.released_to_guardian_id == mother.id
        assert closed.release_approved_by_id == manager.id

    def test_a_returning_guardian_is_found_by_phone(self, area, meter) -> None:
        first = admit(area, meter).session
        services.check_out(first, verified=True)

        second = admit(area, meter, tag="2", name="ملك").session
        assert second.guardian_id == first.guardian_id
        second.guardian.refresh_from_db()
        assert second.guardian.visit_count == 2

    def test_a_session_cannot_be_closed_twice(self, area, meter) -> None:
        session = services.check_out(admit(area, meter).session, verified=True)
        with pytest.raises(BusinessRuleError):
            services.check_out(session, verified=True)


class TestTags:
    def test_a_tag_in_use_is_refused(self, area, meter) -> None:
        admit(area, meter, tag="14")
        with pytest.raises(BusinessRuleError) as raised:
            admit(area, meter, tag="14", name="ملك", phone="01022222222")
        assert raised.value.code == "TAG_IN_USE"

    def test_a_tag_is_reusable_after_checkout(self, area, meter) -> None:
        services.check_out(admit(area, meter, tag="14").session, verified=True)
        assert admit(area, meter, tag="14", name="ملك", phone="01022222222") is not None


# ── pricing ──────────────────────────────────────────────────────────────────


class TestTariffSelection:
    def test_an_explicit_choice_always_wins(self, area, meter, package) -> None:
        """Staff can see the promotion and the family; a schedule cannot."""
        session = admit(area, package).session
        assert session.tariff_id == package.id

    def test_the_highest_priority_matching_window_is_chosen(self, area, meter, package) -> None:
        package.priority = 10
        package.applies_from = time(0, 0)
        package.applies_to = time(23, 59)
        package.save()

        assert services.resolve_tariff(area).id == package.id

    def test_a_tariff_outside_its_window_is_skipped(self, area, meter, package) -> None:
        now = timezone.localtime()
        package.priority = 10
        package.applies_from = (now + timedelta(hours=2)).time()
        package.applies_to = (now + timedelta(hours=3)).time()
        package.save()

        assert services.resolve_tariff(area, moment=now).id == meter.id

    def test_an_area_with_no_tariff_cannot_admit(self, area) -> None:
        with pytest.raises(BusinessRuleError) as raised:
            admit(area)
        assert raised.value.code == "NO_TARIFF"


class TestRunningCharge:
    def test_the_quote_matches_the_golden_engine(self, area, meter) -> None:
        session = admit(area, meter).session
        session.checked_in_at = timezone.now() - timedelta(minutes=52)
        session.save(update_fields=["checked_in_at"])

        assert services.quote(session).charge == Decimal("55.00")

    def test_editing_the_tariff_does_not_reprice_a_running_session(self, area, meter) -> None:
        """The same rule as the VAT snapshot: a change mid-visit rewrites nothing."""
        session = admit(area, meter).session
        session.checked_in_at = timezone.now() - timedelta(minutes=52)
        session.save(update_fields=["checked_in_at"])

        meter.entry_fee = Decimal("500.00")
        meter.block_rate = Decimal("500.00")
        meter.save()

        assert services.quote(session).charge == Decimal("55.00")

    def test_expected_end_is_set_from_the_tariff(self, area, package) -> None:
        session = admit(area, package).session
        assert session.expected_end_at is not None
        assert (session.expected_end_at - session.checked_in_at) == timedelta(minutes=60)

    def test_an_open_day_session_has_no_expected_end(self, area) -> None:
        open_day = PlayTariff.objects.create(
            area=area, name_ar="يوم مفتوح", mode=TariffMode.OPEN_DAY, entry_fee=Decimal("100.00")
        )
        assert admit(area, open_day).session.expected_end_at is None

    def test_overdue_is_a_status_not_an_action(self, area, package) -> None:
        """Nothing is charged and nothing is closed — a human is simply told."""
        session = admit(area, package).session
        session.checked_in_at = timezone.now() - timedelta(minutes=200)
        session.expected_end_at = timezone.now() - timedelta(minutes=140)
        session.save(update_fields=["checked_in_at", "expected_end_at"])

        assert services.refresh_overdue(area) == 1
        session.refresh_from_db()
        assert session.status == SessionStatus.OVERDUE
        assert session.checked_out_at is None

    def test_changing_tariff_mid_session_applies_from_check_in(self, area, meter) -> None:
        open_day = PlayTariff.objects.create(
            area=area, name_ar="يوم مفتوح", mode=TariffMode.OPEN_DAY, entry_fee=Decimal("100.00")
        )
        session = admit(area, meter).session
        session.checked_in_at = timezone.now() - timedelta(minutes=200)
        session.save(update_fields=["checked_in_at"])

        session = services.change_tariff(session, open_day)
        assert services.quote(session).charge == Decimal("100.00")


# ── billing ──────────────────────────────────────────────────────────────────


class TestBilling:
    def _closed(self, area, tariff, minutes=52):
        session = admit(area, tariff).session
        session.checked_in_at = timezone.now() - timedelta(minutes=minutes)
        session.save(update_fields=["checked_in_at"])
        return services.check_out(session, verified=True)

    def test_a_session_becomes_one_ordinary_order_line(self, area, meter) -> None:
        session = self._closed(area, meter)
        order = services.bill_session(session)

        item = order.items.get()
        assert item.unit_price_snapshot == Decimal("55.00")
        assert item.quantity == Decimal("1.000")
        assert item.status == ItemStatus.ACTIVE
        assert order.grand_total >= Decimal("55.00")

    def test_the_line_explains_itself_on_a_reprint(self, area, meter) -> None:
        """Six months later, without joining to a tariff that has since changed."""
        session = self._closed(area, meter)
        name = services.bill_session(session).items.get().name_snapshot

        assert "صالة الأطفال" in name
        assert "عداد" in name
        assert "تاج #1" in name
        assert "دقيقة" in name

    def test_the_kitchen_never_sees_a_play_line(self, area, meter) -> None:
        session = self._closed(area, meter)
        assert services.bill_session(session).items.get().station_id is None

    def test_billing_is_idempotent(self, area, meter) -> None:
        """A retried checkout must not charge the visit twice."""
        session = self._closed(area, meter)
        first = services.bill_session(session)
        session.refresh_from_db()
        second = services.bill_session(session)

        assert first.id == second.id
        assert first.items.count() == 1

    def test_it_appends_to_the_parents_table_order(self, area, meter, branch) -> None:
        from apps.orders import services as order_services

        order = order_services.open_order(branch=branch)
        session = self._closed(area, meter)
        billed = services.bill_session(session, order=order)

        assert billed.id == order.id
        session.refresh_from_db()
        assert session.order_id == order.id

    def test_vat_and_service_apply_like_any_other_line(self, area, meter, branch) -> None:
        """
        The whole reason for converting at checkout instead of metering into the
        financial core: everything downstream works unmodified.
        """
        config_resolver.set_value(
            "finance.vat_percent", "14", scope=Scope.BRANCH, scope_id=branch.id
        )
        config_resolver.set_value(
            "finance.vat_enabled", True, scope=Scope.BRANCH, scope_id=branch.id
        )

        order = services.bill_session(self._closed(area, meter))
        assert order.tax_total == Decimal("7.70")  # 55.00 × 14%
        assert order.grand_total == Decimal("62.70")

    def test_an_unclosed_session_cannot_be_billed(self, area, meter) -> None:
        with pytest.raises(BusinessRuleError) as raised:
            services.bill_session(admit(area, meter).session)
        assert raised.value.code == "SESSION_NOT_CLOSED"

    def test_billing_without_a_configured_product_says_so(
        self, organization, branch, meter, area
    ) -> None:
        area.billing_variant = None
        area.save(update_fields=["billing_variant"])

        session = self._closed(area, meter)
        with pytest.raises(BusinessRuleError) as raised:
            services.bill_session(session)
        assert raised.value.code == "KIDS_BILLING_PRODUCT_NOT_SET"

    def test_the_branch_setting_supplies_the_product_when_the_area_does_not(
        self, area, meter, billing_variant, branch
    ) -> None:
        area.billing_variant = None
        area.save(update_fields=["billing_variant"])
        config_resolver.set_value(
            "kids.billing_product", str(billing_variant.id), scope=Scope.BRANCH, scope_id=branch.id
        )

        assert services.bill_session(self._closed(area, meter)).items.count() == 1


class TestOverride:
    def _closed(self, area, tariff):
        session = admit(area, tariff).session
        session.checked_in_at = timezone.now() - timedelta(minutes=52)
        session.save(update_fields=["checked_in_at"])
        return services.check_out(session, verified=True)

    def test_the_computed_figure_survives_the_override(self, area, meter, make_user) -> None:
        """Never destroy what the system calculated to record what a human decided."""
        session = self._closed(area, meter)
        manager = make_user(email="mgr@caesar.test", role="BRANCH_MANAGER")

        session = services.override_session_charge(
            session, amount=Decimal("30.00"), reason="اعتذار عن عطل", user=manager
        )

        assert session.computed_charge == Decimal("55.00")
        assert session.override_charge == Decimal("30.00")
        assert session.payable == Decimal("30.00")
        assert session.override_by_id == manager.id

    def test_the_override_is_what_reaches_the_bill(self, area, meter) -> None:
        session = services.override_session_charge(
            self._closed(area, meter), amount=Decimal("30.00"), reason="عطل"
        )
        assert services.bill_session(session).items.get().unit_price_snapshot == Decimal("30.00")

    def test_a_reason_is_required(self, area, meter) -> None:
        with pytest.raises(BusinessRuleError) as raised:
            services.override_session_charge(
                self._closed(area, meter), amount=Decimal("30.00"), reason="  "
            )
        assert raised.value.code == "REASON_REQUIRED"

    def test_a_billed_session_cannot_be_re_priced(self, area, meter) -> None:
        """The line is already on an invoice; changing it here would desync the bill."""
        session = self._closed(area, meter)
        services.bill_session(session)
        session.refresh_from_db()

        with pytest.raises(BusinessRuleError) as raised:
            services.override_session_charge(session, amount=Decimal("1.00"), reason="متأخر")
        assert raised.value.code == "ALREADY_BILLED"

    def test_the_setting_can_forbid_overrides_entirely(self, area, meter, branch) -> None:
        config_resolver.set_value(
            "kids.allow_charge_override", False, scope=Scope.BRANCH, scope_id=branch.id
        )
        with pytest.raises(BusinessRuleError) as raised:
            services.override_session_charge(
                self._closed(area, meter), amount=Decimal("30.00"), reason="عطل"
            )
        assert raised.value.code == "OVERRIDE_DISABLED"


# ── reporting ────────────────────────────────────────────────────────────────


class TestReporting:
    def test_open_sessions_are_outstanding_liability(self, area, meter, branch) -> None:
        session = admit(area, meter).session
        session.checked_in_at = timezone.now() - timedelta(minutes=52)
        session.save(update_fields=["checked_in_at"])

        outstanding = services.outstanding_sessions(branch)
        assert len(outstanding) == 1
        assert outstanding[0]["running_charge"] == "55.00"

    def test_a_shift_z_report_carries_them(self, area, meter, branch, make_user) -> None:
        """A shift must not close silently over a child still in the area."""
        from apps.shifts import services as shift_services

        user = make_user(email="cashier@caesar.test")
        shift = shift_services.open_shift(branch=branch, user=user, opening_cash=Decimal("500.00"))
        admit(area, meter)

        closed = shift_services.close_shift(shift=shift, counted_cash=Decimal("500.00"), user=user)
        assert closed.z_report["open_play_sessions"] == 1
        assert Decimal(closed.z_report["open_play_liability"]) > Decimal("0")

    def test_the_report_buckets_by_hour_and_tariff(self, area, meter, package, branch) -> None:
        for index, tariff in enumerate((meter, package)):
            session = admit(area, tariff, tag=str(index), phone=f"010000000{index}").session
            session.checked_in_at = timezone.now() - timedelta(minutes=52)
            session.save(update_fields=["checked_in_at"])
            services.check_out(session, verified=True)

        report = services.report(branch)
        assert report["sessions"] == 2
        assert set(report["by_tariff"]) == {"عداد", "ساعة لعب"}
        assert report["average_minutes"] > 0

    def test_an_empty_report_is_zeros_not_a_crash(self, branch) -> None:
        assert services.report(branch)["sessions"] == 0


class TestIncidents:
    def test_an_incident_is_recorded_against_the_session(self, area, meter, make_user) -> None:
        session = admit(area, meter).session
        incident = services.log_incident(
            area=area,
            incident_type=IncidentType.INJURY,
            description="سقوط من الزحلقة — تم إبلاغ ولي الأمر",
            session=session,
            user=make_user(email="staff@caesar.test", role="KIDS_STAFF"),
        )
        assert incident.session_id == session.id
        assert area.incidents.count() == 1


# ── API ──────────────────────────────────────────────────────────────────────


class TestAPI:
    def test_check_in_and_board(self, area, meter, branch, make_user, authed) -> None:
        client = authed(make_user(email="kids@caesar.test", role="KIDS_STAFF"), branch=branch)

        response = client.post(
            "/api/v1/kids/sessions/check-in/",
            {
                "area": str(area.id),
                "child_name": "يوسف",
                "guardian_name": "أحمد",
                "guardian_phone": "01001234567",
                "age_months": 60,
                "tag_number": "14",
            },
            format="json",
        )
        assert response.status_code == 201, response.json()
        assert response.json()["data"]["session"]["tag_number"] == "14"

        board = client.get(f"/api/v1/kids/areas/{area.id}/board/")
        assert board.json()["data"]["occupancy"] == 1
        assert board.json()["data"]["capacity"] == area.max_capacity

    def test_check_out_bills_and_returns_the_order(self, area, meter, branch, make_user, authed):
        client = authed(make_user(email="kids@caesar.test", role="CASHIER"), branch=branch)
        session = admit(area, meter).session
        session.checked_in_at = timezone.now() - timedelta(minutes=52)
        session.save(update_fields=["checked_in_at"])

        response = client.post(
            f"/api/v1/kids/sessions/{session.id}/check-out/",
            {"verified": True},
            format="json",
        )
        assert response.status_code == 200, response.json()
        assert response.json()["data"]["charge"] == "55.00"
        assert response.json()["data"]["order_id"] is not None

    def test_check_out_without_verification_is_refused(
        self, area, meter, branch, make_user, authed
    ) -> None:
        client = authed(make_user(email="kids@caesar.test", role="CASHIER"), branch=branch)
        session = admit(area, meter).session

        response = client.post(f"/api/v1/kids/sessions/{session.id}/check-out/", {}, format="json")
        assert response.status_code == 400
        assert response.json()["code"] == "KIDS_GUARDIAN_NOT_VERIFIED"

    def test_kids_staff_cannot_override_a_charge(self, area, meter, branch, make_user, authed):
        """
        Deliberately narrow: the person running the area handles children, not
        prices. Overriding is a manager's decision.
        """
        client = authed(make_user(email="kids@caesar.test", role="KIDS_STAFF"), branch=branch)
        session = services.check_out(admit(area, meter).session, verified=True)

        response = client.post(
            f"/api/v1/kids/sessions/{session.id}/override/",
            {"amount": "1.00", "reason": "محاولة"},
            format="json",
        )
        assert response.status_code == 403

    def test_another_branch_cannot_see_the_area(
        self, area, other_branch, other_organization, make_user, authed
    ) -> None:
        outsider = make_user(
            email="other@caesar.test", role="BRANCH_MANAGER", org=other_organization
        )
        client = authed(outsider, branch=other_branch)

        assert client.get(f"/api/v1/kids/areas/{area.id}/board/").status_code == 404
        assert client.get("/api/v1/kids/areas/").json()["data"] == []

    def test_a_guardian_is_searchable_by_phone(self, area, meter, branch, make_user, authed):
        admit(area, meter)
        client = authed(make_user(email="kids@caesar.test", role="KIDS_STAFF"), branch=branch)

        response = client.get("/api/v1/kids/guardians/?phone=0100123")
        assert len(response.json()["data"]) == 1

    def test_the_tariff_preview_uses_the_real_engine(
        self, area, meter, branch, make_user, authed
    ) -> None:
        """
        What an admin sees while designing a rule must be what a parent is
        charged under it — so the preview runs `compute_charge`, not a second
        implementation in the browser.
        """
        client = authed(make_user(email="mgr@caesar.test", role="BRANCH_MANAGER"), branch=branch)

        rows = client.get(f"/api/v1/kids/tariffs/{meter.id}/preview/").json()["data"]
        by_minutes = {row["minutes"]: row for row in rows}

        assert by_minutes[34]["charge"] == "25.00"  # inside grace
        assert by_minutes[38]["charge"] == "40.00"  # one block
        assert by_minutes[52]["charge"] == "55.00"  # two blocks
        assert by_minutes[240]["capped"] is True

    def test_the_configuration_lists_are_reachable(
        self, area, meter, package, branch, make_user, authed
    ) -> None:
        """
        A POST-only test would not have noticed these: the tariff and child
        querysets are scoped through a relation, and a wrong manager on either
        only fails when something actually reads them.
        """
        client = authed(make_user(email="mgr@caesar.test", role="BRANCH_MANAGER"), branch=branch)
        admit(area, meter)

        assert len(client.get("/api/v1/kids/tariffs/").json()["data"]) == 2
        assert len(client.get("/api/v1/kids/areas/").json()["data"]) == 1
        assert len(client.get("/api/v1/kids/children/").json()["data"]) == 1
        assert len(client.get("/api/v1/kids/sessions/").json()["data"]) == 1

    def test_a_tariff_that_gives_overrun_away_is_rejected(
        self, area, branch, make_user, authed
    ) -> None:
        client = authed(make_user(email="mgr@caesar.test", role="BRANCH_MANAGER"), branch=branch)

        response = client.post(
            "/api/v1/kids/tariffs/",
            {
                "area": str(area.id),
                "name_ar": "سيئة",
                "mode": TariffMode.TIMED,
                "entry_fee": "25.00",
                "included_minutes": 30,
                "block_minutes": 15,
                "block_rate": "0.00",
            },
            format="json",
        )
        assert response.status_code == 400

    def test_an_incident_can_be_logged_from_the_floor(
        self, area, meter, branch, make_user, authed
    ) -> None:
        client = authed(make_user(email="kids@caesar.test", role="KIDS_STAFF"), branch=branch)
        session = admit(area, meter).session

        response = client.post(
            "/api/v1/kids/incidents/log/",
            {
                "area": str(area.id),
                "session": str(session.id),
                "incident_type": IncidentType.INJURY,
                "description": "كدمة بسيطة",
            },
            format="json",
        )
        assert response.status_code == 201
        assert area.incidents.count() == 1

    def test_the_report_needs_its_own_permission(self, area, branch, make_user, authed) -> None:
        staff = authed(make_user(email="kids@caesar.test", role="KIDS_STAFF"), branch=branch)
        assert staff.get("/api/v1/kids/reports/").status_code == 403

        manager = authed(make_user(email="mgr@caesar.test", role="BRANCH_MANAGER"), branch=branch)
        assert manager.get("/api/v1/kids/reports/").status_code == 200


class TestOrderIntegration:
    def test_the_event_is_rejected_for_a_session_that_is_still_running(
        self, area, meter, branch
    ) -> None:
        import uuid

        from apps.orders import services as order_services
        from apps.orders.models import EventType

        session = admit(area, meter).session
        order = order_services.open_order(branch=branch)

        with pytest.raises(order_services.EventRejected):
            order_services.apply_events(
                order,
                [
                    {
                        "id": str(uuid.uuid4()),
                        "type": EventType.PLAY_SESSION_CHARGED,
                        "payload": {"session_id": str(session.id)},
                    }
                ],
            )

    def test_the_billed_order_can_be_paid_like_any_other(self, area, meter, branch) -> None:
        from apps.payments import services as payment_services
        from apps.payments.models import PaymentMethod

        session = admit(area, meter).session
        session.checked_in_at = timezone.now() - timedelta(minutes=52)
        session.save(update_fields=["checked_in_at"])
        order = services.bill_session(services.check_out(session, verified=True))

        method = PaymentMethod.objects.create(
            organization=order.organization, branch=branch, code="CASH", name_ar="نقدي"
        )
        payment_services.take_payment(
            order=order,
            method=method,
            amount=order.grand_total,
            idempotency_key=str(session.id),
        )
        order.refresh_from_db()
        assert order.status == OrderStatus.PAID


class TestChildAge:
    def test_a_birth_date_beats_the_snapshot(self, area, meter) -> None:
        result = admit(area, meter, birth_date=date(2020, 8, 7), age_months=None)
        # Computed, not the 60 the caller would otherwise have supplied.
        assert result.session.child.age_months(timezone.now()) == 72

    def test_a_snapshot_is_used_when_there_is_no_birth_date(self, area, meter) -> None:
        """Parents frequently decline a date but will say "سنتين ونص"."""
        session = admit(area, meter, age_months=30).session
        assert session.child.birth_date is None
        assert session.child.age_months() == 30
