"""
Who was supposed to be here, and who actually was.

**A note on the word "shift", because this app and `apps/shifts` both use it and
they are not the same thing.** `shifts.Shift` is a *cash drawer*: it opens with a
float, closes with a count, and produces a variance. This app's `WorkShift` is a
*rota slot*: a person, a date, and the hours they were expected. A waiter has a
work shift and never touches a drawer; a manager may open three drawers in one
work shift. Conflating them would put a cash variance against a waiter and leave
the kitchen off the rota entirely, so they stay separate models with separate
tables and this paragraph exists so nobody merges them later.

The governing rule here mirrors the one inventory follows. **`Attendance` is the
record of what happened; the timesheet is a projection.** Hours worked, lateness
and overtime are all computed from the stored punches on read, never stored as
columns that a later edit could leave disagreeing with the times they came from.
A cafe argues about wages, and "the system says 8 hours but the punches say 7:12"
is an argument nobody can win.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.db import models

from apps.core.models import BaseModel, SoftDeletableModel, TenantScopedModel


class WorkPattern(TenantScopedModel, SoftDeletableModel):
    """
    A named set of hours — "صباحي ٨:٠٠–١٦:٠٠".

    A pattern rather than typing times onto every rota slot, because a cafe runs
    the same three or four shapes all year and a manager building next week's
    rota should be choosing, not typing. The times live here so that correcting
    "the morning now starts at 07:30" is one edit instead of ninety.

    `crosses_midnight` is derived, not stored: an end time earlier than the start
    means the shift runs into tomorrow, and storing a flag somebody could set
    inconsistently with the times themselves is a second source of truth.
    """

    name_ar = models.CharField(max_length=100)
    starts_at = models.TimeField()
    ends_at = models.TimeField()

    #: Minutes after `starts_at` that are still counted as on time for slots on
    #: this pattern. Null falls back to the branch setting — the override exists
    #: because a delivery shift that starts when the van arrives cannot hold the
    #: same grace as a counter shift with a queue at the door.
    grace_minutes = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = "hr_work_patterns"
        ordering = ["starts_at", "name_ar"]
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "name_ar"], name="uniq_hr_pattern_name_per_branch"
            )
        ]

    def __str__(self) -> str:
        return f"{self.name_ar} ({self.starts_at:%H:%M}–{self.ends_at:%H:%M})"

    @property
    def crosses_midnight(self) -> bool:
        return self.ends_at <= self.starts_at

    @property
    def scheduled_minutes(self) -> int:
        start = datetime.combine(date(2000, 1, 1), self.starts_at)
        end = datetime.combine(date(2000, 1, 1), self.ends_at)
        if end <= start:
            end += timedelta(days=1)
        return int((end - start).total_seconds() // 60)


class WorkShift(TenantScopedModel):
    """
    One person, one business day, one pattern. A rota slot.

    Keyed on the **business date**, not on a wall-clock date, because a cafe that
    closes at 02:00 has staff whose shift begins on Tuesday and ends on
    Wednesday. `finance.business_day_start` already decides which trading day an
    order belongs to, and the rota has to answer to the same boundary or the
    timesheet and the sales report will disagree about which day somebody worked.

    A slot is unique per person per business date: a cafe that genuinely
    double-shifts somebody wants that visible as an explicit second pattern, not
    as two rows nobody notices.
    """

    user = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="work_shifts")
    pattern = models.ForeignKey(WorkPattern, on_delete=models.PROTECT, related_name="shifts")
    business_date = models.DateField()

    #: A rota is a plan, and plans get changed by a person who should be named.
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "hr_work_shifts"
        ordering = ["-business_date", "user__full_name_ar"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "business_date"], name="uniq_hr_shift_per_person_per_day"
            )
        ]
        indexes = [
            models.Index(fields=["branch", "business_date"], name="idx_hr_shift_branch_date"),
        ]

    def __str__(self) -> str:
        return f"{self.user.full_name_ar} — {self.business_date} {self.pattern.name_ar}"


class PunchSource(models.TextChoices):
    """
    How a punch got here. Kept because the three are not equally trustworthy.

    TERMINAL is somebody standing at the till. MANAGER is somebody typing on
    their behalf, which is legitimate and also the one an audit reads first.
    """

    TERMINAL = "TERMINAL", "TERMINAL"
    MANAGER = "MANAGER", "MANAGER"
    AMENDED = "AMENDED", "AMENDED"


class Attendance(TenantScopedModel):
    """
    What actually happened: one arrival, one departure.

    **Not deleted and not silently edited.** A correction sets `amended_*`
    alongside the original punch rather than over it, so "he clocked in at 09:40
    and the manager changed it to 09:00" is answerable a month later when the
    wage is disputed. The original is the evidence; the amendment is a decision
    somebody made and put their name to.

    **`checked_out_at` is nullable and nothing closes it automatically.** The same
    argument the kids area settles the same way: an automatic close records
    somebody as having gone home when nobody saw them leave, and a wage computed
    from an invented departure is worse than a gap a human is asked about.
    `hr.missing_checkout_hours` raises an alert for a person to go and look.
    """

    user = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="attendance")
    business_date = models.DateField()

    #: The rota slot this is being measured against, if there was one. Null is a
    #: real state and not an error: somebody who came in on their day off has
    #: attendance and no schedule, and the timesheet has to show that rather
    #: than drop the hours.
    shift = models.ForeignKey(
        WorkShift, null=True, blank=True, on_delete=models.SET_NULL, related_name="attendance"
    )

    checked_in_at = models.DateTimeField()
    checked_out_at = models.DateTimeField(null=True, blank=True)

    source = models.CharField(max_length=10, choices=PunchSource.choices)

    #: An amendment writes here and leaves the punches above untouched.
    amended_in_at = models.DateTimeField(null=True, blank=True)
    amended_out_at = models.DateTimeField(null=True, blank=True)
    amendment_reason = models.CharField(max_length=200, blank=True)
    amended_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="attendance_amendments",
    )

    note = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "hr_attendance"
        ordering = ["-business_date", "-checked_in_at"]
        constraints = [
            # One attendance row per person per business day. A second arrival on
            # the same day is a re-open of this row, not a new one, or the
            # timesheet double-counts a break somebody took.
            models.UniqueConstraint(
                fields=["user", "business_date"], name="uniq_hr_attendance_per_person_per_day"
            ),
        ]
        indexes = [
            models.Index(fields=["branch", "business_date"], name="idx_hr_att_branch_date"),
            models.Index(fields=["user", "-business_date"], name="idx_hr_att_user_date"),
        ]

    def __str__(self) -> str:
        return f"{self.user.full_name_ar} — {self.business_date}"

    # ── the effective times ──────────────────────────────────────────────────
    #
    # Everything downstream reads these, never the raw columns. An amendment is
    # what the payroll should use; the original is what the audit should show.

    @property
    def effective_in(self) -> datetime:
        return self.amended_in_at or self.checked_in_at

    @property
    def effective_out(self) -> datetime | None:
        return self.amended_out_at or self.checked_out_at

    @property
    def is_amended(self) -> bool:
        return self.amended_in_at is not None or self.amended_out_at is not None

    @property
    def is_open(self) -> bool:
        return self.effective_out is None

    @property
    def worked_minutes(self) -> int | None:
        """None while the shift is open — an unknown is not a zero."""
        out = self.effective_out
        if out is None:
            return None
        return max(0, int((out - self.effective_in).total_seconds() // 60))

    def late_minutes(self, *, grace_minutes: int) -> int:
        """
        How late, against the rota slot and its grace window.

        Zero when there is no slot to be late for. Somebody who came in on a day
        they were not rostered cannot be late, and reporting them as late by the
        whole morning is how a report loses its reader.
        """
        if self.shift is None:
            return 0

        expected = self._expected_start()
        allowed = expected + timedelta(minutes=grace_minutes)
        if self.effective_in <= allowed:
            return 0
        return int((self.effective_in - expected).total_seconds() // 60)

    def _expected_start(self) -> datetime:
        """
        The rostered start, as an instant.

        Built from the business date and the pattern's start time in the current
        timezone. A pattern that starts before the business-day boundary belongs
        to the following calendar day — an 02:00 start on a cafe whose day begins
        at 06:00 is tomorrow morning, not fourteen hours ago.
        """
        from django.utils import timezone

        from apps.reporting import business_day

        assert self.shift is not None
        boundary = business_day.boundary_for(self.branch)
        start_time: time = self.shift.pattern.starts_at

        calendar_date = self.business_date
        if start_time < boundary:
            calendar_date = calendar_date + timedelta(days=1)

        naive = datetime.combine(calendar_date, start_time)
        return timezone.make_aware(naive, timezone.get_current_timezone())


class AttendanceEvent(BaseModel):
    """
    An append-only trail of every punch and correction.

    `Attendance` holds the current answer, which is what a screen needs. This
    holds how it got there, which is what a wage dispute needs. The audit log
    records it too — this exists in addition because the audit log is
    organisation-wide and read by an administrator, while this is a per-person
    history a manager can be shown next to the timesheet without granting them
    `audit.view` over the whole cafe.
    """

    class Kind(models.TextChoices):
        CHECK_IN = "CHECK_IN", "CHECK_IN"
        CHECK_OUT = "CHECK_OUT", "CHECK_OUT"
        AMENDED = "AMENDED", "AMENDED"

    attendance = models.ForeignKey(Attendance, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    at = models.DateTimeField()
    actor = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "hr_attendance_events"
        ordering = ["at"]

    def __str__(self) -> str:
        return f"{self.kind} @ {self.at:%Y-%m-%d %H:%M}"
