"""
Attendance: who was supposed to be here, and who actually was.

What these defend, in order of how much a mistake costs:

  * **hours are computed from the punches, every read.** A stored total is a
    number that can disagree with the times it came from, and "the system says 8
    hours but the punches say 7:12" is an argument nobody can win;
  * **the business day is the trading day**, so a cafe closing at 02:00 does not
    split one shift across two timesheets — or worse, disagree with the sales
    report about which day somebody worked;
  * **nothing closes an open punch**, because an automatic close records somebody
    as having gone home when nobody saw them leave;
  * **a correction never erases the original**, and never happens without a
    reason and a name;
  * lateness answers to a grace window that is a setting, not a constant.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from django.utils import timezone

from apps.configuration import resolver as config_resolver
from apps.configuration.registry import Scope
from apps.core.exceptions import AppError, BusinessRuleError, ConflictError
from apps.hr import services
from apps.hr.models import Attendance, AttendanceEvent, PunchSource, WorkPattern, WorkShift
from apps.reporting import business_day

pytestmark = pytest.mark.django_db


@pytest.fixture
def morning(organization, branch) -> WorkPattern:
    return WorkPattern.objects.create(
        organization=organization,
        branch=branch,
        name_ar="صباحي",
        starts_at="08:00",
        ends_at="16:00",
    )


@pytest.fixture
def night(organization, branch) -> WorkPattern:
    """22:00–06:00 — crosses midnight, which is the normal case for a cafe."""
    return WorkPattern.objects.create(
        organization=organization, branch=branch, name_ar="ليلي", starts_at="22:00", ends_at="06:00"
    )


@pytest.fixture
def cashier(make_user, branch):
    return make_user(email="mona@caesar.test", full_name_ar="منى سعيد", branch=branch)


def at(branch, business_date, *, hour: int, minute: int = 0, next_day: bool = False) -> datetime:
    """
    An aware instant on a business date.

    `next_day` is explicit rather than inferred from the boundary. The first
    version of this helper worked out the calendar date by comparing the hour
    against `finance.business_day_start`, which reads as clever and is wrong: with
    the default 04:00 boundary a 05:00 belongs to the *same* calendar day as a
    22:00, so "in at 22:00, out at 05:00" silently became a seven-hour shift that
    ran backwards. A test helper that quietly disagrees with the clock is worse
    than a parameter.
    """
    calendar_date = business_date + timedelta(days=1) if next_day else business_date
    naive = datetime.combine(calendar_date, datetime.min.time()).replace(hour=hour, minute=minute)
    return timezone.make_aware(naive, timezone.get_current_timezone())


def roster(branch, user, pattern, business_date) -> WorkShift:
    return WorkShift.objects.create(
        organization=branch.organization,
        branch=branch,
        user=user,
        pattern=pattern,
        business_date=business_date,
    )


def today(branch):
    return business_day.today(branch)


# ── the trading day ──────────────────────────────────────────────────────────


class TestTheBusinessDay:
    def test_a_punch_after_midnight_belongs_to_the_trading_day_that_is_ending(
        self, branch, cashier
    ) -> None:
        """
        The classic off-by-one-day. A cafe whose day starts at 06:00 has a 01:30
        that belongs to yesterday — the same rule the sales report already
        applies, or the timesheet and the takings disagree about which night the
        late shift worked.
        """
        boundary = business_day.boundary_for(branch)
        assert boundary.hour > 1, "this test assumes a boundary after 01:00"

        moment = timezone.make_aware(
            datetime.combine(today(branch) + timedelta(days=1), datetime.min.time()).replace(
                hour=1, minute=30
            ),
            timezone.get_current_timezone(),
        )
        attendance = services.check_in(user=cashier, branch=branch, at=moment)

        assert attendance.business_date == today(branch)

    def test_one_night_shift_is_one_row_not_two(self, branch, cashier, night) -> None:
        """
        In at 22:00, out at 05:00 the next morning — one row, seven hours.

        **This test found a real bug.** With the default 04:00 boundary the two
        punches land on DIFFERENT business dates: the arrival is filed under
        Tuesday, the departure happens during Wednesday. `check_out` looked the
        punch up by the business date of the moment it was called, so the night
        staff could not clock out at all — the row was under yesterday and the
        query asked about today. It closes the open punch regardless of date now.
        You clock out of whatever you clocked into.
        """
        day = today(branch)
        roster(branch, cashier, night, day)

        services.check_in(user=cashier, branch=branch, at=at(branch, day, hour=22))
        services.check_out(user=cashier, branch=branch, at=at(branch, day, hour=5, next_day=True))

        assert Attendance.objects.count() == 1
        assert Attendance.objects.get().worked_minutes == 7 * 60


# ── lateness ─────────────────────────────────────────────────────────────────


class TestLateness:
    def test_inside_the_grace_window_is_not_late(self, branch, cashier, morning) -> None:
        day = today(branch)
        shift = roster(branch, cashier, morning, day)

        attendance = services.check_in(
            user=cashier, branch=branch, at=at(branch, day, hour=8, minute=9)
        )

        assert attendance.shift == shift
        assert attendance.late_minutes(grace_minutes=10) == 0

    def test_past_the_grace_window_counts_from_the_rostered_start(
        self, branch, cashier, morning
    ) -> None:
        """
        Twenty minutes late with ten minutes' grace is **twenty**, not ten. The
        grace decides whether it counts at all; it is not deducted afterwards. A
        cafe that forgave the first ten minutes of every late arrival would be
        paying for them twice over by the end of the month.
        """
        day = today(branch)
        roster(branch, cashier, morning, day)

        attendance = services.check_in(
            user=cashier, branch=branch, at=at(branch, day, hour=8, minute=20)
        )

        assert attendance.late_minutes(grace_minutes=10) == 20

    def test_a_pattern_can_set_a_stricter_grace_than_the_branch(
        self, organization, branch, cashier
    ) -> None:
        """
        Zero is a real value, not "unset". `or` would silently replace it with the
        branch default, which is the opposite of what somebody just asked for —
        the same `is None` discipline `price_override` follows.
        """
        strict = WorkPattern.objects.create(
            organization=organization,
            branch=branch,
            name_ar="تسليم",
            starts_at="08:00",
            ends_at="16:00",
            grace_minutes=0,
        )
        day = today(branch)
        shift = roster(branch, cashier, strict, day)

        config = services.settings_for(branch)
        assert int(config["hr.grace_minutes"]) > 0, "the branch default must differ from zero"
        assert services.grace_for(shift, config) == 0

    def test_somebody_who_was_not_rostered_cannot_be_late(self, branch, cashier) -> None:
        """
        Nothing to be late for. Reporting them as late by the whole morning is how
        a report loses its reader.
        """
        attendance = services.check_in(
            user=cashier, branch=branch, at=at(branch, today(branch), hour=14)
        )

        assert attendance.shift is None
        assert attendance.late_minutes(grace_minutes=0) == 0

    def test_lateness_on_a_night_shift_is_measured_against_the_right_day(
        self, branch, cashier, night
    ) -> None:
        """
        A 22:00 pattern on today's business date starts tonight. If the expected
        start were built from the calendar date alone, an on-time arrival would
        read as hours late — or hours early, which is worse because nobody checks.
        """
        day = today(branch)
        roster(branch, cashier, night, day)

        attendance = services.check_in(
            user=cashier, branch=branch, at=at(branch, day, hour=22, minute=3)
        )

        assert attendance.late_minutes(grace_minutes=10) == 0


# ── punching ─────────────────────────────────────────────────────────────────


class TestPunching:
    def test_a_second_check_in_returns_the_same_row(self, branch, cashier) -> None:
        """
        Tapping twice because the first tap did not visibly do anything is the
        most likely interaction here, and an error for it teaches somebody to
        distrust the screen.
        """
        first = services.check_in(user=cashier, branch=branch)
        second = services.check_in(user=cashier, branch=branch)

        assert first.id == second.id
        assert Attendance.objects.count() == 1

    def test_coming_back_after_clocking_out_reopens_rather_than_duplicating(
        self, branch, cashier
    ) -> None:
        """
        A break in the middle of a shift must not read as two shifts. The unique
        constraint is per person per day, so this has to reopen the row.
        """
        day = today(branch)
        services.check_in(user=cashier, branch=branch, at=at(branch, day, hour=8))
        services.check_out(user=cashier, branch=branch, at=at(branch, day, hour=12))

        reopened = services.check_in(user=cashier, branch=branch, at=at(branch, day, hour=13))

        assert reopened.is_open
        assert Attendance.objects.count() == 1
        kinds = list(reopened.events.values_list("kind", flat=True))
        assert kinds == ["CHECK_IN", "CHECK_OUT", "CHECK_IN"]

    def test_checking_out_with_nothing_open_is_refused(self, branch, cashier) -> None:
        """
        Rather than inventing an arrival. Manufacturing a check-in to make the
        button work would put hours on a wage nobody recorded.
        """
        with pytest.raises(BusinessRuleError) as exc:
            services.check_out(user=cashier, branch=branch)

        assert exc.value.code == "NOT_CHECKED_IN"
        assert not Attendance.objects.exists()

    def test_checking_out_twice_is_a_conflict(self, branch, cashier) -> None:
        day = today(branch)
        services.check_in(user=cashier, branch=branch, at=at(branch, day, hour=8))
        services.check_out(user=cashier, branch=branch, at=at(branch, day, hour=16))

        with pytest.raises(ConflictError) as exc:
            services.check_out(user=cashier, branch=branch, at=at(branch, day, hour=17))

        assert exc.value.code == "ALREADY_CHECKED_OUT"

    def test_a_departure_before_the_arrival_is_refused(self, branch, cashier) -> None:
        day = today(branch)
        services.check_in(user=cashier, branch=branch, at=at(branch, day, hour=14))

        with pytest.raises(BusinessRuleError) as exc:
            services.check_out(user=cashier, branch=branch, at=at(branch, day, hour=13))

        assert exc.value.code == "CHECKOUT_BEFORE_CHECKIN"

    def test_a_branch_can_require_a_rostered_shift(self, branch, cashier) -> None:
        """
        Off by default: somebody who came in to cover a colleague must be
        recorded, and refusing the record does not prevent the attendance — it
        prevents the trace of it. A branch that wants the discipline can say so.
        """
        config_resolver.set_value(
            "hr.allow_punch_without_shift", False, scope=Scope.BRANCH, scope_id=branch.id
        )

        with pytest.raises(BusinessRuleError) as exc:
            services.check_in(user=cashier, branch=branch)

        assert exc.value.code == "NO_SHIFT_SCHEDULED"


class TestNothingClosesItself:
    def test_an_open_punch_stays_open_and_reports_no_hours(self, branch, cashier) -> None:
        """
        None, not zero. An unknown is not a zero, and counting the time since
        check-in would grow somebody's wage every time the page reloaded.
        """
        attendance = services.check_in(user=cashier, branch=branch)

        assert attendance.is_open
        assert attendance.worked_minutes is None

    def test_an_old_open_punch_is_surfaced_for_a_human(self, branch, cashier) -> None:
        """
        The whole reason nothing auto-closes: the gap becomes a question somebody
        is asked, rather than a departure the software invented.
        """
        services.check_in(user=cashier, branch=branch, at=timezone.now() - timedelta(hours=20))

        stale = services.open_punches_past(branch, hours=14)

        assert [a.user_id for a in stale] == [cashier.id]

    def test_a_closed_punch_is_never_surfaced(self, branch, cashier) -> None:
        day = today(branch)
        services.check_in(user=cashier, branch=branch, at=at(branch, day, hour=8))
        services.check_out(user=cashier, branch=branch, at=at(branch, day, hour=16))

        assert not services.open_punches_past(branch, hours=1).exists()


# ── corrections ──────────────────────────────────────────────────────────────


class TestAmendment:
    def test_the_original_punch_survives_the_correction(self, branch, cashier, make_user) -> None:
        """
        The point of the whole model. "He clocked in at 09:40 and the manager made
        it 09:00" has to still be answerable a month later, when the wage is
        disputed and the manager has left.
        """
        day = today(branch)
        original = at(branch, day, hour=9, minute=40)
        attendance = services.check_in(user=cashier, branch=branch, at=original)
        manager = make_user(email="mgr@caesar.test", role="BRANCH_MANAGER", branch=branch)

        services.amend(
            attendance=attendance,
            reason="عطل في الباب",
            actor=manager,
            new_in=at(branch, day, hour=9),
        )
        attendance.refresh_from_db()

        assert attendance.checked_in_at == original, "the evidence was overwritten"
        assert attendance.effective_in == at(branch, day, hour=9)
        assert attendance.is_amended
        assert attendance.amended_by == manager
        assert attendance.amendment_reason == "عطل في الباب"

    def test_an_amendment_without_a_reason_is_refused(self, branch, cashier, make_user) -> None:
        """
        The reason IS the amendment. An unexplained one is indistinguishable from
        a mistake, and blank-but-present is the same thing as absent.
        """
        attendance = services.check_in(user=cashier, branch=branch)

        with pytest.raises(AppError) as exc:
            services.amend(
                attendance=attendance,
                reason="   ",
                actor=make_user(email="m@caesar.test", role="BRANCH_MANAGER"),
                new_in=timezone.now(),
            )

        assert exc.value.code == "REASON_REQUIRED"

    def test_an_amendment_that_changes_nothing_is_refused(self, branch, cashier, make_user) -> None:
        attendance = services.check_in(user=cashier, branch=branch)

        with pytest.raises(AppError) as exc:
            services.amend(
                attendance=attendance,
                reason="بدون تغيير",
                actor=make_user(email="m@caesar.test", role="BRANCH_MANAGER"),
            )

        assert exc.value.code == "NOTHING_TO_AMEND"

    def test_an_amendment_cannot_invert_the_shift(self, branch, cashier, make_user) -> None:
        day = today(branch)
        services.check_in(user=cashier, branch=branch, at=at(branch, day, hour=8))
        attendance = services.check_out(user=cashier, branch=branch, at=at(branch, day, hour=16))

        with pytest.raises(BusinessRuleError) as exc:
            services.amend(
                attendance=attendance,
                reason="خطأ",
                actor=make_user(email="m@caesar.test", role="BRANCH_MANAGER"),
                new_out=at(branch, day, hour=7),
            )

        assert exc.value.code == "CHECKOUT_BEFORE_CHECKIN"

    def test_the_correction_is_in_the_punch_history(self, branch, cashier, make_user) -> None:
        """
        `AttendanceEvent` exists so a manager can be shown one person's history
        beside the timesheet without being granted `audit.view` over the whole
        cafe — a much larger permission than the question requires.
        """
        attendance = services.check_in(user=cashier, branch=branch)
        services.amend(
            attendance=attendance,
            reason="بصمة لم تُقرأ",
            actor=make_user(email="m@caesar.test", role="BRANCH_MANAGER"),
            new_in=attendance.checked_in_at - timedelta(minutes=30),
        )

        event = attendance.events.get(kind=AttendanceEvent.Kind.AMENDED)
        assert event.detail["reason"] == "بصمة لم تُقرأ"
        assert "in" in event.detail, "the previous value is not recoverable from the event"


# ── the timesheet ────────────────────────────────────────────────────────────


class TestTimesheet:
    def test_hours_come_from_the_punches(self, branch, cashier, morning) -> None:
        day = today(branch)
        roster(branch, cashier, morning, day)
        services.check_in(user=cashier, branch=branch, at=at(branch, day, hour=8))
        services.check_out(user=cashier, branch=branch, at=at(branch, day, hour=15, minute=12))

        row = services.timesheet(branch, date_from=day, date_to=day)[0]

        assert row.worked_minutes == 7 * 60 + 12
        assert row.present_days == 1
        assert row.absent_days == 0

    def test_an_amendment_shows_up_immediately(self, branch, cashier, morning, make_user) -> None:
        """
        A projection, not a stored total: there is nothing to rebuild, so a
        correction is reflected the moment it is made.
        """
        day = today(branch)
        roster(branch, cashier, morning, day)
        services.check_in(user=cashier, branch=branch, at=at(branch, day, hour=9))
        attendance = services.check_out(user=cashier, branch=branch, at=at(branch, day, hour=17))

        before = services.timesheet(branch, date_from=day, date_to=day)[0].worked_minutes
        services.amend(
            attendance=attendance,
            reason="بدأ الساعة ٨",
            actor=make_user(email="m@caesar.test", role="BRANCH_MANAGER"),
            new_in=at(branch, day, hour=8),
        )
        after = services.timesheet(branch, date_from=day, date_to=day)[0].worked_minutes

        assert after == before + 60

    def test_absence_needs_a_roster_to_be_absent_from(self, branch, cashier, morning) -> None:
        """
        `scheduled AND NOT present`. A report that called every day off an absence
        is a report an owner stops opening.
        """
        day = today(branch)
        roster(branch, cashier, morning, day)

        row = services.timesheet(branch, date_from=day, date_to=day)[0]

        assert row.scheduled_days == 1
        assert row.present_days == 0
        assert row.absent_days == 1

    def test_an_unrostered_day_worked_is_hours_but_not_a_scheduled_day(
        self, branch, cashier
    ) -> None:
        day = today(branch)
        services.check_in(user=cashier, branch=branch, at=at(branch, day, hour=8))
        services.check_out(user=cashier, branch=branch, at=at(branch, day, hour=12))

        row = services.timesheet(branch, date_from=day, date_to=day)[0]

        assert row.scheduled_days == 0
        assert row.present_days == 1
        assert row.absent_days == 0
        assert row.worked_minutes == 4 * 60

    def test_an_open_punch_contributes_no_hours_and_is_counted(self, branch, cashier) -> None:
        services.check_in(user=cashier, branch=branch, at=at(branch, today(branch), hour=8))

        row = services.timesheet(branch, date_from=today(branch), date_to=today(branch))[0]

        assert row.worked_minutes == 0
        assert row.open_punches == 1

    def test_overtime_is_what_exceeds_the_threshold(self, branch, cashier, night) -> None:
        """
        Computed, never entered. The threshold is a setting because nine hours is
        a normal day in one cafe and unheard of in another.
        """
        day = today(branch)
        roster(branch, cashier, night, day)
        services.check_in(user=cashier, branch=branch, at=at(branch, day, hour=20))
        services.check_out(user=cashier, branch=branch, at=at(branch, day, hour=6, next_day=True))

        config = services.settings_for(branch)
        threshold = int(config["hr.overtime_after_minutes"])
        row = services.timesheet(branch, date_from=day, date_to=day)[0]

        assert row.worked_minutes == 10 * 60
        assert row.overtime_minutes == 10 * 60 - threshold

    def test_a_backwards_range_is_refused(self, branch) -> None:
        day = today(branch)

        with pytest.raises(AppError) as exc:
            services.timesheet(branch, date_from=day, date_to=day - timedelta(days=1))

        assert exc.value.code == "INVALID_DATE_RANGE"


# ── the API surface ──────────────────────────────────────────────────────────


class TestPermissions:
    @pytest.fixture
    def manager(self, make_user, branch):
        return make_user(email="mgr@caesar.test", role="BRANCH_MANAGER", branch=branch)

    def test_a_cashier_cannot_read_the_roster(self, authed, make_user, branch) -> None:
        cashier = make_user(email="c@caesar.test", role="CASHIER", branch=branch)

        response = authed(cashier, branch=branch).get("/api/v1/hr/roster/")

        assert response.status_code == 403

    def test_a_manager_can_punch_somebody_in(self, authed, manager, cashier, branch) -> None:
        response = authed(manager, branch=branch).post(
            "/api/v1/hr/punch/check-in/", {"user": str(cashier.id)}, format="json"
        )

        assert response.status_code == 200, response.data
        # `response.data` is the serializer output — the envelope is applied by the
        # renderer, so it is not present on the object a test inspects.
        assert response.data["source"] == PunchSource.MANAGER

    def test_the_accountant_can_read_but_never_amend(
        self, authed, make_user, branch, cashier
    ) -> None:
        """
        They compute wages from these hours and must not be able to change the
        hours they are computing from.
        """
        accountant = make_user(email="acc@caesar.test", role="ACCOUNTANT", branch=branch)
        attendance = services.check_in(user=cashier, branch=branch)
        client = authed(accountant, branch=branch)

        assert client.get("/api/v1/hr/timesheet/").status_code == 200
        refused = client.post(
            f"/api/v1/hr/attendance/{attendance.id}/amend/",
            {"reason": "x", "checked_in_at": timezone.now().isoformat()},
            format="json",
        )
        assert refused.status_code == 403

    def test_attendance_has_no_generic_write_endpoint(
        self, authed, manager, branch, cashier
    ) -> None:
        """
        Created by a punch, corrected by an amendment — both explicit actions with
        a reason and a name. A writable row would let a wage be changed with
        neither.
        """
        attendance = services.check_in(user=cashier, branch=branch)

        response = authed(manager, branch=branch).patch(
            f"/api/v1/hr/attendance/{attendance.id}/",
            {"checked_in_at": timezone.now().isoformat()},
            format="json",
        )

        assert response.status_code == 405

    def test_a_manager_cannot_punch_in_somebody_from_another_tenant(
        self, authed, manager, branch, make_user, other_organization
    ) -> None:
        """
        Threat I1 in its plainest form: the person is named by a uuid in the body,
        so the lookup has to be scoped to the caller's organisation.
        """
        outsider = make_user(email="other@elsewhere.test", org=other_organization)

        response = authed(manager, branch=branch).post(
            "/api/v1/hr/punch/check-in/", {"user": str(outsider.id)}, format="json"
        )

        assert response.status_code == 404
        assert not Attendance.objects.filter(user=outsider).exists()
