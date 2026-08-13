"""
Attendance rules. The views own no logic; this does.

Three decisions shape everything here, and each went against the easier option:

  * **The business day is the existing one.** `finance.business_day_start` already
    decides which trading day an order belongs to. A cafe closing at 02:00 has
    staff whose shift starts Tuesday and ends Wednesday, and if the rota used the
    wall-clock date while the sales report used the trading date, the two would
    disagree about which day somebody worked — on the report an owner uses to
    decide whether the late shift pays for itself.

  * **Nothing closes an open punch automatically.** The kids area settles the same
    question the same way and for the same reason: an automatic close records
    somebody as having gone home when nobody saw them leave. A wage computed from
    an invented departure is worse than a gap a human is asked about.

  * **A correction never overwrites a punch.** It writes beside it, with a reason
    and a name. A cafe argues about wages, and "he clocked in at 09:40 and the
    manager made it 09:00" has to still be answerable a month later.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.db import transaction
from django.utils import timezone

from apps.core.exceptions import AppError, BusinessRuleError, ConflictError
from apps.reporting import business_day

from .models import Attendance, AttendanceEvent, PunchSource, WorkShift

#: Settings this module reads. Named once so a typo is a KeyError at import of
#: the setting rather than a plausible-looking default at runtime.
SETTING_KEYS = (
    "hr.grace_minutes",
    "hr.overtime_after_minutes",
    "hr.missing_checkout_hours",
    "hr.allow_punch_without_shift",
)


def settings_for(branch) -> dict:
    from apps.configuration.resolver import ScopeContext, get_many

    context = ScopeContext(organization_id=branch.organization_id, branch_id=branch.id)
    return get_many(list(SETTING_KEYS), context)


def grace_for(shift: WorkShift | None, config: dict) -> int:
    """
    The pattern's own grace, or the branch's.

    Per-pattern override because a delivery shift that starts when the van
    arrives cannot hold the same grace as a counter shift with a queue at the
    door. `is None` and not falsiness — a pattern deliberately set to zero grace
    is the strictest setting available, and `or` would silently replace it with
    the branch default, which is the opposite of what somebody just asked for.
    """
    if shift is not None and shift.pattern.grace_minutes is not None:
        return shift.pattern.grace_minutes
    return int(config["hr.grace_minutes"])


# ── punching ─────────────────────────────────────────────────────────────────


@transaction.atomic
def check_in(
    *,
    user,
    branch,
    at: datetime | None = None,
    source: str = PunchSource.TERMINAL,
    actor=None,
    note: str = "",
) -> Attendance:
    """
    Record an arrival.

    Idempotent by the day, not by the request: a second check-in on a day that
    already has one returns the existing row rather than raising. Somebody
    tapping the button twice because the first tap did not visibly do anything is
    the single most likely interaction here, and an error message for it teaches
    them to distrust the screen. A genuine second arrival after going home is a
    re-open, handled below.
    """
    at = at or timezone.now()
    business_date = business_day.business_date_of(branch, at)
    config = settings_for(branch)

    shift = WorkShift.objects.filter(user=user, business_date=business_date, branch=branch).first()

    if shift is None and not config["hr.allow_punch_without_shift"]:
        raise BusinessRuleError(
            "لا توجد وردية مسجلة لهذا اليوم. المدير يضيفها من الجدول.",
            code="NO_SHIFT_SCHEDULED",
        )

    existing = (
        Attendance.objects.select_for_update()
        .filter(user=user, business_date=business_date)
        .first()
    )
    if existing is not None:
        if existing.is_open:
            # Already here. Not an error — see the docstring.
            return existing
        # Came back after clocking out. Re-open rather than create a second row:
        # the unique constraint is per person per day, and a break in the middle
        # of a shift must not read as two shifts.
        existing.checked_out_at = None
        existing.save(update_fields=["checked_out_at", "updated_at"])
        _event(existing, AttendanceEvent.Kind.CHECK_IN, at, actor, {"reopened": True})
        return existing

    attendance = Attendance.objects.create(
        organization=branch.organization,
        branch=branch,
        user=user,
        business_date=business_date,
        shift=shift,
        checked_in_at=at,
        source=source,
        note=note,
        created_by=actor,
    )
    _event(attendance, AttendanceEvent.Kind.CHECK_IN, at, actor, {})

    late = attendance.late_minutes(grace_minutes=grace_for(shift, config))
    if late:
        _audit(
            "hr.attendance_late",
            attendance,
            detail={"late_minutes": late, "grace_minutes": grace_for(shift, config)},
        )
    return attendance


@transaction.atomic
def check_out(*, user, branch, at: datetime | None = None, actor=None) -> Attendance:
    """
    Record a departure.

    Refuses when there is nothing open, rather than inventing an arrival. A
    departure with no matching arrival is a data-entry problem for a manager to
    fix on the timesheet, and manufacturing a check-in to make the button work
    would put hours on a wage that nobody recorded.

    **Closes the open punch, whichever business date it belongs to** — not the one
    matching the moment of the departure. With the default 04:00 boundary a
    22:00–06:00 shift starts on one business date and ends on the next, so
    matching by date meant the night staff could not clock out at all: the punch
    was filed under yesterday and the lookup asked about today. You clock out of
    whatever you clocked into.

    A punch left open longer than `hr.missing_checkout_hours` is refused instead,
    because closing it would write a twenty-hour shift into somebody's wage
    silently. That case is a forgotten check-out from a previous day, the alert
    has already raised it, and the remedy is an amendment with a reason on it
    rather than a departure time that happens to be when the next person
    remembered.
    """
    at = at or timezone.now()

    attendance = (
        Attendance.objects.select_for_update()
        .filter(
            user=user,
            branch=branch,
            checked_out_at__isnull=True,
            amended_out_at__isnull=True,
        )
        .order_by("-checked_in_at")
        .first()
    )
    if attendance is None:
        # Distinguish "never arrived" from "already left", because the remedies
        # differ and a single message would send somebody to the wrong screen.
        already = Attendance.objects.filter(
            user=user, branch=branch, business_date=business_day.business_date_of(branch, at)
        ).exists()
        if already:
            raise ConflictError("تم تسجيل الانصراف بالفعل.", code="ALREADY_CHECKED_OUT")
        raise BusinessRuleError(
            "لا يوجد تسجيل حضور لهذا اليوم. المدير يسجّله من كشف الحضور.",
            code="NOT_CHECKED_IN",
        )

    config = settings_for(branch)
    stale_after = timedelta(hours=int(config["hr.missing_checkout_hours"]))
    if at - attendance.effective_in > stale_after:
        raise BusinessRuleError(
            "الحضور المفتوح قديم — المدير يصححه من كشف الحضور بسبب مكتوب.",
            code="STALE_PUNCH",
        )

    if at < attendance.effective_in:
        raise BusinessRuleError(
            "وقت الانصراف قبل وقت الحضور.",
            code="CHECKOUT_BEFORE_CHECKIN",
        )

    attendance.checked_out_at = at
    attendance.save(update_fields=["checked_out_at", "updated_at"])
    _event(attendance, AttendanceEvent.Kind.CHECK_OUT, at, actor, {})
    return attendance


@transaction.atomic
def amend(
    *,
    attendance: Attendance,
    reason: str,
    actor,
    new_in: datetime | None = None,
    new_out: datetime | None = None,
) -> Attendance:
    """
    Correct a punch without erasing it.

    A reason is required and not optional-with-a-default, because the reason is
    the whole point: the amendment is a decision somebody made about somebody
    else's wage, and an unexplained one is indistinguishable from a mistake.

    Audited at WARNING. A manager who can move a clock-in can move a wage, which
    puts this in the same class as a discount ceiling or a price override rather
    than in the class of routine edits.
    """
    if not reason.strip():
        raise AppError("سبب التعديل مطلوب.", code="REASON_REQUIRED")
    if new_in is None and new_out is None:
        raise AppError("لا يوجد تعديل.", code="NOTHING_TO_AMEND")

    effective_in = new_in or attendance.effective_in
    effective_out = new_out if new_out is not None else attendance.effective_out
    if effective_out is not None and effective_out < effective_in:
        raise BusinessRuleError("وقت الانصراف قبل وقت الحضور.", code="CHECKOUT_BEFORE_CHECKIN")

    before = {
        "in": attendance.effective_in.isoformat(),
        "out": attendance.effective_out.isoformat() if attendance.effective_out else None,
    }

    if new_in is not None:
        attendance.amended_in_at = new_in
    if new_out is not None:
        attendance.amended_out_at = new_out
    attendance.amendment_reason = reason.strip()
    attendance.amended_by = actor
    attendance.source = PunchSource.AMENDED
    attendance.save(
        update_fields=[
            "amended_in_at",
            "amended_out_at",
            "amendment_reason",
            "amended_by",
            "source",
            "updated_at",
        ]
    )
    _event(
        attendance,
        AttendanceEvent.Kind.AMENDED,
        timezone.now(),
        actor,
        {"reason": reason.strip(), **before},
    )
    _audit(
        "hr.attendance_amended",
        attendance,
        detail={"reason": reason.strip(), "before": before},
        before=before,
        after={
            "in": attendance.effective_in.isoformat(),
            "out": (attendance.effective_out.isoformat() if attendance.effective_out else None),
        },
    )
    return attendance


# ── the timesheet, which is a projection ─────────────────────────────────────


@dataclass
class Row:
    """One person's month. Every figure computed, none stored."""

    user_id: str
    name_ar: str
    scheduled_days: int
    present_days: int
    absent_days: int
    late_days: int
    late_minutes: int
    worked_minutes: int
    overtime_minutes: int
    open_punches: int


