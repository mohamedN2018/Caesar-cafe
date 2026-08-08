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
