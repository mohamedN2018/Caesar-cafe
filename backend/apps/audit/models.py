"""
The audit log (docs/09, threats R1–R3 and T5).

Append-only, and enforced at three levels rather than trusted at one:

  1. `delete()` and the queryset's `delete()` raise. A developer who tries to
     tidy up an audit row gets a traceback, not a silent hole.
  2. There is no delete endpoint, and the only write path is `services.record`.
  3. The production DB role is documented as lacking `DELETE` on this table
     (docs/13). Application-level guards do not survive a `manage.py shell`;
     the grant does.

`before` and `after` are stored rather than only a diff, because "what did the
row look like before this happened" is the question a dispute actually asks, and
reconstructing it from a chain of diffs six months later is a research project.
"""

from __future__ import annotations

from django.db import models

from .actions import Severity


class AuditQuerySet(models.QuerySet):
    def delete(self):
        raise PermissionError(
            "AuditLog is append-only. Deleting audit rows is how 'I never voided "
            "that order' becomes unanswerable."
        )


class AuditLog(models.Model):
    """
    One recorded action.

    Every field that identifies WHO is snapshotted alongside its foreign key. A
    user deactivated next year must still be nameable in this year's record, and
    `on_delete=SET_NULL` alone would leave a row that says nothing.
    """

    id = models.BigAutoField(primary_key=True)

    action = models.CharField(max_length=48, db_index=True)
    domain = models.CharField(max_length=24, db_index=True)
    severity = models.CharField(
        max_length=8, choices=[(s.value, s.value) for s in Severity], default=Severity.INFO
    )

    organization = models.ForeignKey(
        "organizations.Organization",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="audit_logs",
        help_text=(
            "Null for events that belong to no tenant — a failed login against "
            "an address that does not exist. Dropping those would blind exactly "
            "the credential-stuffing case the record exists for, so they are "
            "kept and are visible only to a superuser."
        ),
    )
    branch = models.ForeignKey(
        "organizations.Branch",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
    )

    actor = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    actor_name = models.CharField(
        max_length=150, blank=True, help_text="Snapshotted — a deleted user must still be nameable."
    )
    approved_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    approved_by_name = models.CharField(max_length=150, blank=True)
    device_id = models.UUIDField(null=True, blank=True, db_index=True)

    object_type = models.CharField(max_length=48, blank=True)
    object_id = models.CharField(max_length=64, blank=True, db_index=True)
    object_label = models.CharField(
        max_length=250, blank=True, help_text="Human-readable, snapshotted: 'MB-01-0042'."
    )

    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    changes = models.JSONField(
        default=dict, blank=True, help_text="Only the fields that moved: {field: [old, new]}."
    )
    detail = models.JSONField(
        default=dict,
        blank=True,
        help_text="Context that is not a field change — a reason, an amount.",
    )

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=250, blank=True)
    request_id = models.CharField(max_length=64, blank=True, db_index=True)

    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = AuditQuerySet.as_manager()

    class Meta:
        db_table = "audit_log"
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(fields=["organization", "-occurred_at"], name="idx_audit_org_time"),
            models.Index(fields=["branch", "action"], name="idx_audit_branch_action"),
            models.Index(fields=["actor", "-occurred_at"], name="idx_audit_actor_time"),
            models.Index(fields=["object_type", "object_id"], name="idx_audit_object"),
        ]

    def __str__(self) -> str:
        return f"{self.action} by {self.actor_name or '—'} at {self.occurred_at:%Y-%m-%d %H:%M}"

    def save(self, *args, **kwargs):
        """
        Insert only. An audit row that can be edited is a record of what somebody
        most recently claimed, not of what happened.
        """
        if self.pk is not None:
            raise PermissionError("AuditLog rows are immutable once written.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("AuditLog is append-only.")
