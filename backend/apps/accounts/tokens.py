"""
JWT issuance with refresh-token families and reuse detection.

Rotation alone is not enough. If a refresh token is stolen, both the thief and
the legitimate user hold a valid token; whoever rotates first wins and the other
is simply logged out — a silent compromise.

So each login opens a *family*. Rotation advances the family's `current_jti`.
Presenting a token whose jti is not current means two parties hold tokens from
one login: the family is revoked immediately and an alert is raised. That turns
a silent credential theft into a loud, visible event (threat S6).
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

import jwt
from django.conf import settings
from django.utils import timezone

from apps.core.exceptions import AppError

from .models import TokenFamily

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"


class TokenError(AppError):
    status_code = 401
    code = "TOKEN_INVALID"
    default_detail = "الجلسة غير صالحة. برجاء تسجيل الدخول مرة أخرى."


class TokenReuseDetected(AppError):
    status_code = 401
    code = "TOKEN_REUSE_DETECTED"
    default_detail = "تم اكتشاف استخدام غير صالح للجلسة. تم إنهاء كل الجلسات لهذا الحساب."


def _signing_key() -> str:
    return settings.SIMPLE_JWT["SIGNING_KEY"]


def _encode(payload: dict[str, Any]) -> str:
    return jwt.encode(payload, _signing_key(), algorithm=ALGORITHM)


def decode(token: str, *, expected_type: str | None = None) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, _signing_key(), algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("انتهت صلاحية الجلسة", code="TOKEN_EXPIRED") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError() from exc

    if expected_type and payload.get("typ") != expected_type:
        raise TokenError()
    return payload


def _lifetimes() -> tuple[timedelta, timedelta]:
    return (
        settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"],
        settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"],
    )


def issue_pair(
    *,
    user,
    kind: str,
    organization_id: uuid.UUID | None = None,
    branch_id: uuid.UUID | None = None,
    device_id: uuid.UUID | None = None,
    ip_address: str | None = None,
    user_agent: str = "",
) -> dict[str, Any]:
    """Open a new token family and issue the first access/refresh pair."""
    access_ttl, refresh_ttl = _lifetimes()
    now = timezone.now()
    jti = uuid.uuid4()

    family = TokenFamily.objects.create(
        user=user,
        current_jti=jti,
        device_id=device_id,
        kind=kind,
        ip_address=ip_address,
        user_agent=user_agent[:300],
        expires_at=now + refresh_ttl,
    )

    return {
        "access": _access_token(
            user=user,
            kind=kind,
            organization_id=organization_id,
            branch_id=branch_id,
            device_id=device_id,
            family_id=family.id,
            ttl=access_ttl,
        ),
        "refresh": _refresh_token(family_id=family.id, jti=jti, ttl=refresh_ttl),
        "access_expires_in": int(access_ttl.total_seconds()),
        "refresh_expires_in": int(refresh_ttl.total_seconds()),
        "family_id": str(family.id),
    }


def _access_token(*, user, kind, organization_id, branch_id, device_id, family_id, ttl) -> str:
    now = timezone.now()
    payload: dict[str, Any] = {
        "typ": "access",
        "kind": str(kind),
        "org": str(organization_id) if organization_id else None,
        "branch": str(branch_id) if branch_id else None,
        "device": str(device_id) if device_id else None,
        "fam": str(family_id),
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }

    # `sub` is OMITTED — not set to null — for DEVICE tokens. A terminal has no
    # human behind it, and pretending otherwise would put someone's name on
    # actions they never took. RFC 7519 says `sub` is a string when present, and
    # PyJWT enforces that: a null value makes the token undecodable.
    if user is not None:
        payload["sub"] = str(user.id)

    return _encode(payload)


def _refresh_token(*, family_id: uuid.UUID, jti: uuid.UUID, ttl: timedelta) -> str:
    now = timezone.now()
    return _encode(
        {
            "typ": "refresh",
            "fam": str(family_id),
            "jti": str(jti),
            "iat": int(now.timestamp()),
            "exp": int((now + ttl).timestamp()),
        }
    )


def issue_enrollment_token(*, user, organization_id=None, minutes: int = 10) -> dict[str, Any]:
    """
    A short-lived, permission-less token that opens ONLY the MFA enrolment
    endpoints.

    Resolves the deadlock created by policy-mandated MFA: the password has been
    verified, so the account is not anonymous, but it owes a security step
    before it gets a real session. No refresh token is issued — if enrolment is
    abandoned, the user simply logs in again.
    """
    ttl = timedelta(minutes=minutes)
    return {
        "enrollment_token": _access_token(
            user=user,
            kind="ENROLLMENT",
            organization_id=organization_id,
            branch_id=None,
            device_id=None,
            family_id=uuid.uuid4(),  # not persisted: nothing to rotate
            ttl=ttl,
        ),
        "expires_in": int(ttl.total_seconds()),
    }


def rotate(refresh_token: str, *, ip_address: str | None = None) -> dict[str, Any]:
    """
    Exchange a refresh token for a new pair.

    Raises TokenReuseDetected — and kills every session for the user — if the
    presented token is not the family's current one.
    """
    payload = decode(refresh_token, expected_type="refresh")

    try:
        family = TokenFamily.objects.select_related("user").get(id=payload["fam"])
    except (TokenFamily.DoesNotExist, ValueError, KeyError) as exc:
        raise TokenError() from exc

    if family.revoked_at is not None:
        raise TokenError("انتهت الجلسة", code="SESSION_REVOKED")

    if str(family.current_jti) != payload.get("jti"):
        # Two parties hold tokens from one login. Assume the worst.
        revoked = TokenFamily.revoke_all_for_user(family.user_id, reason="REUSE_DETECTED")
        logger.error(
            "Refresh token reuse detected — revoked all sessions",
            extra={
                "user_id": str(family.user_id),
                "family_id": str(family.id),
                "families_revoked": revoked,
                "ip": ip_address,
            },
        )
        raise TokenReuseDetected()

    if family.user is not None and not family.user.is_active:
        raise TokenError("الحساب غير مفعّل", code="ACCOUNT_INACTIVE")

    access_ttl, refresh_ttl = _lifetimes()
    new_jti = uuid.uuid4()
    family.current_jti = new_jti
    family.rotation_count += 1
    family.last_used_at = timezone.now()
    family.save(update_fields=["current_jti", "rotation_count", "last_used_at"])

    return {
        "access": _access_token(
            user=family.user,
            kind=family.kind,
            organization_id=family.user.organization_id if family.user else None,
            branch_id=None,
            device_id=family.device_id,
            family_id=family.id,
            ttl=access_ttl,
        ),
        "refresh": _refresh_token(family_id=family.id, jti=new_jti, ttl=refresh_ttl),
        "access_expires_in": int(access_ttl.total_seconds()),
        "refresh_expires_in": int(refresh_ttl.total_seconds()),
    }


def revoke(refresh_token: str) -> None:
    """Logout. Silent on an already-invalid token — nothing useful to report."""
    try:
        payload = decode(refresh_token, expected_type="refresh")
    except TokenError:
        return
    TokenFamily.objects.filter(id=payload.get("fam"), revoked_at__isnull=True).update(
        revoked_at=timezone.now(), revoked_reason="LOGOUT"
    )
