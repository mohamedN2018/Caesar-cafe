"""
Kitchen stations.

Only the Station model lands in Phase 4, because `catalog.Product` routes to it.
Tickets, the display and the real-time layer arrive in Phase 6.
"""

from __future__ import annotations

from django.db import models

from apps.core.models import SoftDeletableModel, TenantScopedModel


class Station(TenantScopedModel, SoftDeletableModel):
    code = models.CharField(max_length=32, help_text="COFFEE, HOT, COLD, DESSERT, BAR")
    name_ar = models.CharField(max_length=100)
    target_prep_minutes = models.PositiveSmallIntegerField(default=8)
    auto_accept = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "kitchen_stations"
        ordering = ["sort_order", "name_ar"]
        constraints = [
            models.UniqueConstraint(fields=["branch", "code"], name="uniq_station_code_per_branch")
        ]

    def __str__(self) -> str:
        return self.name_ar
