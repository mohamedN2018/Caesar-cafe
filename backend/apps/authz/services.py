"""
Permission resolution and caching.

Effective permissions are the union of every role assigned to the user that
applies to the branch in scope. Resolved once per request, cached in Redis, and
invalidated on any role change — a revoked permission that lingers for five
minutes is a security hole, so invalidation is deliberately coarse.
"""

from __future__ import annotations

import logging
from uuid import UUID

from django.core.cache import cache
from django.db.models import Q

from . import catalog
from .models import Role, RoleAssignment, RoleLimit

logger = logging.getLogger(__name__)

CACHE_PREFIX = "authz:perms"
CACHE_TTL = 300


def _cache_key(user_id: UUID, branch_id: UUID | None) -> str:
    return f"{CACHE_PREFIX}:{user_id}:{branch_id or 'all'}"


def effective_permissions(user_id: UUID, branch_id: UUID | None = None) -> frozenset[str]:
    """
    Every permission code this user holds in this branch.

    An assignment with `branch = NULL` applies everywhere; one with a branch
    applies only there.

    `branch_id = None` means "no branch selected yet" — a fresh web login, before
    the user picks one. It resolves to the union of ALL their assignments, not
    just the org-wide ones. Filtering to `branch IS NULL` there would give a
    branch-scoped cashier an empty permission set at login and lock them out of
    their own system.
    """
    key = _cache_key(user_id, branch_id)
    cached = cache.get(key)
    if cached is not None:
        return cached

    assignments = RoleAssignment.objects.filter(user_id=user_id)
    if branch_id is not None:
        # Narrowed to one branch: org-wide roles plus that branch's roles, and
        # explicitly NOT another branch's.
        assignments = assignments.filter(Q(branch__isnull=True) | Q(branch_id=branch_id))

    codes = set(assignments.values_list("role__permissions__code", flat=True).distinct())
    codes.discard(None)

    permissions = frozenset(codes)
    cache.set(key, permissions, CACHE_TTL)
    return permissions


def role_limit(user_id: UUID, branch_id: UUID | None, key: str, default):
    """
    The most permissive limit across the user's roles.

    A person holding both Cashier (10% discount) and Supervisor (25%) gets 25%.
    Taking the minimum instead would make adding a role *reduce* someone's
    authority, which nobody expects.
    """
    scope = Q(branch__isnull=True)
    if branch_id is not None:
        scope |= Q(branch_id=branch_id)

    role_ids = RoleAssignment.objects.filter(Q(user_id=user_id) & scope).values_list(
        "role_id", flat=True
    )
    values = list(
        RoleLimit.objects.filter(role_id__in=role_ids, key=key).values_list("value", flat=True)
    )
    if not values:
        return default

    try:
        return max(values, key=lambda v: float(v))
    except (TypeError, ValueError):
        return values[0]


def invalidate_user(user_id: UUID) -> None:
    """Drop every cached permission set for one user, across all branches."""
    try:
        cache.delete_pattern(f"{CACHE_PREFIX}:{user_id}:*")  # type: ignore[attr-defined]
    except AttributeError:
        cache.clear()  # LocMemCache (tests) has no pattern delete


def invalidate_all() -> None:
    try:
        cache.delete_pattern(f"{CACHE_PREFIX}:*")  # type: ignore[attr-defined]
    except AttributeError:
        cache.clear()


def ensure_system_roles(organization) -> dict[str, Role]:
    """
    Create or update the eight system roles for an organization.

    Idempotent: safe to run on every deploy, which is how a newly added
    permission code reaches existing installations.
    """
    created: dict[str, Role] = {}

    for code, spec in catalog.SYSTEM_ROLES.items():
        shipped = set(spec["permissions"])

        role, was_new = Role.objects.get_or_create(
            organization=organization,
            code=code,
            defaults={
                "name_ar": spec["name_ar"],
                "is_system": True,
                "synced_permissions": sorted(shipped),
            },
        )
        if not role.is_system:
            role.is_system = True
            role.save(update_fields=["is_system"])

        if was_new:
            role.set_permissions(sorted(shipped))
            created[code] = role
            continue

        # Add ONLY codes that did not exist in the product last time we synced.
        # Comparing against `synced_permissions` rather than the role's current
        # permissions is what distinguishes "new feature shipped" from "an
        # operator deliberately removed this" — the latter must survive deploys.
        previously_shipped = set(role.synced_permissions or [])
        newly_introduced = shipped - previously_shipped

        if newly_introduced:
            role.set_permissions(sorted(role.permission_codes | newly_introduced))
            logger.info(
                "Added newly shipped permissions to system role",
                extra={"role": code, "added": sorted(newly_introduced)},
            )

        if previously_shipped != shipped:
            role.synced_permissions = sorted(shipped)
            role.save(update_fields=["synced_permissions"])

        created[code] = role

    invalidate_all()
    return created
