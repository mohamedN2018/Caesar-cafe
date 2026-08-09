"""
Who gets told, and what they were told already.

Two tables, and the second one is the interesting one. Sending a notification is
easy; **not sending the same one nine times** is what makes the feature usable
rather than something the owner turns off in a week.
"""

from __future__ import annotations

from django.db import models

from apps.core.models import BaseModel, TenantScopedModel


class PushSubscription(TenantScopedModel):
    """
    One browser on one device that has agreed to be told.

    The three opaque fields come from the browser's `PushSubscription` and are
    useless without each other: `endpoint` says which push service, `p256dh` is
    the key the payload is encrypted to, `auth` salts the derivation. None of
    them is a secret we chose, and none can be regenerated from our side — a
    lost row means the owner re-enables notifications, which is why it is a row
    and not a cache.
    """

    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="push_subscriptions"
    )
    endpoint = models.TextField(unique=True)
    p256dh = models.CharField(max_length=200)
    auth = models.CharField(max_length=100)

    #: What the browser called itself. Purely so the owner can tell "my phone"
    #: from "the office laptop" when revoking one.
    label = models.CharField(max_length=120, blank=True)

    last_sent_at = models.DateTimeField(null=True, blank=True)
    #: Consecutive delivery failures. A push service that has refused several
    #: times is usually a browser that was uninstalled without telling anybody.
    failures = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "push_subscriptions"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "branch"], name="idx_push_user_branch")]

    def __str__(self) -> str:
        return f"{self.user_id} @ {self.endpoint[:40]}…"


class AlertKind(models.TextChoices):
    """
    The things worth interrupting somebody for.

    Deliberately short. Every entry here is a decision that this event is worth
    a phone buzzing in a pocket, and the failure mode of a long list is an owner
    who mutes all of them — at which point the one that mattered is also muted.
    """

    CASH_VARIANCE = "CASH_VARIANCE", "CASH_VARIANCE"
    """A drawer closed short or over by more than the configured tolerance."""

    KITCHEN_LATE = "KITCHEN_LATE", "KITCHEN_LATE"
    """A ticket has been waiting far past its station's target."""

    KIDS_OVERDUE = "KIDS_OVERDUE", "KIDS_OVERDUE"
    """A child is well past their session's expected end."""

    TERMINAL_OFFLINE = "TERMINAL_OFFLINE", "TERMINAL_OFFLINE"
    """A device has not synced for long enough that sales are at risk."""

    BACKUP_FAILED = "BACKUP_FAILED", "BACKUP_FAILED"
    """Last night's backup did not complete. The quietest serious failure there is."""

    SYNC_CONFLICT = "SYNC_CONFLICT", "SYNC_CONFLICT"
    """An operation needs a human and has been waiting."""


class SentAlert(BaseModel):
    """
    One alert about one thing, recorded so it is not sent again.

    This table is the difference between a useful feature and a nuisance. The
    task that raises alerts runs every few minutes and re-evaluates the same
    conditions — a ticket that is late at 20:14 is still late at 20:19 — so
    without a record of what has already gone out, one late order becomes a
    notification every five minutes until somebody makes the food.

    `dedupe_key` identifies the SUBJECT, not the event: "ticket 94 is late", not
    "an alert was raised". Two evaluations of the same subject collapse; a
    genuinely new late ticket does not.
    """

    kind = models.CharField(max_length=24, choices=AlertKind.choices)
    branch = models.ForeignKey(
        "organizations.Branch", on_delete=models.CASCADE, related_name="sent_alerts"
    )
    dedupe_key = models.CharField(max_length=200)
    title = models.CharField(max_length=200)
    body = models.TextField()
    #: Where tapping the notification should land. An alert that cannot be acted
    #: on is a worse version of a text message.
    url = models.CharField(max_length=200, blank=True)
    delivered = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "sent_alerts"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "kind", "dedupe_key"], name="uniq_alert_per_subject"
            )
        ]
        indexes = [models.Index(fields=["branch", "-created_at"], name="idx_alert_branch_time")]

    def __str__(self) -> str:
        return f"{self.kind}: {self.dedupe_key}"
