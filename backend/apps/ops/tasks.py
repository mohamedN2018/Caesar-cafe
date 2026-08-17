"""
Scheduled operations.

The nightly backup runs at 03:00 — before the 04:00 business-day boundary, so a
dump lands while the previous trading day is still complete and nothing is
half-written into the next one.
"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="ops.nightly_backup")
def nightly_backup() -> dict:
    """
    Take a backup, then prune. In that order, always.

    Pruning first would, on the one night the dump fails, delete the oldest copy
    and add nothing — a retention policy that quietly shortens itself every time
    something goes wrong.
    """
    from . import backups

    record = backups.create()
    removed = backups.prune() if record.status == "COMPLETE" else []

    if record.status != "COMPLETE":
        logger.error(
            "Nightly backup failed — retention untouched",
            extra={"backup_file": record.filename, "error": record.error},
        )

    return {
        "filename": record.filename,
        "status": record.status,
        "size_mb": record.size_mb,
        "pruned": len(removed),
    }


@shared_task(name="ops.verify_last_backup")
def verify_last_backup() -> dict:
    """
    Re-digest the most recent backup.

    Cheap, and catches the truncation case — a dump cut short when the disk
    filled looks like a valid file until somebody tries to read it.
    """
    from . import backups
    from .models import BackupRecord, BackupStatus

    last = BackupRecord.objects.filter(status=BackupStatus.COMPLETE).first()
    if last is None:
        logger.warning("No backup to verify")
        return {"verified": False, "reason": "no backups"}

    ok = backups.verify(last)
    if not ok:
        logger.error("Backup verification FAILED", extra={"backup_file": last.filename})

    return {"verified": ok, "filename": last.filename}


# ── demo data, switched from a screen ────────────────────────────────────────
#
# The demo-data screen offers two states — a full fortnight of trading, and a
# configured cafe with an empty ledger — and both are the SEED, not new
# machinery: `seed_demo --reset` already deletes in the dependency order that
# PROTECT demands, and `--no-live` is the only thing this feature added to it.
#
# A Celery task and not a view, for the same reason the boot no longer seeds:
# a fortnight of trading is thousands of orders through the real order and
# payment services, and a request that takes minutes is a gunicorn worker held
# hostage and a browser that gave up long ago. The worker container is the one
# place built for exactly this.

DEMO_JOB_KEY = "ops:demo-data:job"
DEMO_JOB_TTL = 60 * 60  # a finished state readable for an hour, then silence


def _set_job(state: str, mode: str, detail: str = "") -> None:
    from django.core.cache import cache
    from django.utils import timezone

    cache.set(
        DEMO_JOB_KEY,
        {"state": state, "mode": mode, "detail": detail, "at": timezone.now().isoformat()},
        DEMO_JOB_TTL,
    )


@shared_task(name="ops.switch_demo_data")
def switch_demo_data(mode: str, days: int = 14) -> str:
    """
    Rebuild the demo dataset in one of its two shapes.

    `full`  — `seed_demo --reset --days N`: the fortnight, the seated room, the
              kitchen mid-service.
    `empty` — `seed_demo --reset --days 0 --no-live`: the same cafe, configured
              and licensed, with nothing sold yet. Every screen shows its honest
              empty state and the till still works.

    Either way the licence is reissued and every device dies with the old one —
    that is `--reset`'s documented behaviour, not a side effect — so the status
    payload warns about it BEFORE the button, and DEMO_MODE keeps the new key
    readable on the licensing screen.
    """
    from io import StringIO

    from django.core.cache import cache
    from django.core.management import call_command

    # A second gate, HERE, at the point of damage — not only in the view.
    #
    # The view's gate stops two clicks; it cannot stop two QUEUED tasks from
    # running together, and that happened: a rebuild crashed fast, its failure
    # state legitimately let a second click through, and for half a second two
    # seeds overlapped — one deleting the catalogue while the other sold from
    # it, `VARIANT_NOT_FOUND` mid-trade, and a database in neither shape.
    # Concurrency is celery's (=2), so the exclusion has to be the task's own.
    lock = f"{DEMO_JOB_KEY}:lock"
    if not cache.add(lock, mode, 60 * 30):
        _set_job("failed", mode, "عملية أخرى كانت قائمة — أعد المحاولة بعد انتهائها.")
        return "skipped"

    _set_job("running", mode)
    try:
        out = StringIO()
        if mode == "empty":
            call_command("seed_demo", reset=True, days=0, no_live=True, stdout=out)
        else:
            call_command("seed_demo", reset=True, days=days, stdout=out)
        _set_job("done", mode)
        return "ok"
    except Exception as exc:
        # The state carries the message because the screen is the only place
        # anybody will look — a failure that lives in worker logs is a spinner
        # that never stops.
        _set_job("failed", mode, str(exc))
        raise
    finally:
        cache.delete(lock)
