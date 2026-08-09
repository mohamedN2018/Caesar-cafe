"""
The children's play area (صالة الأطفال).

This is not "one more table area". It introduces a second billing model to a
system that otherwise assumes `product × quantity = line total`, plus a set of
child-safety obligations that exist nowhere else in the product.

The central idea (docs/12-kids-area.md): a `PlaySession` is a RUNNING METER, not
a sale. It converts into exactly one order line at checkout and not a moment
before. Modelling it as an order item with a quantity that keeps changing would
fight both the order state machine and the event model, and would put a mutable
row in the middle of a financial record.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from apps.core.models import BaseModel, SoftDeletableModel, TenantScopedModel, uuid7
from apps.core.precision import MONEY


class PlayArea(TenantScopedModel, SoftDeletableModel):
    """
    A physical play space with a hard capacity.

    `max_capacity` is a safety limit, not a revenue setting: it fails closed,
    offline included.
    """

    name_ar = models.CharField(max_length=100, default="صالة الأطفال")
    max_capacity = models.PositiveSmallIntegerField(default=25)
    min_age_months = models.PositiveSmallIntegerField(default=12)
    max_age_months = models.PositiveSmallIntegerField(default=144)
    requires_socks = models.BooleanField(default=True)

    billing_variant = models.ForeignKey(
        "catalog.ProductVariant",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        help_text=(
            "The service product a session is billed as. Overrides "
            "`kids.billing_product`. The price comes from the tariff, never from "
            "the variant — this only gives the sale a place in the catalog."
        ),
    )
    socks_variant = models.ForeignKey(
        "catalog.ProductVariant",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        help_text="Added to the order at check-in when socks are required.",
    )
    notes = models.TextField(blank=True)

    class Meta:
        db_table = "kids_areas"
        ordering = ["name_ar"]

    def __str__(self) -> str:
        return self.name_ar

    def occupancy(self) -> int:
        """Children currently inside. The number capacity is checked against."""
        return self.sessions.filter(status__in=OPEN_SESSION_STATUSES).count()


class TariffMode(models.TextChoices):
    TIMED = "TIMED", "TIMED"
    PACKAGE = "PACKAGE", "PACKAGE"
    OPEN_DAY = "OPEN_DAY", "OPEN_DAY"


class PlayTariff(BaseModel, SoftDeletableModel):
    """
    A pricing rule. The arithmetic lives in `apps.core.play_pricing`, which the
    Desktop vendors, so this model holds only the numbers.

    Tariffs are never edited in place in a way that re-prices history: the
    session records which tariff it was charged under, and the resulting order
    line snapshots the name and the figure.
    """

    area = models.ForeignKey(PlayArea, on_delete=models.CASCADE, related_name="tariffs")
    name_ar = models.CharField(max_length=100)
    mode = models.CharField(max_length=16, choices=TariffMode.choices, default=TariffMode.TIMED)

    entry_fee = models.DecimalField(**MONEY, validators=[MinValueValidator(Decimal("0"))])
    included_minutes = models.PositiveSmallIntegerField(
        default=0, help_text="TIMED: what the entry fee covers."
    )
    package_minutes = models.PositiveSmallIntegerField(
        default=0, help_text="PACKAGE: the fixed duration the flat fee buys."
    )
    block_minutes = models.PositiveSmallIntegerField(default=0)
    block_rate = models.DecimalField(**MONEY, default=Decimal("0"))
    grace_minutes = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Null = use the branch's kids.grace_minutes.",
    )
    daily_cap = models.DecimalField(**MONEY, default=Decimal("0"), help_text="0 = uncapped.")

    applies_days = models.JSONField(
        default=list, blank=True, help_text="Monday=0. Empty = every day."
    )
    applies_from = models.TimeField(null=True, blank=True)
    applies_to = models.TimeField(null=True, blank=True)
    priority = models.SmallIntegerField(
        default=0, help_text="Highest matching priority wins at check-in."
    )
    is_default = models.BooleanField(default=False)

    class Meta:
        db_table = "kids_tariffs"
        ordering = ["-priority", "name_ar"]

    def __str__(self) -> str:
        return self.name_ar


class Guardian(TenantScopedModel):
    """
    Whoever carries responsibility for collecting a specific child.

    Deliberately separate from a customer record. They overlap but are not the
    same thing: a customer buys coffee; a guardian is who the staff may hand a
    child to. The link is optional so a walk-in who never buys anything still
    has a guardian record.
    """

    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=32, blank=True, db_index=True)
    national_id = models.CharField(max_length=32, blank=True)
    visit_count = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)

    class Meta:
        db_table = "kids_guardians"
        ordering = ["full_name"]
        indexes = [models.Index(fields=["branch", "phone"], name="idx_guardian_phone")]

    def __str__(self) -> str:
        return f"{self.full_name} · {self.phone}".strip(" ·")


class Child(BaseModel):
    """
    `age_months_snapshot` sits beside a nullable `birth_date` because parents
    frequently decline to give a date but will say "سنتين ونص". The age check
    must still work, and next year's visit must not silently reuse a stale age —
    so the snapshot is re-taken at every check-in.
    """

    guardian = models.ForeignKey(Guardian, on_delete=models.CASCADE, related_name="children")
    first_name = models.CharField(max_length=100)
    birth_date = models.DateField(null=True, blank=True)
    age_months_snapshot = models.PositiveSmallIntegerField(default=0)
    medical_notes = models.TextField(
        blank=True, help_text="Allergies, conditions staff must know about."
    )
    consent_recorded = models.BooleanField(default=False)

    class Meta:
        db_table = "kids_children"
        ordering = ["first_name"]

    def __str__(self) -> str:
        return self.first_name

    def age_months(self, on=None) -> int:
        """Computed from the birth date when there is one, else the snapshot."""
        if self.birth_date is None:
            return self.age_months_snapshot
        today = (on or timezone.now()).date()
        months = (today.year - self.birth_date.year) * 12 + (today.month - self.birth_date.month)
        if today.day < self.birth_date.day:
            months -= 1
        return max(0, months)


class SessionStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "ACTIVE"
    OVERDUE = "OVERDUE", "OVERDUE"
    CHECKED_OUT = "CHECKED_OUT", "CHECKED_OUT"
    CANCELLED = "CANCELLED", "CANCELLED"


#: A child physically inside the area. What capacity counts and what a shift
#: close reports as outstanding.
OPEN_SESSION_STATUSES = (SessionStatus.ACTIVE, SessionStatus.OVERDUE)


class PlaySession(TenantScopedModel):
    """
    One child's visit — a meter, until checkout turns it into an order line.

    `id` is client-minted (UUIDv7) like an order, so a Desktop can open a
    session during an outage and name it before the server has ever heard of it.
    """

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)

    area = models.ForeignKey(PlayArea, on_delete=models.PROTECT, related_name="sessions")
    child = models.ForeignKey(Child, on_delete=models.PROTECT, related_name="sessions")
    guardian = models.ForeignKey(Guardian, on_delete=models.PROTECT, related_name="sessions")
    tariff = models.ForeignKey(PlayTariff, on_delete=models.PROTECT, related_name="sessions")

    order = models.ForeignKey(
        "orders.Order",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="play_sessions",
        help_text="Null until the session is billed.",
    )
    order_line_id = models.UUIDField(
        null=True, blank=True, help_text="The order line this session became."
    )
    device_id = models.UUIDField(null=True, blank=True)

    tag_number = models.CharField(max_length=16, help_text="The wristband. What staff shout.")
    status = models.CharField(
        max_length=16, choices=SessionStatus.choices, default=SessionStatus.ACTIVE
    )

    checked_in_at = models.DateTimeField(default=timezone.now, db_index=True)
    expected_end_at = models.DateTimeField(null=True, blank=True)
    checked_out_at = models.DateTimeField(null=True, blank=True)

    # Snapshots of the tariff as it stood at check-in. A tariff edited mid-visit
    # must not re-price a session already running, for the same reason a VAT
    # change does not rewrite an open bill.
    tariff_name_snapshot = models.CharField(max_length=100, blank=True)
    tariff_snapshot = models.JSONField(default=dict, blank=True)
    rounding_snapshot = models.CharField(max_length=16, blank=True)
    grace_minutes_snapshot = models.PositiveSmallIntegerField(default=0)

    billable_minutes = models.PositiveIntegerField(default=0)
    computed_charge = models.DecimalField(**MONEY, default=Decimal("0"))
    override_charge = models.DecimalField(
        **MONEY,
        null=True,
        blank=True,
        help_text="Sits BESIDE the computed figure, never replaces it.",
    )
    override_reason = models.CharField(max_length=200, blank=True)
    override_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    checked_in_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    checked_out_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    released_to_guardian = models.ForeignKey(
        Guardian,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="collections",
        help_text=(
            "Who ACTUALLY collected the child, which may legitimately differ "
            "from who registered them. Storing it makes the handover an "
            "auditable fact rather than someone's memory."
        ),
    )
    release_approved_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "kids_sessions"
        ordering = ["-checked_in_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["area", "tag_number"],
                condition=models.Q(status__in=["ACTIVE", "OVERDUE"]),
                name="uniq_open_tag_per_area",
            )
        ]
        indexes = [
            models.Index(fields=["branch", "status"], name="idx_session_branch_status"),
            models.Index(fields=["branch", "-checked_in_at"], name="idx_session_branch_time"),
        ]

    def __str__(self) -> str:
        return f"#{self.tag_number} {self.child.first_name} ({self.status})"

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_SESSION_STATUSES

    @property
    def payable(self) -> Decimal:
        """What the customer is actually charged — the override when there is one."""
        return self.computed_charge if self.override_charge is None else self.override_charge

    def elapsed_minutes(self, now=None) -> int:
        from apps.core.play_pricing import elapsed_minutes

        return elapsed_minutes(self.checked_in_at, self.checked_out_at or now or timezone.now())

    def is_overdue(self, now=None) -> bool:
        if self.expected_end_at is None:
            return False
        return (now or timezone.now()) > self.expected_end_at


class IncidentType(models.TextChoices):
    INJURY = "INJURY", "INJURY"
    DISPUTE = "DISPUTE", "DISPUTE"
    LOST_ITEM = "LOST_ITEM", "LOST_ITEM"
    CAPACITY = "CAPACITY", "CAPACITY"
    OTHER = "OTHER", "OTHER"


class PlayIncident(TenantScopedModel):
    """
    Anything that went wrong.

    Kept because the failure modes of a play area are not financial, and a venue
    that cannot produce a record of what happened when a child was hurt has a
    problem no accounting report will help with.
    """

    area = models.ForeignKey(PlayArea, on_delete=models.PROTECT, related_name="incidents")
    session = models.ForeignKey(
        PlaySession, null=True, blank=True, on_delete=models.SET_NULL, related_name="incidents"
    )
    incident_type = models.CharField(
        max_length=16, choices=IncidentType.choices, default=IncidentType.OTHER
    )
    description = models.TextField()
    reported_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    occurred_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "kids_incidents"
        ordering = ["-occurred_at"]

    def __str__(self) -> str:
        return f"{self.incident_type} @ {self.occurred_at:%Y-%m-%d %H:%M}"
