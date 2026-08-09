"""
The scheduled sweep.

Runs every five minutes, evaluates every active branch, and delivers whatever
is new. Five minutes is a compromise: a late ticket noticed a minute sooner
rarely changes the outcome, and a job that ran every thirty seconds would spend
its life re-reading conditions that had not moved.

The task never raises. It is the same job that checks the backups, and a push
service having a bad afternoon must not stop that.
"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="notifications.sweep")
def sweep() -> dict:
    """Evaluate and deliver for every active branch."""
    import httpx

    from apps.organizations.models import Branch

    from . import services

    try:
        services.keys()
    except services.NotConfigured as exc:
        # Said once per run, at WARNING. A deployment that believes it is
        # sending alerts and is not is worse than one that says it cannot.
        logger.warning("Alerts are not configured", extra={"reason": str(exc)})
        return {"configured": False}

    totals = {"sent": 0, "dropped": 0, "failed": 0, "branches": 0}

    # One connection pool for the whole sweep. Every subscription on a branch
    # usually shares a push service, so reusing the connection is most of the
    # cost of the job.
    with httpx.Client(timeout=services.TIMEOUT) as client:
        for branch in Branch.objects.filter(is_active=True).select_related("organization"):
            try:
                result = services.run_for_branch(branch, client=client)
            except Exception:
                logger.exception(
                    "Alert sweep failed for a branch", extra={"branch": str(branch.id)}
                )
                continue

            totals["sent"] += result.sent
            totals["dropped"] += result.dropped
            totals["failed"] += result.failed
            totals["branches"] += 1

    if totals["sent"] or totals["dropped"]:
        logger.info("Alert sweep complete", extra=totals)

    return {"configured": True, **totals}
