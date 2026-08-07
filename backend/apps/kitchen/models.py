"""
Kitchen stations and tickets.

A fired order becomes one ticket PER STATION. A cappuccino and a slice of cake
on the same order are two tickets — coffee bar and dessert — because they are
prepared by different people in different places. The order is READY only when
every one of its tickets is.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

from apps.core.models import BaseModel, SequentialBaseModel, SoftDeletableModel, TenantScopedModel


class Station(TenantScopedModel, SoftDeletableModel):
    code = models.CharField(max_length=32, help_text="COFFEE, HOT, COLD, DESSERT, BAR")
    name_ar = models.CharField(max_length=100)
    target_prep_minutes = models.PositiveSmallIntegerField(default=8)
    auto_accept = models.BooleanField(
        default=False,
        help_text="Skip the ACCEPTED step for stations that never triage, e.g. a bar.",
    )
    printer_name = models.CharField(max_length=100, blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "kitchen_stations"
        ordering = ["sort_order", "name_ar"]
        constraints = [
            models.UniqueConstraint(fields=["branch", "code"], name="uniq_station_code_per_branch")
        ]

    def __str__(self) -> str:
        return self.name_ar


class TicketStatus(models.TextChoices):
    NEW = "NEW", "NEW"
    ACCEPTED = "ACCEPTED", "ACCEPTED"
    PREPARING = "PREPARING", "PREPARING"
    READY = "READY", "READY"
    SERVED = "SERVED", "SERVED"
    CANCELLED = "CANCELLED", "CANCELLED"


#: Legal transitions. Recalling a served ticket is deliberate and bounded.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    TicketStatus.NEW: {TicketStatus.ACCEPTED, TicketStatus.PREPARING, TicketStatus.CANCELLED},
    TicketStatus.ACCEPTED: {TicketStatus.PREPARING, TicketStatus.CANCELLED},
    TicketStatus.PREPARING: {TicketStatus.READY, TicketStatus.CANCELLED},
    TicketStatus.READY: {TicketStatus.SERVED, TicketStatus.PREPARING},
    TicketStatus.SERVED: {TicketStatus.PREPARING},  # recall, within a time limit
    TicketStatus.CANCELLED: set(),
}

OPEN_STATUSES = {
    TicketStatus.NEW,
    TicketStatus.ACCEPTED,
    TicketStatus.PREPARING,
}


class KitchenTicket(SequentialBaseModel):
    """
    One station's share of one firing.

    A second firing on the same order creates a NEW ticket rather than reopening
    the old one: the kitchen has already made the first round, and merging them
    would hide what was actually cooked when.
    """

    order = models.ForeignKey(
        "orders.Order", on_delete=models.CASCADE, related_name="kitchen_tickets"
    )
    station = models.ForeignKey(Station, on_delete=models.PROTECT, related_name="tickets")
    branch = models.ForeignKey(
        "organizations.Branch", on_delete=models.PROTECT, related_name="kitchen_tickets"
    )

    ticket_number = models.PositiveIntegerField(help_text="Per branch per day — what staff shout.")
    status = models.CharField(max_length=16, choices=TicketStatus.choices, default=TicketStatus.NEW)

    accepted_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    served_at = models.DateTimeField(null=True, blank=True)

    prep_seconds = models.PositiveIntegerField(
        null=True, blank=True, help_text="Fired to ready. The raw material for the delay report."
    )
    printed = models.BooleanField(default=False)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "kitchen_tickets"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["branch", "status"], name="idx_ticket_branch_status"),
            models.Index(fields=["station", "status"], name="idx_ticket_station_status"),
            models.Index(fields=["order"], name="idx_ticket_order"),
        ]

    def __str__(self) -> str:
        return f"#{self.ticket_number} {self.station.name_ar} ({self.status})"

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    def elapsed_seconds(self, now=None) -> int:
        """Age of the ticket — what drives the colour on the display."""
        end = self.ready_at or (now or timezone.now())
        return int((end - self.created_at).total_seconds())

    def is_late(self, now=None) -> bool:
        target = self.station.target_prep_minutes * 60
        return self.elapsed_seconds(now) > target


class TicketLine(BaseModel):
    """
    A snapshot of what to make.

    Snapshotted like an order line, and for the same reason: the kitchen slip is
    a record of what was asked for at that moment. It must not change because
    someone later renamed the product.
    """

    ticket = models.ForeignKey(KitchenTicket, on_delete=models.CASCADE, related_name="lines")
    order_item = models.ForeignKey(
        "orders.OrderItem", on_delete=models.CASCADE, related_name="ticket_lines"
    )

    name_snapshot = models.CharField(max_length=250)
    quantity = models.DecimalField(max_digits=10, decimal_places=3)
    modifiers_snapshot = models.JSONField(default=list, blank=True)
    note = models.CharField(max_length=200, blank=True)

    ready_at = models.DateTimeField(
        null=True, blank=True, help_text="Per-item readiness, for stations that plate in stages."
    )

    class Meta:
        db_table = "kitchen_ticket_lines"
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.quantity}× {self.name_snapshot}"
