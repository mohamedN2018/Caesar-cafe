"""Roles, their permission codes, and how they are assigned to people."""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import BaseModel

from . import catalog


class Role(BaseModel):
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="roles"
    )
    code = models.CharField(max_length=50)
    name_ar = models.CharField(max_length=100)
    name_en = models.CharField(max_length=100, blank=True)
    description_ar = models.TextField(blank=True)
    is_system = models.BooleanField(
        default=False,
        help_text="System roles may be edited but never deleted.",
    )
    synced_permissions = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "The shipped spec as of the last sync. Lets ensure_system_roles tell "
            "'a new code was added to the product' apart from 'an admin removed "
            "this on purpose' — without it, every deploy silently restores "
            "permissions an operator deliberately took away."
        ),
    )

    class Meta:
        db_table = "roles"
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], name="uniq_role_code_per_org")
        ]

    def __str__(self) -> str:
        return f"{self.name_ar} ({self.code})"

    def delete(self, *args, **kwargs):
        if self.is_system:
            raise ValidationError(
                f"الدور '{self.name_ar}' دور نظام ولا يمكن حذفه. يمكن تعديل صلاحياته."
            )
        return super().delete(*args, **kwargs)

    @property
    def permission_codes(self) -> set[str]:
        return set(self.permissions.values_list("code", flat=True))

    def set_permissions(self, codes: list[str]) -> None:
        """Replace this role's permissions. Unknown codes are rejected loudly."""
        unknown = [c for c in codes if not catalog.is_valid(c)]
        if unknown:
            raise ValidationError(f"صلاحيات غير معروفة: {', '.join(unknown)}")

        wanted = set(codes)
        current = self.permission_codes

        self.permissions.filter(code__in=current - wanted).delete()
        RolePermission.objects.bulk_create(
            [RolePermission(role=self, code=code) for code in wanted - current]
        )


class RolePermission(models.Model):
    id = models.BigAutoField(primary_key=True)
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="permissions")
    code = models.CharField(max_length=100)

    class Meta:
        db_table = "role_permissions"
        constraints = [
            models.UniqueConstraint(fields=["role", "code"], name="uniq_permission_per_role")
        ]
        indexes = [models.Index(fields=["code"], name="idx_roleperm_code")]

    def __str__(self) -> str:
        return f"{self.role.code}:{self.code}"


class RoleAssignment(BaseModel):
    """
    A user holding a role, optionally scoped to one branch.

    `branch = NULL` means all branches. Nothing in the single-branch launch
    depends on that, and nothing has to be migrated later to use it — it is the
    seam that lets a future multi-branch owner hold one Super Admin assignment
    while a Branch Manager is scoped to exactly one location.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="role_assignments"
    )
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="assignments")
    branch = models.ForeignKey(
        "organizations.Branch",
        on_delete=models.CASCADE,
        related_name="role_assignments",
        null=True,
        blank=True,
        help_text="Null = every branch in the organization.",
    )

    class Meta:
        db_table = "role_assignments"
        constraints = [
            models.UniqueConstraint(fields=["user", "role", "branch"], name="uniq_role_assignment"),
            models.UniqueConstraint(
                fields=["user", "role"],
                condition=models.Q(branch__isnull=True),
                name="uniq_org_wide_role_assignment",
            ),
        ]
        indexes = [models.Index(fields=["user", "branch"], name="idx_assignment_user_branch")]

    def __str__(self) -> str:
        scope = self.branch.code if self.branch else "ALL"
        return f"{self.user.email} → {self.role.code} @ {scope}"


class RoleLimit(models.Model):
    """
    Numeric ceilings attached to a role.

    Kept beside the settings registry rather than inside it because these are
    per-role and read on the hot path of every discount and void. The registry
    holds the *defaults*; a row here overrides one for a specific role.
    """

    id = models.BigAutoField(primary_key=True)
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="limits")
    key = models.CharField(
        max_length=100, help_text="A settings-registry key, e.g. discounts.max_percent"
    )
    value = models.JSONField()

    class Meta:
        db_table = "role_limits"
        constraints = [models.UniqueConstraint(fields=["role", "key"], name="uniq_limit_per_role")]

    def __str__(self) -> str:
        return f"{self.role.code}:{self.key}={self.value}"
