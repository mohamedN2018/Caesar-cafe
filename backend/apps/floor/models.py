"""Areas, tables, and the sessions that occupy them."""

from __future__ import annotations

from django.db import models

from apps.core.models import BaseModel, SoftDeletableModel, TenantScopedModel


class Area(TenantScopedModel, SoftDeletableModel):
    name_ar = models.CharField(max_length=100, help_text="الصالة / التراس / صالة الأطفال")
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "floor_areas"
        ordering = ["sort_order", "name_ar"]

    def __str__(self) -> str:
        return self.name_ar


class TableStatus(models.TextChoices):
    AVAILABLE = "AVAILABLE", "AVAILABLE"
    OCCUPIED = "OCCUPIED", "OCCUPIED"
    RESERVED = "RESERVED", "RESERVED"
    CLEANING = "CLEANING", "CLEANING"


class Table(BaseModel, SoftDeletableModel):
    area = models.ForeignKey(Area, on_delete=models.PROTECT, related_name="tables")
    number = models.CharField(max_length=16, help_text="T-05")
    seats = models.PositiveSmallIntegerField(default=4)
    status = models.CharField(
        max_length=16, choices=TableStatus.choices, default=TableStatus.AVAILABLE
    )

    # The Web Admin's drag-and-drop canvas writes these, and the Desktop floor
    # map mirrors them. Finding "table 7" by the shape of the room is
    # meaningfully faster than reading a list.
    pos_x = models.IntegerField(default=0)
    pos_y = models.IntegerField(default=0)

    class Meta:
        db_table = "floor_tables"
        ordering = ["number"]
        constraints = [
            models.UniqueConstraint(fields=["area", "number"], name="uniq_table_number_per_area")
        ]

    def __str__(self) -> str:
        return self.number

    @property
    def open_session(self):
        return self.sessions.filter(closed_at__isnull=True).first()


class TableSession(BaseModel):
    """
    One party's visit to a table.

    Separate from the order because a party can generate several orders (a
    second round), and because transferring a table moves the SESSION, carrying
    every order with it.
    """

    table = models.ForeignKey(Table, on_delete=models.PROTECT, related_name="sessions")
    guest_count = models.PositiveSmallIntegerField(default=1)
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    opened_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    waiter = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="waited_sessions",
        help_text="Drives floor.waiter_sees_only_own_tables.",
    )

    class Meta:
        db_table = "floor_table_sessions"
        ordering = ["-opened_at"]
        indexes = [
            models.Index(fields=["table", "closed_at"], name="idx_session_table_open"),
        ]

    def __str__(self) -> str:
        return f"{self.table.number} @ {self.opened_at:%H:%M}"

    @property
    def is_open(self) -> bool:
        return self.closed_at is None
