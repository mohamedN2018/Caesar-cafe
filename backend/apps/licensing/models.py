"""Licences, devices, and the records that make activation auditable."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import BaseModel, SequentialBaseModel


class LicenseStatus(models.TextChoices):
    PENDING = "PENDING", "PENDING"
    ACTIVE = "ACTIVE", "ACTIVE"
    SUSPENDED = "SUSPENDED", "SUSPENDED"
    EXPIRED = "EXPIRED", "EXPIRED"
    REVOKED = "REVOKED", "REVOKED"


class LicenseType(models.TextChoices):
    TRIAL = "TRIAL", "TRIAL"
    MONTHLY = "MONTHLY", "MONTHLY"
    YEARLY = "YEARLY", "YEARLY"
    LIFETIME = "LIFETIME", "LIFETIME"


class DeviceStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "ACTIVE"
    SUSPENDED = "SUSPENDED", "SUSPENDED"
    REVOKED = "REVOKED", "REVOKED"


class DeviceMode(models.TextChoices):
    POS = "POS", "POS"
    KDS = "KDS", "KDS"
    BOTH = "BOTH", "BOTH"


class License(BaseModel):
    """
    A licence to run the Desktop client at one branch.

    The plaintext key is NEVER stored. `key_hash` is HMAC-SHA256 with a
    server-side pepper — a deliberate exception to the usual password-hashing
    rule, because activation must find a licence BY its key, which needs a
    deterministic hash to index. A slow per-record hash would force a full table
    scan. The properties that make fast hashes dangerous for passwords do not
    apply: an 80-bit random key has no dictionary and no human-chosen patterns.
    """

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT, related_name="licenses"
    )
    branch = models.ForeignKey(
        "organizations.Branch",
        on_delete=models.PROTECT,
        related_name="licenses",
        null=True,
        blank=True,
        help_text="Bound at first activation.",
    )

    key_hash = models.CharField(max_length=64, unique=True, db_index=True)
    key_prefix = models.CharField(max_length=16, help_text="QSR-7X29, for display.")

    #: The readable key — written ONLY when `settings.DEMO_MODE` is on.
    #:
    #: A licence key is a credential, and the reason this model stores an HMAC is
    #: that a copied database should yield nothing that opens a till. That is the
    #: right default and it stays the default: with DEMO_MODE off this column is
    #: never written, `test_plaintext_key_is_never_stored` still passes unchanged,
    #: and a real installation behaves exactly as it did.
    #:
    #: A demo is a different situation with a different cost. There, the key
    #: vanishing after one render is the problem — somebody is showing the product
    #: and needs to activate a second browser without regenerating and
    #: invalidating the first. So it is kept, deliberately, behind the same switch
    #: that already publishes the demo staff logins.
    key_plaintext = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text="Demo mode only. Empty on any real installation.",
    )
    key_last4 = models.CharField(max_length=8)

    # Kept, unused, and no longer required.
    #
    # This is a single-café product: the organisation IS the customer, and these
    # were a second copy of its identity, free to drift from the first. Nothing
    # reads them now — activation asks for the key and a device name and nothing
    # else. Dropped from the forms rather than from the table, because a column
    # that once held real addresses is not worth deleting to save two rows of
    # schema.
    customer_email = models.EmailField(blank=True, default="")
    customer_name = models.CharField(max_length=200, blank=True, default="")

    license_type = models.CharField(
        max_length=16, choices=LicenseType.choices, default=LicenseType.YEARLY
    )
    status = models.CharField(
        max_length=16, choices=LicenseStatus.choices, default=LicenseStatus.PENDING
    )

    starts_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True, help_text="Null = lifetime.")

    max_devices = models.PositiveIntegerField(default=3)
    activation_count = models.PositiveIntegerField(default=0)
    last_activation_at = models.DateTimeField(null=True, blank=True)

    token_seq = models.PositiveBigIntegerField(
        default=0,
        help_text="Monotonic counter stamped into offline tokens; drives the client ratchet.",
    )

    notes = models.TextField(blank=True)

    class Meta:
        db_table = "licenses"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "status"], name="idx_license_org_status")]

    def __str__(self) -> str:
        return f"{self.masked_key} ({self.status})"

    @property
    def masked_key(self) -> str:
        return f"{self.key_prefix}-••••-••••-{self.key_last4}"

    @property
    def is_lifetime(self) -> bool:
        return self.expires_at is None

    @property
    def active_device_count(self) -> int:
        return self.devices.exclude(status=DeviceStatus.REVOKED).count()

    @property
    def seats_available(self) -> int:
        return max(0, self.max_devices - self.active_device_count)

    def days_until_expiry(self, now=None) -> int | None:
        if self.is_lifetime:
            return None
        return (self.expires_at - (now or timezone.now())).days


class Device(BaseModel):
    """
    An activated terminal.

    Authentication is by `secret_hash` — a server-generated 256-bit secret,
    Argon2id-hashed. NOT by hardware fingerprint (commitment C4): MAC addresses
    are trivially spoofable, change legitimately on a Windows update or a new
    dock, and are computed by the client, which therefore can lie about them.

    `fingerprint` is stored anyway, but is NEVER used for authorization. Its
    value is diagnostic: a device whose fingerprint changes daily has probably
    had its credential copied across machines. Detection, not prevention.
    """

    license = models.ForeignKey(License, on_delete=models.CASCADE, related_name="devices")
    branch = models.ForeignKey(
        "organizations.Branch", on_delete=models.PROTECT, related_name="devices"
    )

    device_name = models.CharField(max_length=100)
    secret_hash = models.CharField(max_length=255)
    mode = models.CharField(max_length=8, choices=DeviceMode.choices, default=DeviceMode.POS)

    platform = models.CharField(max_length=64, blank=True)
    app_version = models.CharField(max_length=32, blank=True)
    fingerprint = models.CharField(
        max_length=128, blank=True, help_text="Advisory telemetry only. Never authorizes."
    )

    status = models.CharField(
        max_length=16, choices=DeviceStatus.choices, default=DeviceStatus.ACTIVE
    )

    first_activated_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_ip = models.GenericIPAddressField(null=True, blank=True)
    fingerprint_changed_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "devices"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["license", "device_name"], name="uniq_device_name_per_license"
            )
        ]
        indexes = [models.Index(fields=["branch", "status"], name="idx_device_branch_status")]

    def __str__(self) -> str:
        return f"{self.device_name} ({self.status})"

    @property
    def is_usable(self) -> bool:
        return self.status == DeviceStatus.ACTIVE

    def minutes_since_seen(self, now=None) -> float | None:
        if self.last_seen_at is None:
            return None
        return ((now or timezone.now()) - self.last_seen_at).total_seconds() / 60


class LicenseEvent(SequentialBaseModel):
    """
    Append-only licence history.

    Every create, activation, suspension, renewal and revocation lands here with
    the acting admin. `/licensing/licenses/{id}/events/` is what answers "who
    revoked this and when".
    """

    class Event(models.TextChoices):
        CREATED = "CREATED", "CREATED"
        ACTIVATED = "ACTIVATED", "ACTIVATED"
        ACTIVATION_FAILED = "ACTIVATION_FAILED", "ACTIVATION_FAILED"
        SUSPENDED = "SUSPENDED", "SUSPENDED"
        RESUMED = "RESUMED", "RESUMED"
        RENEWED = "RENEWED", "RENEWED"
        REVOKED = "REVOKED", "REVOKED"
        SEATS_CHANGED = "SEATS_CHANGED", "SEATS_CHANGED"
        KEY_REGENERATED = "KEY_REGENERATED", "KEY_REGENERATED"
        DEVICE_REVOKED = "DEVICE_REVOKED", "DEVICE_REVOKED"
        DEVICE_RESET = "DEVICE_RESET", "DEVICE_RESET"
        DEVICE_UNLOCKED = "DEVICE_UNLOCKED", "DEVICE_UNLOCKED"
        """A manager cleared the PIN lockout on a terminal."""
        HEARTBEAT_DENIED = "HEARTBEAT_DENIED", "HEARTBEAT_DENIED"

    license = models.ForeignKey(
        License, on_delete=models.CASCADE, related_name="events", null=True, blank=True
    )
    device = models.ForeignKey(
        Device, on_delete=models.SET_NULL, related_name="events", null=True, blank=True
    )
    event = models.CharField(max_length=32, choices=Event.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    detail = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        db_table = "license_events"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["license", "-created_at"], name="idx_licevent_lic_time")]

    def __str__(self) -> str:
        return f"{self.event} @ {self.created_at:%Y-%m-%d %H:%M}"


class InvoiceBlock(BaseModel):
    """
    A range of invoice numbers reserved to one device (commitment C9).

    Egyptian receipts want gapless sequential numbers, but three offline
    terminals cannot each pick "the next one" without colliding — and asking the
    server defeats the point of working offline. So each device consumes a
    disjoint pre-allocated range.

    Blocks introduce gaps in the global sequence (device A ends at 1187, B
    starts at 1500). Those gaps are REPORTED, never hidden: an accountant asking
    "where are invoices 1188–1499?" gets a documented answer instead of a
    suspicion of deleted sales. Fabricating a gapless sequence after the fact
    would mean rewriting numbers already printed and handed to customers.
    """

    branch = models.ForeignKey(
        "organizations.Branch", on_delete=models.PROTECT, related_name="invoice_blocks"
    )
    device = models.ForeignKey(Device, on_delete=models.PROTECT, related_name="invoice_blocks")

    range_start = models.PositiveBigIntegerField()
    range_end = models.PositiveBigIntegerField()
    next_unused = models.PositiveBigIntegerField()

    allocated_at = models.DateTimeField(default=timezone.now)
    exhausted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "invoice_blocks"
        ordering = ["range_start"]
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "range_start"], name="uniq_block_start_per_branch"
            ),
            models.CheckConstraint(
                condition=models.Q(range_end__gte=models.F("range_start")),
                name="block_range_is_ordered",
            ),
        ]
        indexes = [models.Index(fields=["device", "exhausted_at"], name="idx_block_device_open")]

    def __str__(self) -> str:
        return f"{self.range_start}-{self.range_end} → {self.device.device_name}"

    @property
    def size(self) -> int:
        return self.range_end - self.range_start + 1

    @property
    def used(self) -> int:
        return self.next_unused - self.range_start

    @property
    def remaining(self) -> int:
        return self.range_end - self.next_unused + 1

    @property
    def is_exhausted(self) -> bool:
        return self.next_unused > self.range_end
