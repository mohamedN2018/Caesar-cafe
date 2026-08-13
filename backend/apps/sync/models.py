"""
The synchronization engine's storage (docs/07).

Three tables carry the whole design:

  `ChangeLog`     server → device. A monotonic BIGSERIAL feed, never a
                  timestamp — see the class docstring for why that choice is
                  load-bearing rather than a preference.
  `SyncOperation` device → server. `op_uuid` is UNIQUE, which makes replaying a
                  batch structurally impossible rather than merely handled.
  `SyncConflict`  the small residue that a human has to look at.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

from apps.core.models import BaseModel


class Stream(models.TextChoices):
    """
    Pull streams, polled at different cadences because their staleness
    tolerances differ. A price list may be a minute old; another device's open
    table may not.
    """

    CONFIG = "config", "config"
    CATALOG = "catalog", "catalog"
    FLOOR = "floor", "floor"
    STAFF = "staff", "staff"
    ORDERS = "orders", "orders"
    KIDS = "kids", "kids"


class Operation(models.TextChoices):
    UPSERT = "UPSERT", "UPSERT"
    DELETE = "DELETE", "DELETE"


class ChangeLog(models.Model):
    """
    One server-side change, in a strictly increasing sequence.

    **Why not `?since=<timestamp>`** — the obvious design, and subtly broken in
    three ways (docs/07):

      * the client's clock is not the server's, so the window is wrong in a
        direction nobody can predict;
      * a row written at 13:59:59 inside a transaction that commits at 14:00:01
        becomes visible AFTER a client asked for "everything since 14:00:00",
        and is then skipped forever — rare, silent, unreproducible;
      * rows sharing a millisecond force a choice between skipping some and
        re-sending some.

    A BIGSERIAL has none of those properties. It does have one of its own: the
    value is assigned at INSERT and becomes visible at COMMIT, so seq 100 can
    commit after seq 101 is already readable, and a reader at exactly the wrong
    moment skips 100. `txid` is what closes that hole — see `services.pull`.
    """

    seq = models.BigAutoField(primary_key=True)

    branch = models.ForeignKey(
        "organizations.Branch", on_delete=models.CASCADE, related_name="changes"
    )
    stream = models.CharField(max_length=16, choices=Stream.choices)
    entity_type = models.CharField(max_length=48)
    entity_id = models.UUIDField()
    operation = models.CharField(max_length=8, choices=Operation.choices)
    payload = models.JSONField(default=dict, blank=True)

    txid = models.BigIntegerField(
        db_index=True,
        help_text=(
            "The Postgres transaction that wrote this row (pg_current_xact_id). "
            "A pull never serves rows from a transaction that might still be "
            "in flight, which is what stops a concurrent writer's row from "
            "landing behind a cursor that has already moved past it."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "sync_change_log"
        ordering = ["seq"]
        indexes = [
            models.Index(fields=["branch", "stream", "seq"], name="idx_change_branch_stream"),
        ]

    def __str__(self) -> str:
        return f"#{self.seq} {self.stream}/{self.entity_type} {self.operation}"


class OperationStatus(models.TextChoices):
    PENDING = "PENDING", "PENDING"
    APPLIED = "APPLIED", "APPLIED"
    CONFLICT = "CONFLICT", "CONFLICT"
    REJECTED = "REJECTED", "REJECTED"


class SyncOperation(BaseModel):
    """
    One pushed operation, recorded before it is attempted.

    `op_uuid` is client-minted and UNIQUE. That single constraint is the entire
    idempotency story: a replayed batch cannot create a second row, so it cannot
    create a second sale. Retry logic is not being clever — a duplicate insert
    is simply impossible.

    The row is written even when the operation goes on to fail, because "this
    terminal tried to do something and it was rejected" is exactly the fact an
    admin needs and the fact a silent failure loses.
    """

    op_uuid = models.UUIDField(unique=True, db_index=True)
    batch_id = models.UUIDField(null=True, blank=True, db_index=True)

    device = models.ForeignKey(
        "licensing.Device", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    branch = models.ForeignKey(
        "organizations.Branch", on_delete=models.CASCADE, related_name="sync_operations"
    )
    actor = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    entity_type = models.CharField(max_length=48)
    entity_id = models.UUIDField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)

    status = models.CharField(
        max_length=12, choices=OperationStatus.choices, default=OperationStatus.PENDING
    )
    result = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=48, blank=True)
    error_message = models.TextField(blank=True)

    client_seq = models.BigIntegerField(
        null=True, blank=True, help_text="The device's outbox ordering. Preserves causality."
    )
    aggregate_seq = models.IntegerField(
        null=True,
        blank=True,
        help_text=(
            "The device's per-aggregate sequence, e.g. this order's 4th event "
            "from this terminal. What SEQUENCE_GAP detection compares against — "
            "the server assigns its own order sequence, so the client's number "
            "is only useful for spotting a hole in the device's own stream."
        ),
    )
    client_time = models.DateTimeField(
        null=True, blank=True, help_text="The device's clock — may be skewed."
    )
    clock_skew_seconds = models.IntegerField(
        default=0,
        help_text="client_time − server time at receipt. Recorded, never used for anything.",
    )
    received_at = models.DateTimeField(default=timezone.now, db_index=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "sync_operations"
        ordering = ["received_at"]
        indexes = [
            models.Index(fields=["branch", "status"], name="idx_op_branch_status"),
            models.Index(fields=["device", "-received_at"], name="idx_op_device_time"),
        ]

    def __str__(self) -> str:
        return f"{self.entity_type} {self.op_uuid} ({self.status})"


class SyncConflict(BaseModel):
    """
    Something a human has to decide.

    There are deliberately very few of these. Order events commute, payments are
    idempotent, and stock is server-computed, so the whole generic-merge problem
    is designed out rather than solved. What is left is real: an item added to an
    order another device already paid, or an event referencing a product
    deactivated in the meantime.
    """

    operation = models.ForeignKey(SyncOperation, on_delete=models.CASCADE, related_name="conflicts")
    branch = models.ForeignKey(
        "organizations.Branch", on_delete=models.CASCADE, related_name="sync_conflicts"
    )
    code = models.CharField(max_length=48)
    message_ar = models.CharField(max_length=250)
    server_state = models.JSONField(
        default=dict, blank=True, help_text="What the server believed, so the human can compare."
    )

    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    resolution = models.CharField(max_length=32, blank=True)
    resolution_note = models.CharField(max_length=250, blank=True)

    class Meta:
        db_table = "sync_conflicts"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["branch", "resolved_at"], name="idx_conflict_branch_open"),
        ]

    def __str__(self) -> str:
        return f"{self.code} ({'resolved' if self.resolved_at else 'open'})"

    @property
    def is_open(self) -> bool:
        return self.resolved_at is None


class DeviceCursor(BaseModel):
    """
    Where a device has read up to, per stream.

    Kept server-side as well as on the device so the Web Admin can answer "is
    that terminal actually up to date?" without asking the terminal — which is
    precisely the question you cannot ask a terminal that has stopped talking.
    """

    device = models.ForeignKey("licensing.Device", on_delete=models.CASCADE, related_name="cursors")
    stream = models.CharField(max_length=16, choices=Stream.choices)
    cursor = models.BigIntegerField(default=0)
    last_pulled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "sync_device_cursors"
        constraints = [
            models.UniqueConstraint(fields=["device", "stream"], name="uniq_cursor_per_stream")
        ]

    def __str__(self) -> str:
        return f"{self.device_id}/{self.stream} @ {self.cursor}"
