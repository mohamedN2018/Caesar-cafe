"""
Users and staff.

Two credentials per user, deliberately (docs/02):

  * `password` — Argon2id, used on the Web Admin. Full entropy.
  * `pin`      — 4–6 digits, used for fast POS login and step-up approval.

A PIN is weak by construction, so it is ONLY ever accepted from an activated
device, is rate-limited per device, locks out after N failures, and can never
authenticate against the Web Admin. The device credential carries the real
entropy; the PIN identifies *which human* is standing at a trusted terminal.
"""

from __future__ import annotations

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.core.models import BaseModel

from .managers import UserManager


class User(AbstractBaseUser, PermissionsMixin, BaseModel):
    """
    Email is the identifier — cafes do not want a separate username, and an
    email is what a licence and a password reset are addressed to.

    `is_superuser`/`is_staff` from PermissionsMixin exist only for the Django
    admin, which is a break-glass tool. Product authorization is `apps.authz`.
    """

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="users",
        null=True,
        blank=True,
        help_text="Null only for the platform superuser.",
    )

    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=32, blank=True)
    full_name_ar = models.CharField(max_length=200)
    full_name_en = models.CharField(max_length=200, blank=True)

    pin_hash = models.CharField(max_length=255, blank=True)
    pin_set_at = models.DateTimeField(null=True, blank=True)

    mfa_secret = models.CharField(max_length=64, blank=True)
    mfa_enabled = models.BooleanField(default=False)
    mfa_confirmed_at = models.DateTimeField(null=True, blank=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(
        default=False, help_text="Django admin access only. Not a product role."
    )

    last_login_ip = models.GenericIPAddressField(null=True, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name_ar"]

    objects = UserManager()

    class Meta:
        db_table = "users"
        indexes = [models.Index(fields=["organization", "is_active"], name="idx_user_org_active")]

    def __str__(self) -> str:
        return f"{self.full_name_ar} <{self.email}>"

    def get_username(self) -> str:
        return self.email

    # ── PIN ──────────────────────────────────────────────────────────────────

    def set_pin(self, raw_pin: str) -> None:
        self.pin_hash = make_password(raw_pin)
        self.pin_set_at = timezone.now()

    def check_pin(self, raw_pin: str) -> bool:
        """
        Verify a PIN. Callers MUST go through `accounts.services.verify_pin`,
        which adds the lockout that makes a 4-digit secret defensible.
        """
        if not self.pin_hash:
            return False
        return check_password(raw_pin, self.pin_hash)

    @property
    def has_pin(self) -> bool:
        return bool(self.pin_hash)


class StaffProfile(BaseModel):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="staff_profile")
    employee_code = models.CharField(max_length=32, blank=True)
    national_id = models.CharField(max_length=32, blank=True)
    job_title = models.CharField(max_length=100, blank=True)
    hired_at = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        db_table = "staff_profiles"

    def __str__(self) -> str:
        return f"{self.user.full_name_ar} — {self.job_title}"


class TokenFamily(BaseModel):
    """
    One login session's refresh-token lineage.

    Rotation advances `current_jti`. A refresh presenting any other jti means two
    parties hold tokens from the same login — see tokens.rotate().
    """

    KIND_CHOICES = [("WEB", "WEB"), ("DEVICE", "DEVICE"), ("POS", "POS")]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="token_families",
        null=True,
        blank=True,
        help_text="Null for DEVICE sessions — a terminal is not a person.",
    )
    current_jti = models.UUIDField(db_index=True)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default="WEB")
    device_id = models.UUIDField(null=True, blank=True)

    rotation_count = models.PositiveIntegerField(default=0)
    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()

    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=40, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)

    class Meta:
        db_table = "token_families"
        indexes = [
            models.Index(fields=["user", "revoked_at"], name="idx_tokenfam_user_active"),
        ]

    def __str__(self) -> str:
        state = "revoked" if self.revoked_at else "active"
        return f"{self.user.email} {self.kind} ({state})"

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None and self.expires_at > timezone.now()

    @classmethod
    def revoke_all_for_user(cls, user_id, *, reason: str) -> int:
        return cls.objects.filter(user_id=user_id, revoked_at__isnull=True).update(
            revoked_at=timezone.now(), revoked_reason=reason
        )


class RecoveryCode(models.Model):
    """Single-use MFA recovery codes, hashed like passwords."""

    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="recovery_codes")
    code_hash = models.CharField(max_length=255)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "mfa_recovery_codes"

    def __str__(self) -> str:
        return f"{self.user.email} ({'used' if self.used_at else 'unused'})"


class LoginAttempt(models.Model):
    """
    Failed authentication attempts.

    Kept as rows rather than only a cache counter: threat R1 (repudiation) and
    the security review both need a durable record of who was attacked and from
    where. The cache holds the fast lockout counter; this holds the evidence.
    """

    KIND_CHOICES = [("PASSWORD", "PASSWORD"), ("PIN", "PIN"), ("MFA", "MFA")]

    id = models.BigAutoField(primary_key=True)
    identifier = models.CharField(max_length=255, db_index=True, help_text="Email or user id.")
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    succeeded = models.BooleanField(default=False)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    device_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "login_attempts"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["identifier", "-created_at"], name="idx_login_ident_time"),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {'ok' if self.succeeded else 'FAIL'} {self.identifier}"


# `Badge` lives in `badges.py` beside the minting and hashing it belongs to.
# Re-exported here so Django's app loader finds it and so `from
# apps.accounts.models import Badge` reads the same as every other model.
from .badges import Badge  # noqa: E402,F401  (import position is deliberate)
