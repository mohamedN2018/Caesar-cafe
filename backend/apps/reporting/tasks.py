"""
Scheduled work.

`build_rollups` runs nightly, after the business day has closed. It is safe to
run at any time and any number of times — `build_day` is delete-then-insert, so
a beat that fires twice or a manual backfill overlapping the nightly job cannot
double a day's revenue.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task

from apps.organizations.models import Branch

from . import business_day, rollups

logger = logging.getLogger(__name__)


@shared_task(name="reporting.build_rollups")
def build_rollups(days: int = 1) -> dict:
    """
    Roll up the last `days` CLOSED business days for every branch.

    Defaults to 1 — yesterday. A larger window is the cheap self-healing
    mechanism: if the worker was down for a weekend, Monday's run backfills it
    without anyone noticing, because rebuilding a day that already exists costs
    the same as building one that does not.
    """
    built = {}
    for branch in Branch.objects.filter(is_active=True):
        today = business_day.today(branch)
        for offset in range(1, days + 1):
            day = today - timedelta(days=offset)
            rollups.build_day(branch, day)
        built[branch.code] = days

    logger.info("Nightly rollups complete", extra={"branches": len(built), "days": days})
    return built


@shared_task(name="reporting.backfill")
def backfill(branch_id: str, date_from: str, date_to: str) -> int:
    """Rebuild a range on demand — after a fold fix, or to seed a deployment."""
    from datetime import date

    branch = Branch.objects.get(id=branch_id)
    return rollups.backfill(branch, date.fromisoformat(date_from), date.fromisoformat(date_to))
