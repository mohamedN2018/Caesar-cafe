"""
Sparse setting-value store.

There is no per-branch settings table with one column per option: ~180 settings
would make it an unmanageable wide table and every new setting a migration.
Definitions are a typed registry in code (registry.py, definitions.py); values
are sparse rows here. A scope that has overridden nothing stores nothing.

Organization and Branch live in `apps.organizations` — they are domain entities,
not configuration.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from .registry import Scope


class SettingValue(models.Model):
    """One override of a registered setting at one scope."""

    SCOPE_CHOICES = [(s.value, s.value) for s in Scope]

    id = models.BigAutoField(primary_key=True)
    scope_type = models.CharField(max_length=20, choices=SCOPE_CHOICES)
    scope_id = models.UUIDField()
    key = models.CharField(max_length=100)
    value = models.JSONField()

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "setting_values"
        constraints = [
            models.UniqueConstraint(
                fields=["scope_type", "scope_id", "key"], name="uniq_setting_per_scope"
            )
        ]
        indexes = [
            models.Index(fields=["scope_type", "scope_id"], name="idx_setting_scope"),
            models.Index(fields=["key"], name="idx_setting_key"),
        ]

    def __str__(self) -> str:
        return f"{self.key} @ {self.scope_type}:{self.scope_id}"


class SettingChangeLog(models.Model):
    """
    Immutable history of setting changes.

    Answers "who changed the service charge and when" — the first question asked
    when a total looks wrong. Append-only: no update or delete path exists.
    """

    id = models.BigAutoField(primary_key=True)
    scope_type = models.CharField(max_length=20)
    scope_id = models.UUIDField()
    key = models.CharField(max_length=100)
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "setting_change_log"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["key", "-created_at"], name="idx_settinglog_key")]

    def __str__(self) -> str:
        return f"{self.key}: {self.old_value!r} → {self.new_value!r}"
