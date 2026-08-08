"""
Backup bookkeeping.

The row exists so that "do we have a backup?" is answerable from the application
rather than by SSH-ing to a host and reading a directory listing. A backup nobody
can see the status of is a backup nobody knows is broken.

The file itself is NOT in the database — only its name, size and digest. Storing
the dump inside the database it is a dump of would be its own joke.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models


class BackupStatus(models.TextChoices):
    RUNNING = "RUNNING", "RUNNING"
    COMPLETE = "COMPLETE", "COMPLETE"
    FAILED = "FAILED", "FAILED"


class BackupRecord(models.Model):
    id = models.BigAutoField(primary_key=True)

    filename = models.CharField(max_length=200, unique=True)
    size_bytes = models.BigIntegerField(default=0)
    sha256 = models.CharField(
        max_length=64,
        blank=True,
        help_text="Of the file as written. What a restore checks before trusting it.",
    )
    encrypted = models.BooleanField(default=False)

    status = models.CharField(
        max_length=12, choices=BackupStatus.choices, default=BackupStatus.RUNNING
    )
    error = models.TextField(blank=True)

    started_at = models.DateTimeField(auto_now_add=True, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.IntegerField(default=0)

    triggered_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Null for the scheduled nightly run.",
    )

    class Meta:
        db_table = "ops_backups"
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"{self.filename} ({self.status})"

    @property
    def size_mb(self) -> Decimal:
        """
        A string on the wire, like every other number in this API.

        `float` would be harmless here — a file size is not money — but the
        architecture guard that forbids floats is absolute on purpose. A guard
        with one convenient exception is a guard with two next year.
        """
        return (Decimal(self.size_bytes) / Decimal(1_048_576)).quantize(Decimal("0.01"))
