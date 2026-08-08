"""
Authentication services: lockout, PIN verification, MFA.

The lockout is what makes a 4-digit PIN defensible (accepted risk AR4). Without
it, 10 000 combinations is minutes of work. With 5 attempts per 15 minutes per
device, it is years — and every failure is recorded.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.utils import timezone

from apps.configuration import resolver
from apps.configuration.resolver import ScopeContext
from apps.core.exceptions import AppError

from .models import LoginAttempt, RecoveryCode, User

logger = logging.getLogger(__name__)

LOCKOUT_PREFIX = "auth:lockout"
ATTEMPT_PREFIX = "auth:attempts"


class AuthenticationFailed(AppError):
    status_code = 401
    code = "AUTHENTICATION_FAILED"
    default_detail = "بيانات الدخول غير صحيحة"


class AccountLocked(AppError):
    status_code = 429
    code = "ACCOUNT_LOCKED"
    default_detail = "تم إيقاف المحاولات مؤقتاً بسبب محاولات خاطئة متكررة."


class MFARequired(AppError):
    status_code = 401
    code = "MFA_REQUIRED"
    default_detail = "مطلوب رمز التحقق بخطوتين"


@dataclass(frozen=True)
class LockoutPolicy:
    max_attempts: int
    lockout_seconds: int

    @classmethod
    def load(cls, organization_id: UUID | None) -> LockoutPolicy:
        context = ScopeContext(organization_id=organization_id)
        return cls(
            max_attempts=resolver.get("security.pin_lockout_attempts", context),
            lockout_seconds=resolver.get("security.pin_lockout_minutes", context) * 60,
        )


def _keys(scope: str) -> tuple[str, str]:
    return f"{ATTEMPT_PREFIX}:{scope}", f"{LOCKOUT_PREFIX}:{scope}"


def assert_not_locked(scope: str) -> None:
    _, lock_key = _keys(scope)
    if cache.get(lock_key):
        raise AccountLocked()


def record_failure(scope: str, policy: LockoutPolicy) -> int:
    """Returns attempts used. Locks the scope when the policy is exhausted."""
    attempt_key, lock_key = _keys(scope)

    # `add` then `incr` avoids the race where two failures both initialise to 1.
    cache.add(attempt_key, 0, policy.lockout_seconds)
    try:
        attempts = cache.incr(attempt_key)
    except ValueError:
        cache.set(attempt_key, 1, policy.lockout_seconds)
        attempts = 1

    if attempts >= policy.max_attempts:
        cache.set(lock_key, True, policy.lockout_seconds)
        logger.warning(
            "Authentication scope locked out",
            extra={"scope": scope, "attempts": attempts},
        )
        _audit_auth(
            "auth.lockout", scope, {"attempts": attempts, "seconds": policy.lockout_seconds}
        )
    elif attempts > 3:
        # Below the threshold, repeated failures are still worth finding — a run
        # of three across several accounts is the shape of credential stuffing,
        # and each one on its own looks like somebody who forgot their password.
        _audit_auth("auth.login_failed", scope, {"attempts": attempts})

    return attempts


def _audit_auth(action: str, scope: str, detail: dict) -> None:
    """
    Audit an auth event.

    The tenant has to be resolved from the scope, because a failed login happens
    BEFORE authentication — there is no principal for the middleware to have
    filled in. A known address resolves to its organization, so the branch
    manager whose staff account is being stuffed can see it. An unknown address
    resolves to nothing and the row stays org-less, visible to a superuser: that
    attempt is a platform-level signal, not one tenant's business.
    """
    from apps.audit import services as audit

    organization = None
    if scope.startswith("email:"):
        user = User.objects.filter(email__iexact=scope.removeprefix("email:")).first()
        organization = user.organization if user else None

    audit.record(
        action,
        organization=organization,
        object_type="auth_scope",
        object_id=scope[:64],
        detail=detail,
    )


def clear_failures(scope: str) -> None:
    attempt_key, lock_key = _keys(scope)
    cache.delete_many([attempt_key, lock_key])


def log_attempt(
    *,
    identifier: str,
    kind: str,
    succeeded: bool,
    ip_address: str | None = None,
    user_agent: str = "",
    device_id: UUID | None = None,
) -> None:
    LoginAttempt.objects.create(
        identifier=identifier[:255],
        kind=kind,
        succeeded=succeeded,
        ip_address=ip_address,
        user_agent=user_agent[:300],
        device_id=device_id,
    )


def authenticate_password(
    *, email: str, password: str, ip_address: str | None = None, user_agent: str = ""
) -> User:
    scope = f"password:{email.lower()}"
    assert_not_locked(scope)

    user = User.objects.filter(email__iexact=email).first()

    # Hash even when the user does not exist, so response time does not reveal
    # which emails are registered (threat I7's cousin).
    if user is None:
        make_password(password)
        log_attempt(
            identifier=email,
            kind="PASSWORD",
            succeeded=False,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        record_failure(scope, LockoutPolicy.load(None))
        raise AuthenticationFailed()

    policy = LockoutPolicy.load(user.organization_id)

    if not user.check_password(password) or not user.is_active:
        log_attempt(
            identifier=email,
            kind="PASSWORD",
            succeeded=False,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        record_failure(scope, policy)
        raise AuthenticationFailed()

    clear_failures(scope)
    log_attempt(
        identifier=email,
        kind="PASSWORD",
        succeeded=True,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return user


def verify_pin(
    *,
    user: User,
    pin: str,
    device_id: UUID,
    ip_address: str | None = None,
) -> bool:
    """
    Verify a POS PIN. Rate-limited per (user, device).

    Callers must already have established that the request comes from an
    activated device — a PIN is never accepted from the open internet.
    """
    scope = f"pin:{user.id}:{device_id}"
    assert_not_locked(scope)

    policy = LockoutPolicy.load(user.organization_id)
    ok = user.is_active and user.check_pin(pin)

    log_attempt(
        identifier=str(user.id),
        kind="PIN",
        succeeded=ok,
        ip_address=ip_address,
        device_id=device_id,
    )

    if ok:
        clear_failures(scope)
    else:
        record_failure(scope, policy)
    return ok


# ── MFA ──────────────────────────────────────────────────────────────────────


def mfa_is_required(user: User) -> bool:
    """True when any of the user's roles appears in security.require_mfa_for_roles."""
    from apps.authz.models import RoleAssignment

    context = ScopeContext(organization_id=user.organization_id)
    required_roles = set(resolver.get("security.require_mfa_for_roles", context) or [])
    if not required_roles:
        return False

    held = set(RoleAssignment.objects.filter(user=user).values_list("role__code", flat=True))
    return bool(held & required_roles)


def issue_recovery_codes(user: User, codes: list[str]) -> None:
    RecoveryCode.objects.filter(user=user, used_at__isnull=True).delete()
    RecoveryCode.objects.bulk_create(
        [RecoveryCode(user=user, code_hash=make_password(code)) for code in codes]
    )


def consume_recovery_code(user: User, code: str) -> bool:
    normalized = code.strip().lower()
    for record in user.recovery_codes.filter(used_at__isnull=True):
        if check_password(normalized, record.code_hash):
            record.used_at = timezone.now()
            record.save(update_fields=["used_at"])
            logger.warning("MFA recovery code used", extra={"user_id": str(user.id)})
            return True
    return False
