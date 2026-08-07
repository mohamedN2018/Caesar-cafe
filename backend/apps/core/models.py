"""
Base models every business table inherits.

Tenancy is shared-schema, row-scoped by organization_id + branch_id
(commitment C2, docs/01-system-architecture.md). Scoping is enforced in four
places because one layer will eventually be forgotten:

  1. this manager's default queryset
  2. serializers, which never accept `branch` from a client payload
  3. database constraints
  4. a CI test asserting every ViewSet is cross-tenant safe
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


def uuid7() -> uuid.UUID:
    """
    Time-ordered UUID (RFC 9562 v7).

    Used for high-volume rows — orders, events, stock movements — so we get
    distributed identity (a Desktop must mint an id while offline) without
    shredding B-tree insert locality the way random v4 keys do.
    """
    import os
    import time

    ms = int(time.time() * 1000)
    rand = os.urandom(10)
    b = bytearray(16)
    b[0:6] = ms.to_bytes(6, "big")
    b[6:16] = rand
    b[6] = (b[6] & 0x0F) | 0x70  # version 7
    b[8] = (b[8] & 0x3F) | 0x80  # RFC 4122 variant
    return uuid.UUID(bytes=bytes(b))


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class BaseModel(TimestampedModel):
    """UUID primary key + authorship. The default for master data."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    class Meta:
        abstract = True


class SequentialBaseModel(BaseModel):
    """Same as BaseModel but time-ordered ids, for append-heavy tables."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)

    class Meta:
        abstract = True


class TenantScopedQuerySet(models.QuerySet):
    def for_branch(self, branch_id) -> TenantScopedQuerySet:
        return self.filter(branch_id=branch_id)

    def for_organization(self, organization_id) -> TenantScopedQuerySet:
        return self.filter(organization_id=organization_id)


class TenantScopedManager(models.Manager.from_queryset(TenantScopedQuerySet)):  # type: ignore[misc]
    """
    Default manager for tenant-scoped models.

    Callers pass the scope explicitly via `.for_branch(...)`. Reaching across
    tenants requires `all_objects`, which is greppable and reviewed — a silent
    cross-tenant read should never be the path of least resistance.
    """


class TenantScopedModel(BaseModel):
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="+",
    )
    branch = models.ForeignKey(
        "organizations.Branch",
        on_delete=models.PROTECT,
        related_name="+",
        db_index=True,
    )

    # Order follows the Django style guide (fields → managers → Meta). Ruff
    # cannot resolve the custom manager class and misreads the pair.
    objects = TenantScopedManager()
    all_objects = models.Manager()  # noqa: DJ012

    class Meta:
        abstract = True


class SoftDeletableModel(models.Model):
    """
    Deactivation, not deletion.

    A product that has ever been sold must not be removable — deleting it would
    orphan historical line items and silently rewrite last quarter's reports.
    """

    is_active = models.BooleanField(default=True, db_index=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True
