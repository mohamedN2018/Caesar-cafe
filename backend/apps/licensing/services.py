"""
Licence issuance, activation, and the graduated expiry policy.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Any

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.configuration import resolver
from apps.configuration.resolver import ScopeContext
from apps.core.exceptions import AppError

from . import keys, offline_token
from .models import (
    Device,
    DeviceStatus,
    InvoiceBlock,
    License,
    LicenseEvent,
    LicenseStatus,
)

logger = logging.getLogger(__name__)

INVOICE_BLOCK_SIZE = 500


# ── errors ───────────────────────────────────────────────────────────────────
# Every message names both the problem and the remedy. A cashier at 7am needs to
# know whether to call the manager or check the wifi — "activation failed" sends
# them to the phone.


class ActivationError(AppError):
    status_code = 403
    code = "ACTIVATION_FAILED"


class LicenseNotFound(ActivationError):
    status_code = 404
    code = "LICENSE_NOT_FOUND"
    default_detail = "مفتاح الترخيص غير صحيح. تأكد من كتابته بشكل صحيح."


class LicenseEmailMismatch(ActivationError):
    code = "LICENSE_EMAIL_MISMATCH"
    default_detail = "البريد الإلكتروني لا يطابق الترخيص."


class LicenseSuspended(ActivationError):
    code = "LICENSE_SUSPENDED"
    default_detail = "الترخيص موقوف مؤقتاً. تواصل مع مدير النظام."


class LicenseRevoked(ActivationError):
    code = "LICENSE_REVOKED"
    default_detail = "تم إلغاء الترخيص."


class LicenseExpired(ActivationError):
    code = "LICENSE_EXPIRED"
    default_detail = "انتهت صلاحية الترخيص. تواصل مع مدير النظام."


class DeviceLimitReached(ActivationError):
    status_code = 409
    code = "DEVICE_LIMIT_REACHED"
    default_detail = "عدد الأجهزة المسموح بها مكتمل."


class DeviceRevoked(ActivationError):
    code = "DEVICE_REVOKED"
    default_detail = "تم إلغاء تفعيل هذا الجهاز."


class ClientTooOld(ActivationError):
    code = "CLIENT_TOO_OLD"
    default_detail = "إصدار البرنامج قديم. برجاء التحديث."


# ── key hashing ──────────────────────────────────────────────────────────────


def hash_key(canonical_key: str) -> str:
    """
    HMAC-SHA256 with the server pepper.

    LICENSE_PEPPER must NEVER be rotated: every issued key becomes unverifiable
    and there is no recovery path, because plaintext keys are not stored. Back it
    up separately from the database — a backup containing both the pepper and
    the hashes has undone the point of having a pepper.
    """
    pepper = settings.LICENSE_PEPPER
    if not pepper:
        raise RuntimeError("LICENSE_PEPPER is not configured; refusing to hash a licence key.")
    return hmac.new(pepper.encode(), canonical_key.encode(), hashlib.sha256).hexdigest()


def _equalize_timing() -> None:
    """
    Burn the work a successful activation would have spent on Argon2.

    Without this, a valid key takes materially longer than an invalid one, and
    the difference turns 80 bits of entropy into a much shorter search.
    """
    make_password(secrets.token_urlsafe(16))


# ── issuance ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class IssuedLicense:
    license: License
    plaintext_key: str
    """Shown exactly once, at creation. Never recoverable afterwards."""


@transaction.atomic
def issue_license(
    *,
    organization,
    customer_email: str,
    license_type: str,
    max_devices: int = 3,
    expires_at=None,
    branch=None,
    customer_name: str = "",
    notes: str = "",
    actor=None,
    ip_address: str | None = None,
) -> IssuedLicense:
    plaintext = keys.generate()

    license_obj = License.objects.create(
        organization=organization,
        branch=branch,
        key_hash=hash_key(plaintext),
        # Readable only in demo mode; empty everywhere else. See the field.
        key_plaintext=plaintext if getattr(settings, "DEMO_MODE", False) else "",
        key_prefix=keys.prefix_of(plaintext),
        key_last4=keys.last4_of(plaintext),
        customer_email=customer_email.strip().lower(),
        customer_name=customer_name,
        license_type=license_type,
        max_devices=max_devices,
        expires_at=expires_at,
        notes=notes,
        status=LicenseStatus.PENDING,
        created_by=actor,
    )
    _record(
        license_obj,
        LicenseEvent.Event.CREATED,
        actor=actor,
        ip_address=ip_address,
        detail={"license_type": license_type, "max_devices": max_devices},
    )
    return IssuedLicense(license=license_obj, plaintext_key=plaintext)


@transaction.atomic
def regenerate_key(license_obj: License, *, actor=None, ip_address=None) -> IssuedLicense:
    """
    Replace the key. Irreversible — the old one dies immediately.

    Existing devices keep working on their device secrets until they need to
    re-activate, so this does not take a cafe offline mid-service.
    """
    plaintext = keys.generate()
    license_obj.key_hash = hash_key(plaintext)
    license_obj.key_prefix = keys.prefix_of(plaintext)
    license_obj.key_last4 = keys.last4_of(plaintext)
    # Kept in step with issuance: readable in demo mode, and CLEARED otherwise —
    # so turning DEMO_MODE off and regenerating leaves nothing readable behind.
    license_obj.key_plaintext = plaintext if getattr(settings, "DEMO_MODE", False) else ""
    license_obj.save(
        update_fields=["key_hash", "key_prefix", "key_last4", "key_plaintext", "updated_at"]
    )

    _record(license_obj, LicenseEvent.Event.KEY_REGENERATED, actor=actor, ip_address=ip_address)
    return IssuedLicense(license=license_obj, plaintext_key=plaintext)


# ── activation ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Activation:
    device: Device
    device_secret: str
    """Returned exactly once. Stored in the Windows Credential Manager."""
    offline_token: str


def activate(
    *,
    license_key: str,
    email: str,
    device_name: str,
    branch=None,
    mode: str = "POS",
    platform: str = "",
    app_version: str = "",
    fingerprint: str = "",
    ip_address: str | None = None,
) -> Activation:
    """
    Bind a new device to a licence.

    Two things worth knowing about the implementation:

    1. The seat check runs under `SELECT ... FOR UPDATE`. Without the row lock,
       three simultaneous activations against a 3-seat licence each read
       activation_count = 2, each conclude there is room, and each insert —
       leaving 5 devices on a 3-seat plan.

    2. Failure events are recorded AFTER the transaction unwinds. Writing them
       inside would be pointless: the exception rolls the transaction back and
       takes the audit record with it, so every failed activation would vanish
       exactly when the audit trail matters most.
    """
    try:
        canonical = keys.normalize(license_key)
    except ValueError as exc:
        _equalize_timing()
        raise LicenseNotFound() from exc

    key_hash = hash_key(canonical)

    try:
        return _activate_locked(
            key_hash=key_hash,
            email=email,
            device_name=device_name,
            branch=branch,
            mode=mode,
            platform=platform,
            app_version=app_version,
            fingerprint=fingerprint,
            ip_address=ip_address,
        )
    except ActivationError as exc:
        # Outside the rolled-back transaction, so the record survives.
        license_id = getattr(exc, "license_id", None)
        if license_id is not None:
            _record(
                License.objects.filter(pk=license_id).first(),
                LicenseEvent.Event.ACTIVATION_FAILED,
                ip_address=ip_address,
                detail={"reason": exc.code, "device_name": device_name},
            )
        raise


def _activate_locked(
    *,
    key_hash: str,
    email: str,
    device_name: str,
    branch,
    mode: str,
    platform: str,
    app_version: str,
    fingerprint: str,
    ip_address: str | None,
) -> Activation:
    with transaction.atomic():
        license_obj = (
            License.objects
            # `of=("self",)` locks the licence row only. Without it Postgres
            # refuses: `branch` is nullable, so select_related produces a LEFT
            # OUTER JOIN, and FOR UPDATE cannot be applied to its nullable side.
            .select_for_update(of=("self",))
            .filter(key_hash=key_hash)
            .select_related("organization", "branch")
            .first()
        )

        if license_obj is None:
            _equalize_timing()
            raise LicenseNotFound()

        # Constant-time compare so a near-miss email cannot be probed.
        if not hmac.compare_digest(license_obj.customer_email.lower(), email.strip().lower()):
            raise _tagged(LicenseEmailMismatch(), license_obj)

        _assert_license_usable(license_obj)

        target_branch = branch or license_obj.branch or _sole_branch(license_obj)
        if target_branch is None:
            raise ActivationError("لم يتم تحديد الفرع لهذا الترخيص.", code="BRANCH_NOT_RESOLVED")

        # Re-activating an existing device name reuses its seat rather than
        # consuming a new one — reinstalling Windows must not burn a seat.
        existing = license_obj.devices.filter(device_name=device_name).first()
        if existing and existing.status == DeviceStatus.REVOKED:
            raise _tagged(DeviceRevoked(), license_obj)

        if existing is None and license_obj.seats_available <= 0:
            raise _tagged(
                DeviceLimitReached(
                    f"عدد الأجهزة المسموح بها مكتمل "
                    f"({license_obj.active_device_count}/{license_obj.max_devices}). "
                    f"يمكن للمدير إلغاء تفعيل جهاز آخر.",
                    extra={
                        "used": license_obj.active_device_count,
                        "max": license_obj.max_devices,
                    },
                ),
                license_obj,
            )

        device_secret = secrets.token_urlsafe(32)  # 256 bits, server-generated

        if existing:
            device = existing
            if device.fingerprint and fingerprint and device.fingerprint != fingerprint:
                device.fingerprint_changed_count += 1
            device.secret_hash = make_password(device_secret)
            device.mode = mode
            device.platform = platform
            device.app_version = app_version
            device.fingerprint = fingerprint
            device.last_ip = ip_address
            device.save()
        else:
            device = Device.objects.create(
                license=license_obj,
                branch=target_branch,
                device_name=device_name,
                secret_hash=make_password(device_secret),
                mode=mode,
                platform=platform,
                app_version=app_version,
                fingerprint=fingerprint,
                last_ip=ip_address,
            )

        license_obj.activation_count += 1
        license_obj.last_activation_at = timezone.now()
        if license_obj.status == LicenseStatus.PENDING:
            license_obj.status = LicenseStatus.ACTIVE
        if license_obj.branch_id is None:
            license_obj.branch = target_branch
        license_obj.save(
            update_fields=[
                "activation_count",
                "last_activation_at",
                "status",
                "branch",
                "updated_at",
            ]
        )

        _record(
            license_obj,
            LicenseEvent.Event.ACTIVATED,
            device=device,
            ip_address=ip_address,
            detail={"device_name": device_name, "mode": mode, "reactivation": bool(existing)},
        )

        token = issue_offline_token(license_obj, device)

    return Activation(device=device, device_secret=device_secret, offline_token=token)


def _sole_branch(license_obj: License):
    from apps.organizations.models import Branch

    branches = list(
        Branch.objects.filter(organization=license_obj.organization, is_active=True)[:2]
    )
    return branches[0] if len(branches) == 1 else None


def _tagged(exc: ActivationError, license_obj: License) -> ActivationError:
    """
    Attach the licence id so the caller can record the failure after rollback.

    The exception is the only thing that survives the aborted transaction, so it
    has to carry what the audit entry needs.
    """
    exc.license_id = license_obj.pk
    return exc


def _assert_license_usable(license_obj: License) -> None:
    if license_obj.status == LicenseStatus.REVOKED:
        raise _tagged(LicenseRevoked(), license_obj)
    if license_obj.status == LicenseStatus.SUSPENDED:
        raise _tagged(LicenseSuspended(), license_obj)

    now = timezone.now()
    if license_obj.starts_at > now:
        raise _tagged(
            ActivationError("لم يبدأ سريان الترخيص بعد.", code="LICENSE_NOT_STARTED"),
            license_obj,
        )
    if not license_obj.is_lifetime and license_obj.expires_at <= now:
        raise _tagged(
            LicenseExpired(
                f"انتهت صلاحية الترخيص بتاريخ {license_obj.expires_at:%Y/%m/%d}. "
                "تواصل مع مدير النظام.",
                extra={"expires_at": license_obj.expires_at.isoformat()},
            ),
            license_obj,
        )


def _record(license_obj, event, *, device=None, actor=None, ip_address=None, detail=None):
    return LicenseEvent.objects.create(
        license=license_obj,
        device=device,
        event=event,
        actor=actor,
        ip_address=ip_address,
        detail=detail or {},
    )


# ── device authentication ────────────────────────────────────────────────────


def authenticate_device(*, device_id, device_secret: str, ip_address=None) -> Device:
    device = Device.objects.select_related("license", "branch").filter(id=device_id).first()
    if device is None or not check_password(device_secret, device.secret_hash):
        raise AppError("بيانات الجهاز غير صحيحة", code="DEVICE_AUTH_FAILED", extra=None)
    if device.status == DeviceStatus.REVOKED:
        raise DeviceRevoked()
    if device.status == DeviceStatus.SUSPENDED:
        raise ActivationError("تم إيقاف هذا الجهاز مؤقتاً.", code="DEVICE_SUSPENDED")

    device.last_seen_at = timezone.now()
    device.last_ip = ip_address
    device.save(update_fields=["last_seen_at", "last_ip"])
    return device


# ── expiry policy ────────────────────────────────────────────────────────────


class ExpiryStage(StrEnum):
    ACTIVE = "ACTIVE"
    NOTICE = "NOTICE"
    WARNING = "WARNING"
    GRACE = "GRACE"
    RESTRICTED = "RESTRICTED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class LicenseState:
    stage: ExpiryStage
    can_open_new_orders: bool
    can_close_open_orders: bool
    can_read_history: bool
    days_until_expiry: int | None
    message_ar: str = ""


def evaluate_state(license_obj: License, *, now=None) -> LicenseState:
    """
    Graduated expiry (docs/06 §60).

    Never hard-kill a running cafe. A POS that goes black mid-service because a
    renewal was forgotten is worse than a few days of unpaid use, and is the
    fastest way to lose a customer permanently.

    RESTRICTED is deliberately not "locked": a cafe with eight open tables when
    the licence lapses must be able to finish serving and settle them. Blocking
    NEW orders applies commercial pressure without stranding customers or
    destroying access to the owner's own financial records.
    """
    now = now or timezone.now()

    if license_obj.status == LicenseStatus.REVOKED:
        return LicenseState(ExpiryStage.BLOCKED, False, False, True, None, "تم إلغاء الترخيص.")
    if license_obj.status == LicenseStatus.SUSPENDED:
        return LicenseState(ExpiryStage.BLOCKED, False, True, True, None, "الترخيص موقوف مؤقتاً.")

    if license_obj.is_lifetime:
        return LicenseState(ExpiryStage.ACTIVE, True, True, True, None)

    context = ScopeContext(organization_id=license_obj.organization_id)
    warn_days = resolver.get("license.warn_before_expiry_days", context)
    grace_days = resolver.get("license.grace_days_after_expiry", context)
    policy = resolver.get("license.expiry_policy", context)

    remaining = (license_obj.expires_at - now).days

    if remaining > warn_days:
        return LicenseState(ExpiryStage.ACTIVE, True, True, True, remaining)
    if remaining > 3:
        return LicenseState(
            ExpiryStage.NOTICE,
            True,
            True,
            True,
            remaining,
            f"ينتهي الترخيص خلال {remaining} يوم.",
        )
    if remaining >= 0:
        return LicenseState(
            ExpiryStage.WARNING,
            True,
            True,
            True,
            remaining,
            f"ينتهي الترخيص خلال {remaining} يوم. برجاء التجديد.",
        )

    days_over = -remaining
    if days_over <= grace_days:
        return LicenseState(
            ExpiryStage.GRACE,
            True,
            True,
            True,
            remaining,
            "انتهت صلاحية الترخيص. النظام يعمل بشكل مؤقت — برجاء التجديد.",
        )

    if policy == "READ_ONLY":
        return LicenseState(
            ExpiryStage.RESTRICTED,
            False,
            False,
            True,
            remaining,
            "انتهت صلاحية الترخيص. العرض فقط.",
        )
    return LicenseState(
        ExpiryStage.RESTRICTED,
        False,
        True,
        True,
        remaining,
        "انتهت صلاحية الترخيص. يمكن إنهاء الطلبات المفتوحة وتحصيلها فقط.",
    )


# ── offline token ────────────────────────────────────────────────────────────


def _signing_key() -> bytes:
    return keys.validate_signing_key(settings.LICENSE_SIGNING_KEY)


def public_key_bytes() -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.from_private_bytes(_signing_key())
    return private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )


def issue_offline_token(license_obj: License, device: Device, *, now=None) -> str:
    """Mint a fresh offline token, advancing the licence's monotonic sequence."""
    now = now or timezone.now()
    context = ScopeContext(organization_id=license_obj.organization_id)
    grace_hours = resolver.get("license.offline_grace_hours", context)

    # Increment in the database, not in Python: two heartbeats racing must never
    # be stamped with the same sequence, or the client ratchet would reject one.
    License.objects.filter(pk=license_obj.pk).update(token_seq=F("token_seq") + 1)
    license_obj.refresh_from_db(fields=["token_seq"])

    state = evaluate_state(license_obj, now=now)
    payload: dict[str, Any] = {
        "v": offline_token.TOKEN_VERSION,
        "seq": license_obj.token_seq,
        "license_id": str(license_obj.id),
        "branch_id": str(device.branch_id),
        "device_id": str(device.id),
        "device_mode": device.mode,
        "status": license_obj.status,
        "stage": state.stage.value,
        "can_open_new_orders": state.can_open_new_orders,
        "license_expires_at": (
            license_obj.expires_at.isoformat() if license_obj.expires_at else None
        ),
        "grace_hours": grace_hours,
        "expiry_policy": resolver.get("license.expiry_policy", context),
        "issued_at": now.isoformat(),
        "server_time": now.isoformat(),
        "token_expires_at": (now + timedelta(hours=grace_hours)).isoformat(),
    }
    return offline_token.sign(payload, _signing_key())


