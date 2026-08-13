"""
Writing audit records.

`record()` is the only write path. It is deliberately forgiving about what it
does not know and strict about what it must not get wrong:

  * An unknown action code raises. A typo in an action name is a hole in the
    trail, and the hole is invisible until somebody searches for the thing that
    is missing.
  * A missing actor, IP or device is fine. A Celery task and a management command
    have none of those, and refusing to record because the context is thin would
    mean the automated actions — the ones nobody watches — are the ones with no
    trail.
  * **Recording never breaks the action it describes.** A failure here is logged
    loudly and swallowed. Losing the audit row for a completed sale is bad;
    failing the sale because the audit row could not be written is worse, and
    would hand any attacker a denial-of-service on the whole POS.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from django.db import models

from . import context as audit_context
from .actions import Severity, is_valid, severity_of
from .models import AuditLog

logger = logging.getLogger(__name__)

#: Never stored, whatever a caller passes. The redaction filter protects the
#: LOGS; this protects the DATABASE, which is the copy that gets backed up,
#: shipped off-site, and read by whoever restores it.
REDACTED_FIELDS = frozenset(
    {
        "password",
        "password1",
        "password2",
        "pin",
        "pin_hash",
        "secret",
        "secret_hash",
        "token",
        "access",
        "refresh",
        "authorization",
        "key_hash",
        "device_secret",
        "totp_secret",
        "recovery_codes",
    }
)
MASK = "«محجوب»"


def _scalar(value: Any) -> Any:
    """Make a field value JSON-safe without losing precision."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, models.Model):
        return str(value.pk)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _clean(data: dict | None) -> dict:
    if not data:
        return {}
    return {
        key: (MASK if key.lower() in REDACTED_FIELDS else _scalar(value))
        for key, value in data.items()
    }


def snapshot(instance, fields: list[str] | None = None) -> dict:
    """
    Capture a model row as a plain dict.

    Call this BEFORE mutating, keep the result, and pass it as `before`. Fields
    default to every concrete column, because the useful diff is usually the one
    nobody predicted would change.
    """
    if instance is None:
        return {}

    names = fields or [
        f.name for f in instance._meta.concrete_fields if f.name not in {"created_by", "updated_by"}
    ]
    return _clean(
        {name: getattr(instance, f"{name}_id", getattr(instance, name, None)) for name in names}
    )


def diff(before: dict, after: dict) -> dict:
    """
    `{field: [old, new]}` for the fields that actually moved.

    Unchanged fields are dropped so a reviewer reads three lines instead of
    forty — the whole point of an audit trail is that somebody can be bothered
    to look at it.
    """
    changes: dict[str, list] = {}
    for key in set(before) | set(after):
        old, new = before.get(key), after.get(key)
        if old != new:
            changes[key] = [old, new]
    return changes


def record(
    action: str,
    *,
    organization=None,
    branch=None,
    actor=None,
    approved_by=None,
    obj=None,
    object_type: str = "",
    object_id: str = "",
    object_label: str = "",
    before: dict | None = None,
    after: dict | None = None,
    detail: dict | None = None,
    severity: str | None = None,
) -> AuditLog | None:
    """
    Write one audit row. Returns None if it could not be written.

    Anything not passed is filled from the request context, so a service that
    knows only "this order was voided for this reason" still produces a complete
    record.
    """
    if not is_valid(action):
        raise ValueError(
            f"Unknown audit action '{action}'. Register it in apps/audit/actions.py — "
            "an unlisted action is an action nobody will find later."
        )

    ctx = audit_context.current()

    try:
        # May legitimately be None — a failed login against an address that does
        # not exist belongs to no tenant. See AuditLog.organization.
        organization_id = _id(organization) or _org_of(branch) or ctx.organization_id

        if obj is not None:
            object_type = object_type or obj._meta.model_name
            object_id = object_id or str(obj.pk)
            object_label = object_label or str(obj)[:250]

        before_clean = _clean(before)
        after_clean = _clean(after)

        return AuditLog.objects.create(
            action=action,
            domain=action.split(".")[0],
            severity=severity or severity_of(action),
            organization_id=organization_id,
            branch_id=_id(branch) or ctx.branch_id,
            actor=actor,
            actor_name=(
                getattr(actor, "full_name_ar", "") or getattr(actor, "email", "") or ctx.actor_name
            )[:150],
            approved_by=approved_by,
            approved_by_name=(getattr(approved_by, "full_name_ar", "") or "")[:150],
            device_id=ctx.device_id,
            object_type=object_type[:48],
            object_id=str(object_id)[:64],
            object_label=object_label[:250],
            before=before_clean,
            after=after_clean,
            changes=diff(before_clean, after_clean),
            detail=_clean(detail),
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent[:250],
            request_id=ctx.request_id[:64],
        )
    except Exception:
        # Never break the action being audited. See the module docstring.
        logger.exception("Failed to write audit record", extra={"action": action})
        return None


def record_change(action: str, instance, before: dict, **kwargs) -> AuditLog | None:
    """Convenience for the common shape: snapshot, mutate, record the diff."""
    return record(action, obj=instance, before=before, after=snapshot(instance), **kwargs)


def _id(value):
    if value is None:
        return None
    return str(getattr(value, "pk", value))


def _org_of(branch):
    return (
        str(branch.organization_id)
        if branch is not None and hasattr(branch, "organization_id")
        else None
    )


__all__ = [
    "MASK",
    "REDACTED_FIELDS",
    "Severity",
    "diff",
    "record",
    "record_change",
    "snapshot",
]