def timesheet(branch, *, date_from: date, date_to: date) -> list[Row]:
    """
    Hours per person over a range, computed from the punches every time.

    A projection, never a stored column — the same rule inventory follows with
    `StockLevel`. A cafe argues about wages, and "the system says 8 hours but the
    punches say 7:12" is an argument nobody can win. Recomputing means an
    amendment is reflected the moment it is made, with nothing to rebuild.

    Absence is `scheduled AND NOT present`, so somebody who was never rostered is
    not marked absent — a report that calls every day off an absence is a report
    an owner stops opening.
    """
    if date_from > date_to:
        raise AppError("نطاق التاريخ معكوس.", code="INVALID_DATE_RANGE")

    config = settings_for(branch)
    overtime_after = int(config["hr.overtime_after_minutes"])

    shifts = WorkShift.objects.filter(
        branch=branch, business_date__gte=date_from, business_date__lte=date_to
    ).select_related("user", "pattern")
    punches = (
        Attendance.objects.filter(
            branch=branch, business_date__gte=date_from, business_date__lte=date_to
        )
        .select_related("user", "shift", "shift__pattern")
        .order_by("business_date")
    )

    people: dict[str, Row] = {}
    scheduled_dates: dict[str, set[date]] = {}
    present_dates: dict[str, set[date]] = {}

    def row_for(user) -> Row:
        key = str(user.id)
        if key not in people:
            people[key] = Row(
                user_id=key,
                name_ar=user.full_name_ar,
                scheduled_days=0,
                present_days=0,
                absent_days=0,
                late_days=0,
                late_minutes=0,
                worked_minutes=0,
                overtime_minutes=0,
                open_punches=0,
            )
            scheduled_dates[key] = set()
            present_dates[key] = set()
        return people[key]

    for shift in shifts:
        row = row_for(shift.user)
        scheduled_dates[row.user_id].add(shift.business_date)

    for punch in punches:
        row = row_for(punch.user)
        present_dates[row.user_id].add(punch.business_date)

        late = punch.late_minutes(grace_minutes=grace_for(punch.shift, config))
        if late:
            row.late_days += 1
            row.late_minutes += late

        worked = punch.worked_minutes
        if worked is None:
            # An open punch contributes no hours. Counting the time since
            # check-in would grow somebody's wage every time the page reloaded.
            row.open_punches += 1
            continue
        row.worked_minutes += worked
        if worked > overtime_after:
            row.overtime_minutes += worked - overtime_after

    for row in people.values():
        row.scheduled_days = len(scheduled_dates[row.user_id])
        row.present_days = len(present_dates[row.user_id])
        row.absent_days = len(scheduled_dates[row.user_id] - present_dates[row.user_id])

    return sorted(people.values(), key=lambda r: r.name_ar)


