"""
Setting resolution: Device → Role → Branch → Organization → registry default.

Most specific wins. This is the only supported way to read a business value —
`settings.get(...)` in a service, never a literal in the code (commitment C10).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.core.cache import cache
from django.db import transaction

from .models import SettingChangeLog, SettingValue
from .registry import SCOPE_PRECEDENCE, Scope, SettingDefinition, registry

logger = logging.getLogger(__name__)

CACHE_PREFIX = "settings"
CACHE_TTL = 300


@dataclass(frozen=True)
class ScopeContext:
    """Where a read is happening. Any level may be omitted."""

    organization_id: UUID | None = None
    branch_id: UUID | None = None
    device_id: UUID | None = None
    role_id: UUID | None = None

    def id_for(self, scope: Scope) -> UUID | None:
        return {
            Scope.ORGANIZATION: self.organization_id,
            Scope.BRANCH: self.branch_id,
            Scope.DEVICE: self.device_id,
            Scope.ROLE: self.role_id,
        }[scope]

    def cache_key(self) -> str:
        parts = [
            str(self.organization_id or "-"),
            str(self.branch_id or "-"),
            str(self.device_id or "-"),
            str(self.role_id or "-"),
        ]
        return f"{CACHE_PREFIX}:{':'.join(parts)}"


@dataclass(frozen=True)
class ResolvedSetting:
    key: str
    value: Any
    origin: str
    """Which scope supplied it: ORGANIZATION / BRANCH / DEVICE / ROLE / DEFAULT."""
    is_default: bool


def _overrides_for(context: ScopeContext) -> dict[tuple[str, str], Any]:
    """Load every override that could apply to this context, in one query."""
    cache_key = f"{context.cache_key()}:raw"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    conditions = [
        (scope.value, scope_id)
        for scope in SCOPE_PRECEDENCE
        if (scope_id := context.id_for(scope)) is not None
    ]
    if not conditions:
        return {}

    from django.db.models import Q

    query = Q()
    for scope_type, scope_id in conditions:
        query |= Q(scope_type=scope_type, scope_id=scope_id)

    overrides = {
        (row.scope_type, row.key): row.value
        for row in SettingValue.objects.filter(query).only("scope_type", "key", "value")
    }
    cache.set(cache_key, overrides, CACHE_TTL)
    return overrides


def resolve(key: str, context: ScopeContext) -> ResolvedSetting:
    definition = registry.get(key)
    overrides = _overrides_for(context)

    for scope in SCOPE_PRECEDENCE:
        if context.id_for(scope) is None:
            continue
        raw = overrides.get((scope.value, key))
        if raw is None:
            continue
        try:
            return ResolvedSetting(
                key=key,
                value=definition.clean(raw),
                origin=scope.value,
                is_default=False,
            )
        except Exception:
            # A stored value that no longer validates (the definition tightened
            # after it was written) must not break the request. Fall through to
            # the default and make the problem visible.
            logger.warning(
                "Invalid stored setting; falling back to default",
                extra={"setting_key": key, "scope": scope.value},
            )
            break

    return ResolvedSetting(key=key, value=definition.default, origin="DEFAULT", is_default=True)


def get(key: str, context: ScopeContext) -> Any:
    """The everyday accessor. Returns the typed value."""
    return resolve(key, context).value


def get_many(keys: list[str], context: ScopeContext) -> dict[str, Any]:
    return {key: get(key, context) for key in keys}


def resolve_all(context: ScopeContext) -> dict[str, ResolvedSetting]:
    return {key: resolve(key, context) for key in registry.all()}


def desktop_payload(context: ScopeContext) -> dict[str, Any]:
    """
    The subset pushed to Desktop clients in /branches/{id}/config/.

    Serialized so JSON can carry Decimals and times without precision loss.
    """
    payload = {}
    for key in registry.desktop_keys():
        definition = registry.get(key)
        payload[key] = definition.serialize(get(key, context))
    return payload


@transaction.atomic
def set_value(
    key: str,
    raw_value: Any,
    *,
    scope: Scope,
    scope_id: UUID,
    user=None,
    ip_address: str | None = None,
) -> ResolvedSetting:
    """
    Write an override. Validates, audits, and invalidates the cache.

    Raises ValidationFailed if the value does not satisfy the registry's
    validators — a percentage cannot exceed 100 no matter who types it.
    """
    definition: SettingDefinition = registry.get(key)

    if scope not in definition.allowed_scopes:
        allowed = ", ".join(s.value for s in definition.allowed_scopes)
        raise ValueError(f"Setting '{key}' may only be written at: {allowed} — not {scope.value}")

    cleaned = definition.clean(raw_value)
    stored = definition.serialize(cleaned)

    existing = SettingValue.objects.filter(
        scope_type=scope.value, scope_id=scope_id, key=key
    ).first()
    old_value = existing.value if existing else None

    SettingValue.objects.update_or_create(
        scope_type=scope.value,
        scope_id=scope_id,
        key=key,
        defaults={"value": stored, "updated_by": user},
    )

    SettingChangeLog.objects.create(
        scope_type=scope.value,
        scope_id=scope_id,
        key=key,
        old_value=old_value,
        new_value=stored,
        changed_by=user,
        ip_address=ip_address,
    )

    _invalidate()
    return ResolvedSetting(key=key, value=cleaned, origin=scope.value, is_default=False)


@transaction.atomic
def reset(key: str, *, scope: Scope, scope_id: UUID, user=None, ip_address: str | None = None):
    """Remove an override so the value falls back to the next scope or default."""
    registry.get(key)  # raises on unknown key

    existing = SettingValue.objects.filter(
        scope_type=scope.value, scope_id=scope_id, key=key
    ).first()
    if existing is None:
        return

    SettingChangeLog.objects.create(
        scope_type=scope.value,
        scope_id=scope_id,
        key=key,
        old_value=existing.value,
        new_value=None,
        changed_by=user,
        ip_address=ip_address,
    )
    existing.delete()
    _invalidate()


def _invalidate() -> None:
    """
    Drop cached overrides.

    Coarse on purpose: settings change rarely and correctness after a change
    matters far more than avoiding a few cache misses. A per-scope invalidation
    that misses one entry means a terminal keeps charging the old VAT rate.
    """
    try:
        cache.delete_pattern(f"{CACHE_PREFIX}:*")  # type: ignore[attr-defined]
    except AttributeError:
        # LocMemCache (tests) has no delete_pattern.
        cache.clear()
