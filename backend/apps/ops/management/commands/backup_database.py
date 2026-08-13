"""
python manage.py backup_database
python manage.py backup_database --prune
python manage.py backup_database --verify
python manage.py backup_database --list
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.ops import backups
from apps.ops.models import BackupRecord, BackupStatus


class Command(BaseCommand):
    help = "Take a database backup, or inspect the existing ones."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--label", default="", help="Suffix for the filename.")
        parser.add_argument(
            "--prune", action="store_true", help="Apply the retention policy after."
        )
        parser.add_argument("--verify", action="store_true", help="Re-digest the latest backup.")
        parser.add_argument("--list", action="store_true", help="Show recent backups and exit.")

    def handle(self, *args, **options) -> None:
        if options["list"]:
            self._list()
            return

        if options["verify"]:
            self._verify()
            return

        record = backups.create(label=options["label"])

        if record.status != BackupStatus.COMPLETE:
            # A non-zero exit is what lets cron, a CI job, or a human running
            # this before a migration notice that it did not work.
            raise CommandError(f"Backup FAILED: {record.error}")

        self.stdout.write(
            self.style.SUCCESS(
                f"{record.filename} — {record.size_mb} MB in {record.duration_seconds}s"
                f"{' (encrypted)' if record.encrypted else ' (NOT ENCRYPTED)'}"
            )
        )
        if not record.encrypted:
            self.stdout.write(
                self.style.WARNING(
                    "BACKUP_ENCRYPTION_KEY is not set. This file is plaintext — "
                    "acceptable in development, refused in production."
                )
            )

        if options["prune"]:
            removed = backups.prune()
            self.stdout.write(f"pruned {len(removed)} old backup(s)")

    def _list(self) -> None:
        rows = BackupRecord.objects.all()[:20]
        if not rows:
            self.stdout.write("no backups recorded")
            return

        for row in rows:
            mark = "✓" if row.status == BackupStatus.COMPLETE else "✗"
            self.stdout.write(
                f"{mark} {row.started_at:%Y-%m-%d %H:%M}  {row.size_mb:>8} MB  {row.filename}"
            )

        state = backups.status()
        self.stdout.write("")
        self.stdout.write(
            f"last success: {state['last_success']} ({state['hours_since_last']}h ago)"
        )
        if state["failed"]:
            self.stdout.write(self.style.WARNING(f"{state['failed']} failed run(s) recorded"))

    def _verify(self) -> None:
        last = BackupRecord.objects.filter(status=BackupStatus.COMPLETE).first()
        if last is None:
            raise CommandError("No completed backup to verify.")

        if backups.verify(last):
            self.stdout.write(self.style.SUCCESS(f"{last.filename} digest matches"))
        else:
            raise CommandError(
                f"{last.filename} FAILED verification — the file is missing or has changed. "
                "Treat it as unusable and take a fresh backup."
            )