def open_punches_past(branch, *, hours: int | None = None, now: datetime | None = None):
    """
    Punches still open after N hours — the ones a human has to go and ask about.

    This is the whole reason nothing auto-closes. The alert sweep reads it.
    """
    now = now or timezone.now()
    config = settings_for(branch)
    limit = hours if hours is not None else int(config["hr.missing_checkout_hours"])
    cutoff = now - timedelta(hours=limit)

    return (
        Attendance.objects.filter(
            branch=branch,
            checked_out_at__isnull=True,
            amended_out_at__isnull=True,
            checked_in_at__lt=cutoff,
        )
        .select_related("user")
        .order_by("checked_in_at")
    )


# ── plumbing ─────────────────────────────────────────────────────────────────


def _event(attendance: Attendance, kind: str, at: datetime, actor, detail: dict) -> None:
    AttendanceEvent.objects.create(
        attendance=attendance, kind=kind, at=at, actor=actor, detail=detail
    )


def _audit(action: str, attendance: Attendance, **kwargs) -> None:
    from apps.audit import services as audit

    audit.record(
        action,
        organization=attendance.organization,
        branch=attendance.branch,
        obj=attendance,
        object_label=f"{attendance.user.full_name_ar} — {attendance.business_date}",
        **kwargs,
    )
