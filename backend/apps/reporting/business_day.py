"""
The business day (assumption A5).

A cafe that closes at 2am does not think of 01:30 as tomorrow, and neither does
its cashier, its Z-report, or its owner comparing Tuesday to Monday. So the day
boundary is `finance.business_day_start` — 04:00 by default, editable per branch
— and EVERY report derives its window from this one function.

Two rules that this module exists to keep:

  * There is exactly one definition of "Tuesday". A report that computed its own
    midnight boundary would disagree with the shift summary sitting next to it,
    and the owner would have no way to tell which one was wrong.

  * Changing the setting never rewrites history. A rollup records the boundary
    it was computed under, so last month's numbers keep meaning what they meant.
    Only days recomputed after the change use the new boundary, and they are
    labelled with it.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.utils import timezone

from apps.configuration import resolver
from apps.configuration.resolver import ScopeContext

__all__ = [
    "boundary_for",
    "business_date_of",
    "day_window",
    "range_window",
    "split_days",
]


def boundary_for(branch) -> time:
    """The branch's configured day start."""
    context = ScopeContext(organization_id=branch.organization_id, branch_id=branch.id)
    return resolver.get("finance.business_day_start", context)


def day_window(branch, business_date: date, *, boundary: time | None = None) -> tuple:
    """
    The half-open instant range `[start, end)` for one business date.

    Half-open on purpose: a sale at exactly the boundary belongs to precisely one
    day, and no order can be counted twice or dropped between two adjacent
    reports.
    """
    boundary = boundary or boundary_for(branch)
    tz = timezone.get_current_timezone()

    start = timezone.make_aware(datetime.combine(business_date, boundary), tz)
    end = timezone.make_aware(datetime.combine(business_date + timedelta(days=1), boundary), tz)
    return start, end


def business_date_of(branch, moment, *, boundary: time | None = None) -> date:
    """
    Which business date an instant falls on.

    01:30 on Wednesday with a 04:00 boundary is still Tuesday — which is what the
    cashier who is standing there would say.
    """
    boundary = boundary or boundary_for(branch)
    local = timezone.localtime(moment)
    return local.date() - timedelta(days=1) if local.time() < boundary else local.date()


def range_window(branch, date_from: date, date_to: date, *, boundary: time | None = None) -> tuple:
    """
    The instant range covering business dates `date_from`..`date_to` INCLUSIVE.

    Inclusive because a human asking for "1st to 7th" means seven days. Getting
    this wrong by one day is the classic reporting bug, and it is invisible
    until somebody reconciles a month by hand.
    """
    boundary = boundary or boundary_for(branch)
    start, _ = day_window(branch, date_from, boundary=boundary)
    _, end = day_window(branch, date_to, boundary=boundary)
    return start, end


def split_days(date_from: date, date_to: date):
    """Every business date in an inclusive range."""
    current = date_from
    while current <= date_to:
        yield current
        current += timedelta(days=1)


def today(branch, *, boundary: time | None = None) -> date:
    """The business date currently in progress."""
    return business_date_of(branch, timezone.now(), boundary=boundary)
