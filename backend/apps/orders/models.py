"""
Orders as an append-only event stream (commitment C1).

An order is NOT a mutable row. It is a sequence of events — ITEM_ADDED,
ITEM_VOIDED, DISCOUNT_APPLIED — that the server folds into an aggregate.

Why this matters more here than almost anywhere else: two devices can touch one
table at the same time, and offline devices reconcile minutes later. With
mutable rows, a waiter's addition and a cashier's void race, and last-write-wins
silently loses an item that a customer is going to be charged for — or isn't.
With events, both land, order is explicit, and the fold is deterministic.

The event stream is also the audit trail. "Why is this bill 204.29?" is
answerable by replaying it, not by inferring from a total.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models

from apps.core.models import BaseModel, TenantScopedModel, uuid7
from apps.core.precision import LINE_QUANTITY as QUANTITY
from apps.core.precision import MONEY, PERCENT


class OrderType(models.TextChoices):
    DINE_IN = "DINE_IN", "DINE_IN"
    TAKE_AWAY = "TAKE_AWAY", "TAKE_AWAY"
    DELIVERY = "DELIVERY", "DELIVERY"


class OrderStatus(models.TextChoices):
    DRAFT = "DRAFT", "DRAFT"
    OPEN = "OPEN", "OPEN"
    IN_KITCHEN = "IN_KITCHEN", "IN_KITCHEN"
    READY = "READY", "READY"
    SERVED = "SERVED", "SERVED"
    PAID = "PAID", "PAID"
    CANCELLED = "CANCELLED", "CANCELLED"
    REFUNDED = "REFUNDED", "REFUNDED"


class Order(TenantScopedModel):
    """
    The folded aggregate. Totals are DERIVED — never accepted from a client.

    `id` is client-minted (UUIDv7) so an offline Desktop can name an order
    before it has ever spoken to the server.
    """

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)

    device_id = models.UUIDField(null=True, blank=True)
    shift = models.ForeignKey(
        "shifts.Shift", null=True, blank=True, on_delete=models.PROTECT, related_name="orders"
    )
    table_session = models.ForeignKey(
        "floor.TableSession",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="orders",
    )
    customer_name = models.CharField(max_length=200, blank=True)
    customer_phone = models.CharField(max_length=32, blank=True)

    local_number = models.CharField(
        max_length=32, help_text="MB-01-0042 — readable, unique per device."
    )
    order_type = models.CharField(
        max_length=16, choices=OrderType.choices, default=OrderType.DINE_IN
    )
    status = models.CharField(max_length=16, choices=OrderStatus.choices, default=OrderStatus.DRAFT)

    # Snapshotted at open time. A mid-service VAT change must not silently
    # rewrite a bill the customer is already looking at.
    vat_percent = models.DecimalField(**PERCENT, default=Decimal("0"))
    service_percent = models.DecimalField(**PERCENT, default=Decimal("0"))
    vat_inclusive = models.BooleanField(default=False)
    rounding_step = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0.01"))

    discount_percent = models.DecimalField(**PERCENT, default=Decimal("0"))
    discount_reason = models.CharField(max_length=200, blank=True)

    subtotal = models.DecimalField(**MONEY, default=Decimal("0"))
    discount_total = models.DecimalField(**MONEY, default=Decimal("0"))
    service_total = models.DecimalField(**MONEY, default=Decimal("0"))
    tax_total = models.DecimalField(**MONEY, default=Decimal("0"))
    rounding_adjustment = models.DecimalField(**MONEY, default=Decimal("0"))
    grand_total = models.DecimalField(**MONEY, default=Decimal("0"))
    paid_total = models.DecimalField(**MONEY, default=Decimal("0"))

    opened_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    opened_at = models.DateTimeField(auto_now_add=True, db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "orders"
        ordering = ["-opened_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "local_number"], name="uniq_local_number_per_branch"
            )
        ]
        indexes = [
            models.Index(fields=["branch", "-opened_at"], name="idx_order_branch_time"),
            models.Index(
                fields=["branch", "status"],
                condition=models.Q(status__in=["OPEN", "IN_KITCHEN", "READY", "SERVED"]),
                name="idx_order_open_board",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.local_number} ({self.status})"

    @property
    def balance_due(self) -> Decimal:
        return self.grand_total - self.paid_total

    @property
    def is_settled(self) -> bool:
        return self.balance_due <= Decimal("0")

    @property
    def is_open(self) -> bool:
        return self.status in {
            OrderStatus.OPEN,
            OrderStatus.IN_KITCHEN,
            OrderStatus.READY,
            OrderStatus.SERVED,
        }


class EventType(models.TextChoices):
    ORDER_OPENED = "ORDER_OPENED", "ORDER_OPENED"
    ITEM_ADDED = "ITEM_ADDED", "ITEM_ADDED"
    ITEM_QUANTITY_CHANGED = "ITEM_QUANTITY_CHANGED", "ITEM_QUANTITY_CHANGED"
    ITEM_VOIDED = "ITEM_VOIDED", "ITEM_VOIDED"
    ITEM_NOTE_SET = "ITEM_NOTE_SET", "ITEM_NOTE_SET"
    ITEM_PRICE_OVERRIDDEN = "ITEM_PRICE_OVERRIDDEN", "ITEM_PRICE_OVERRIDDEN"
    DISCOUNT_APPLIED = "DISCOUNT_APPLIED", "DISCOUNT_APPLIED"
    ORDER_FIRED = "ORDER_FIRED", "ORDER_FIRED"
    PLAY_SESSION_CHARGED = "PLAY_SESSION_CHARGED", "PLAY_SESSION_CHARGED"
    TABLE_ASSIGNED = "TABLE_ASSIGNED", "TABLE_ASSIGNED"
    CUSTOMER_ASSIGNED = "CUSTOMER_ASSIGNED", "CUSTOMER_ASSIGNED"
    PAYMENT_TAKEN = "PAYMENT_TAKEN", "PAYMENT_TAKEN"
    ORDER_CLOSED = "ORDER_CLOSED", "ORDER_CLOSED"
    ORDER_VOIDED = "ORDER_VOIDED", "ORDER_VOIDED"


class OrderEvent(models.Model):
    """
    One immutable fact about an order.

    `id` is client-minted, which makes it the idempotency key: replaying a
    pushed event is a no-op, so a Desktop that times out mid-sync can retry
    without duplicating a line.

    `sequence` is gapless per order, so a missing event is detectable rather
    than merely absent.
    """

    id = models.UUIDField(primary_key=True, editable=False)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="events")
    sequence = models.PositiveIntegerField()
    event_type = models.CharField(max_length=32, choices=EventType.choices)
    payload = models.JSONField(default=dict, blank=True)

    device_id = models.UUIDField(null=True, blank=True)
    actor = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    approved_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Set when a step-up approval authorized this event.",
    )

    occurred_at = models.DateTimeField(help_text="Client clock — may be skewed.")
    recorded_at = models.DateTimeField(auto_now_add=True, help_text="Server clock — authoritative.")

    class Meta:
        db_table = "order_events"
        ordering = ["order", "sequence"]
        constraints = [
            models.UniqueConstraint(fields=["order", "sequence"], name="uniq_sequence_per_order")
        ]
        indexes = [models.Index(fields=["order", "sequence"], name="idx_event_order_seq")]

    def __str__(self) -> str:
        return f"{self.order_id} #{self.sequence} {self.event_type}"


class ItemStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "ACTIVE"
    VOIDED = "VOIDED", "VOIDED"


class OrderItem(BaseModel):
    """
    A projection of the event stream, kept for querying and printing.

    Every `*_snapshot` field exists because a receipt is a legal record of what
    was sold at what price. Joining live to the catalog would silently rewrite
    history the moment a manager changes a price.
    """

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    line_id = models.UUIDField(help_text="Client-minted; the id events refer to.")
    variant = models.ForeignKey(
        "catalog.ProductVariant", on_delete=models.PROTECT, related_name="order_items"
    )
    station = models.ForeignKey(
        "kitchen.Station", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    name_snapshot = models.CharField(max_length=250)
    unit_price_snapshot = models.DecimalField(**MONEY)
    cost_snapshot = models.DecimalField(**MONEY, default=Decimal("0"))
    tax_exempt_snapshot = models.BooleanField(default=False)

    #: A manually set unit price, and never a replacement for the snapshot.
    #: Overwriting `unit_price_snapshot` would erase the answer to the only
    #: question anybody asks about an override afterwards: what was it SUPPOSED
    #: to be? Keeping both makes "sold at 40 instead of 60" a fact the reports
    #: can name, rather than a line that merely looks cheap.
    price_override = models.DecimalField(**MONEY, null=True, blank=True)
    price_override_reason = models.CharField(max_length=200, blank=True)

    quantity = models.DecimalField(**QUANTITY, default=Decimal("1"))
    discount_percent = models.DecimalField(**PERCENT, default=Decimal("0"))

    line_gross = models.DecimalField(**MONEY, default=Decimal("0"))
    line_discount = models.DecimalField(**MONEY, default=Decimal("0"))
    line_total = models.DecimalField(**MONEY, default=Decimal("0"))

    status = models.CharField(max_length=8, choices=ItemStatus.choices, default=ItemStatus.ACTIVE)
    note = models.CharField(max_length=200, blank=True)
    fired_at = models.DateTimeField(null=True, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "order_items"
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(fields=["order", "line_id"], name="uniq_line_per_order")
        ]

    def __str__(self) -> str:
        return f"{self.quantity}× {self.name_snapshot}"

    @property
    def is_active(self) -> bool:
        return self.status == ItemStatus.ACTIVE

    @property
    def effective_unit_price(self) -> Decimal:
        """
        What this line is actually charged at.

        `Decimal("0")` is a legitimate override — a comped item — so the test is
        for None, not for falsiness. `if self.price_override:` would silently
        charge full price for every giveaway.
        """
        return self.unit_price_snapshot if self.price_override is None else self.price_override

    @property
    def price_was_overridden(self) -> bool:
        return self.price_override is not None


class OrderItemModifier(models.Model):
    id = models.BigAutoField(primary_key=True)
    order_item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name="modifiers")
    modifier = models.ForeignKey(
        "catalog.Modifier", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    name_snapshot = models.CharField(max_length=150)
    price_delta_snapshot = models.DecimalField(**MONEY, default=Decimal("0"))

    class Meta:
        db_table = "order_item_modifiers"

    def __str__(self) -> str:
        return self.name_snapshot