# ── invoice blocks ───────────────────────────────────────────────────────────


@transaction.atomic
def allocate_invoice_block(device: Device, *, size: int = INVOICE_BLOCK_SIZE) -> InvoiceBlock:
    """
    Reserve the next disjoint range for this device.

    The lock is taken on the BRANCH row, not on existing blocks. Locking blocks
    is the obvious move and it is wrong: `SELECT ... FOR UPDATE` can only lock
    rows that exist, so the very first concurrent allocations — when the table
    is empty — lock nothing, all compute start = 1, and collide. The branch row
    always exists, so it serializes every allocation for that branch including
    the first.
    """
    from apps.organizations.models import Branch

    Branch.objects.select_for_update().get(pk=device.branch_id)

    highest = InvoiceBlock.objects.filter(branch_id=device.branch_id).order_by("-range_end").first()
    start = (highest.range_end + 1) if highest else 1

    return InvoiceBlock.objects.create(
        branch=device.branch,
        device=device,
        range_start=start,
        range_end=start + size - 1,
        next_unused=start,
    )


def block_gaps(branch) -> list[dict[str, int]]:
    """
    Unused ranges, so the finance report can explain them.

    Gaps are a consequence of block allocation and are surfaced deliberately —
    hiding them would leave an accountant suspecting deleted sales.
    """
    blocks = list(InvoiceBlock.objects.filter(branch=branch).order_by("range_start"))
    gaps = []
    for block in blocks:
        if block.next_unused <= block.range_end and block.exhausted_at:
            gaps.append(
                {
                    "from": block.next_unused,
                    "to": block.range_end,
                    "count": block.range_end - block.next_unused + 1,
                }
            )
    return gaps
