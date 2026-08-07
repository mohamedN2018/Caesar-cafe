"""
Check-in, the running meter, and check-out.

Two rules govern everything here, and they pull in opposite directions:

  * **Capacity fails closed.** It is a safety limit, not a revenue optimization.
    An area at 25/25 refuses the 26th child whether or not the server is
    reachable, and no permission overrides it.

  * **The system never resolves a child's whereabouts on its own.** There is no
    auto-checkout, however long a session runs, because an automatic checkout
    would record a child as collected when nobody collected them. A six-hour
    session raises an alert for a human to go and look.

Everything else — pricing, tariff selection, billing — is ordinary and lives in
`apps.core.play_pricing` or falls through to the order event stream.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.configuration import resolver
from apps.configuration.resolver import ScopeContext
from apps.core.exceptions import BusinessRuleError, NotFoundError
from apps.core.play_pricing import (
    PlayCharge,
    Tariff,
    compute_charge,
    elapsed_minutes,
    expected_end,
    tariff_applies_at,
)

from .models import (
    OPEN_SESSION_STATUSES,
    Child,
    Guardian,
    PlayArea,
    PlayIncident,
    PlaySession,
    PlayTariff,
    SessionStatus,
)

logger = logging.getLogger(__name__)


class CapacityExceeded(BusinessRuleError):
    status_code = 409
    code = "KIDS_AREA_FULL"


class AgeNotAllowed(BusinessRuleError):
    code = "KIDS_AGE_NOT_ALLOWED"


class GuardianNotVerified(BusinessRuleError):
    code = "KIDS_GUARDIAN_NOT_VERIFIED"


@dataclass(frozen=True)
class CheckInResult:
    session: PlaySession
    warnings: list[str]
    """Non-blocking problems the staff member must SEE — an age outside the
    area's limits when the policy is `warn`, a missing phone, and so on."""


# ── settings ─────────────────────────────────────────────────────────────────


def _context(area_or_session) -> ScopeContext:
    return ScopeContext(
        organization_id=area_or_session.organization_id,
        branch_id=area_or_session.branch_id,
    )


def settings_for(area: PlayArea) -> dict:
    """Every `kids.*` value this module reads, resolved once per operation."""
    context = _context(area)
    keys = (
        "enabled",
        "enforce_age_limits",
        "require_guardian_phone",
        "require_guardian_verification",
        "release_to_other_requires_approval",
        "grace_minutes",
        "rounding",
        "warn_before_end_minutes",
        "overdue_alert_minutes",
        "max_session_hours",
        "require_socks",
        "auto_link_to_table",
        "allow_charge_override",
        "billing_product",
        "socks_product",
        "default_tariff",
    )
    return {key: resolver.get(f"kids.{key}", context) for key in keys}


# ── tariffs ──────────────────────────────────────────────────────────────────


def to_pricing_tariff(tariff: PlayTariff, *, grace: int) -> Tariff:
    """Flatten an ORM tariff into the Django-free shape the Desktop shares."""
    return Tariff(
        mode=tariff.mode,
        entry_fee=tariff.entry_fee,
        included_minutes=tariff.included_minutes,
        package_minutes=tariff.package_minutes,
        block_minutes=tariff.block_minutes,
        block_rate=tariff.block_rate,
        grace_minutes=tariff.grace_minutes if tariff.grace_minutes is not None else grace,
        daily_cap=tariff.daily_cap,
    )


def resolve_tariff(area: PlayArea, *, moment=None, requested=None) -> PlayTariff:
    """
    Pick the tariff for a check-in.

    An explicit choice by the staff member always wins — they can see the
    weekend, the promotion, and the family standing in front of them, and the
    schedule cannot. Otherwise the highest-priority tariff whose window contains
    the moment, then the branch default, then any active tariff.
    """
    if requested is not None:
        if requested.area_id != area.id or not requested.is_active:
            raise NotFoundError("التعريفة غير متاحة لهذه الصالة", code="TARIFF_NOT_AVAILABLE")
        return requested

    moment = moment or timezone.now()
    candidates = list(area.tariffs.filter(is_active=True).order_by("-priority", "name_ar"))
    if not candidates:
        raise BusinessRuleError("لا توجد تعريفة مفعّلة لهذه الصالة", code="NO_TARIFF")

    for tariff in candidates:
        if tariff_applies_at(
            moment,
            applies_days=tariff.applies_days,
            applies_from=tariff.applies_from,
            applies_to=tariff.applies_to,
        ):
            return tariff

    configured = resolver.get("kids.default_tariff", _context(area))
    if configured:
        fallback = next((t for t in candidates if str(t.id) == str(configured)), None)
        if fallback is not None:
            return fallback

    return next((t for t in candidates if t.is_default), candidates[0])


# ── check-in ─────────────────────────────────────────────────────────────────


@transaction.atomic
def check_in(
    *,
    area: PlayArea,
    child_name: str,
    guardian_name: str,
    guardian_phone: str = "",
    age_months: int | None = None,
    birth_date=None,
    tariff: PlayTariff | None = None,
    tag_number: str,
    guardian: Guardian | None = None,
    child: Child | None = None,
    order=None,
    session_id=None,
    device_id=None,
    medical_notes: str = "",
    user=None,
    now=None,
) -> CheckInResult:
    """
    Start a session.

    Capacity is checked under a row lock on the area, so two terminals cannot
    both admit the 25th child. That lock is the whole reason this function is
    not a serializer.
    """
    now = now or timezone.now()
    config = settings_for(area)
    warnings: list[str] = []

    if not config["enabled"]:
        raise BusinessRuleError("صالة الأطفال غير مفعّلة", code="KIDS_DISABLED")

    # Lock the AREA, not the sessions: locking rows that do not exist yet locks
    # nothing, and an empty area is exactly when two check-ins race.
    locked_area = PlayArea.objects.select_for_update().get(pk=area.pk)
    occupancy = locked_area.occupancy()
    if occupancy >= locked_area.max_capacity:
        raise CapacityExceeded(
            f"الصالة ممتلئة ({occupancy}/{locked_area.max_capacity})",
            extra={"occupancy": occupancy, "capacity": locked_area.max_capacity},
        )

    if not guardian_phone and config["require_guardian_phone"] and guardian is None:
        raise BusinessRuleError("رقم هاتف ولي الأمر مطلوب", code="GUARDIAN_PHONE_REQUIRED")

    if guardian is None:
        guardian = _find_or_create_guardian(area, guardian_name, guardian_phone)
    guardian.visit_count += 1
    guardian.save(update_fields=["visit_count", "updated_at"])

    if child is None:
        child = _find_or_create_child(guardian, child_name, birth_date, medical_notes)

    # Re-snapshot the age every visit. A stale snapshot silently ages a child
    # out of — or into — the area's limits without anyone saying anything.
    resolved_age = age_months if age_months is not None else child.age_months(now)
    child.age_months_snapshot = resolved_age
    if birth_date is not None:
        child.birth_date = birth_date
    child.save(update_fields=["age_months_snapshot", "birth_date", "updated_at"])

    warnings += _check_age(locked_area, resolved_age, policy=config["enforce_age_limits"])

    if PlaySession.objects.filter(
        area=locked_area, tag_number=str(tag_number), status__in=OPEN_SESSION_STATUSES
    ).exists():
        raise BusinessRuleError(
            f"التاج رقم {tag_number} مستخدم حالياً", code="TAG_IN_USE", extra={"tag": tag_number}
        )

    chosen = resolve_tariff(locked_area, moment=now, requested=tariff)
    grace = chosen.grace_minutes
    if grace is None:
        grace = config["grace_minutes"]

    pricing = to_pricing_tariff(chosen, grace=config["grace_minutes"])

    session = PlaySession.objects.create(
        id=session_id or uuid.uuid4(),
        organization=locked_area.organization,
        branch=locked_area.branch,
        area=locked_area,
        child=child,
        guardian=guardian,
        tariff=chosen,
        order=order,
        device_id=device_id,
        tag_number=str(tag_number),
        status=SessionStatus.ACTIVE,
        checked_in_at=now,
        expected_end_at=expected_end(pricing, now),
        tariff_name_snapshot=chosen.name_ar,
        tariff_snapshot=_tariff_json(pricing),
        rounding_snapshot=config["rounding"],
        grace_minutes_snapshot=grace,
        checked_in_by=user,
        created_by=user,
    )

    if config["require_socks"] and locked_area.requires_socks:
        warnings.append("شراب الأطفال مطلوب")

    logger.info(
        "Play session opened",
        extra={
            "session": str(session.id),
            "tag": session.tag_number,
            "occupancy": occupancy + 1,
            "capacity": locked_area.max_capacity,
        },
    )
    return CheckInResult(session=session, warnings=warnings)


def _find_or_create_guardian(area: PlayArea, name: str, phone: str) -> Guardian:
    """A returning guardian is found by phone, which turns the second visit into
    three fields — the difference between a form a parent tolerates and one they
    do not."""
    if phone:
        existing = Guardian.objects.filter(branch=area.branch, phone=phone).first()
        if existing is not None:
            return existing
    return Guardian.objects.create(
        organization=area.organization,
        branch=area.branch,
        full_name=name[:150],
        phone=phone[:32],
    )


def _find_or_create_child(guardian: Guardian, name: str, birth_date, medical_notes: str) -> Child:
    existing = guardian.children.filter(first_name=name[:100]).first()
    if existing is not None:
        return existing
    return Child.objects.create(
        guardian=guardian,
        first_name=name[:100],
        birth_date=birth_date,
        medical_notes=medical_notes,
    )


def _check_age(area: PlayArea, age_months: int, *, policy: str) -> list[str]:
    """
    Age enforcement defaults to a warning, not a block.

    The staff member can see the child; the software is working from a number a
    parent said out loud. Blocking on it would override the person who can
    actually see the situation.
    """
    if policy == "off":
        return []
    if area.min_age_months <= age_months <= area.max_age_months:
        return []

    message = (
        f"سن الطفل ({age_months} شهر) خارج حدود الصالة "
        f"({area.min_age_months}–{area.max_age_months} شهر)"
    )
    if policy == "block":
        raise AgeNotAllowed(message, extra={"age_months": age_months})
    return [message]


def _tariff_json(tariff: Tariff) -> dict:
    return {
        "mode": tariff.mode,
        "entry_fee": str(tariff.entry_fee),
        "included_minutes": tariff.included_minutes,
        "package_minutes": tariff.package_minutes,
        "block_minutes": tariff.block_minutes,
        "block_rate": str(tariff.block_rate),
        "grace_minutes": tariff.grace_minutes,
        "daily_cap": str(tariff.daily_cap),
    }


def _tariff_from_snapshot(session: PlaySession) -> Tariff:
    """
    Price from the snapshot, never from the live tariff row.

    A tariff edited while a child is playing must not re-price the visit that is
    already running — the same rule as the VAT snapshot on an order.
    """
    snapshot = session.tariff_snapshot or {}
    if not snapshot:
        return to_pricing_tariff(session.tariff, grace=session.grace_minutes_snapshot)
    return Tariff(
        mode=snapshot["mode"],
        entry_fee=Decimal(snapshot["entry_fee"]),
        included_minutes=int(snapshot.get("included_minutes", 0)),
        package_minutes=int(snapshot.get("package_minutes", 0)),
        block_minutes=int(snapshot.get("block_minutes", 0)),
        block_rate=Decimal(snapshot.get("block_rate", "0")),
        grace_minutes=int(snapshot.get("grace_minutes", 0)),
        daily_cap=Decimal(snapshot.get("daily_cap", "0")),
    )


# ── the running meter ────────────────────────────────────────────────────────


def quote(session: PlaySession, *, at=None) -> PlayCharge:
    """
    What the session would cost if the child left now.

    Computed from the two timestamps rather than accumulated as time passes, so
    a slept device, a drifting clock and a late sync all reach the same answer.
    """
    at = at or timezone.now()
    minutes = elapsed_minutes(session.checked_in_at, session.checked_out_at or at)
    return compute_charge(
        _tariff_from_snapshot(session),
        minutes,
        rounding=session.rounding_snapshot or "up_to_block",
        grace_minutes=session.grace_minutes_snapshot,
    )


@transaction.atomic
def change_tariff(session: PlaySession, tariff: PlayTariff, *, user=None) -> PlaySession:
    """
    Move a running session onto a different tariff — a parent upgrading to the
    open-day rate, most often.

    Re-snapshots, so the new rule applies to the whole visit from check-in. That
    is the generous reading and the one a customer expects when they have just
    agreed to pay more.
    """
    if not session.is_open:
        raise BusinessRuleError("الجلسة منتهية", code="SESSION_CLOSED")
    if tariff.area_id != session.area_id:
        raise NotFoundError("التعريفة غير متاحة لهذه الصالة", code="TARIFF_NOT_AVAILABLE")

    config = settings_for(session.area)
    pricing = to_pricing_tariff(tariff, grace=config["grace_minutes"])

    session.tariff = tariff
    session.tariff_name_snapshot = tariff.name_ar
    session.tariff_snapshot = _tariff_json(pricing)
    session.grace_minutes_snapshot = pricing.grace_minutes
    session.expected_end_at = expected_end(pricing, session.checked_in_at)
    session.save(
        update_fields=[
            "tariff",
            "tariff_name_snapshot",
            "tariff_snapshot",
            "grace_minutes_snapshot",
            "expected_end_at",
            "updated_at",
        ]
    )
    return session


def refresh_overdue(area: PlayArea, *, now=None) -> int:
    """
    Flip sessions past their expected end to OVERDUE.

    A status, not an action: nothing is charged, nothing is closed, a human is
    simply told. Returns how many changed.
    """
    now = now or timezone.now()
    config = settings_for(area)
    cutoff = now - timedelta(minutes=config["overdue_alert_minutes"])

    return PlaySession.objects.filter(
        area=area,
        status=SessionStatus.ACTIVE,
        expected_end_at__isnull=False,
        expected_end_at__lt=cutoff,
    ).update(status=SessionStatus.OVERDUE, updated_at=now)


# ── check-out ────────────────────────────────────────────────────────────────


@transaction.atomic
def check_out(
    session: PlaySession,
    *,
    released_to: Guardian | None = None,
    verified: bool = False,
    approval=None,
    user=None,
    now=None,
) -> PlaySession:
    """
    End the session, compute the charge, and record who took the child.

    Guardian verification is the one step in this entire system whose failure is
    not a financial loss, so it is checked before anything else and cannot be
    satisfied by a client that simply did not send the flag.
    """
    now = now or timezone.now()
    locked = (
        PlaySession.objects.select_for_update()
        .select_related("area", "guardian")
        .get(pk=session.pk)
    )
    if not locked.is_open:
        raise BusinessRuleError("الجلسة منتهية بالفعل", code="SESSION_CLOSED")

    config = settings_for(locked.area)
    recipient = released_to or locked.guardian

    if config["require_guardian_verification"] and not verified:
        raise GuardianNotVerified(
            "يجب تأكيد هوية المستلم قبل تسليم الطفل",
            extra={
                "guardian": locked.guardian.full_name,
                "phone": locked.guardian.phone,
            },
        )

    if recipient.id != locked.guardian_id and config["release_to_other_requires_approval"]:
        if approval is None:
            raise GuardianNotVerified(
                "التسليم لغير ولي الأمر المسجّل يتطلب موافقة مشرف",
                code="RELEASE_APPROVAL_REQUIRED",
                extra={"registered_guardian": locked.guardian.full_name},
            )
        locked.release_approved_by = approval

    charge = compute_charge(
        _tariff_from_snapshot(locked),
        elapsed_minutes(locked.checked_in_at, now),
        rounding=locked.rounding_snapshot or "up_to_block",
        grace_minutes=locked.grace_minutes_snapshot,
    )

    locked.checked_out_at = now
    locked.checked_out_by = user
    locked.released_to_guardian = recipient
    locked.billable_minutes = charge.billable_minutes
    locked.computed_charge = charge.charge
    locked.status = SessionStatus.CHECKED_OUT
    locked.save(
        update_fields=[
            "checked_out_at",
            "checked_out_by",
            "released_to_guardian",
            "release_approved_by",
            "billable_minutes",
            "computed_charge",
            "status",
            "updated_at",
        ]
    )

    logger.info(
        "Play session closed",
        extra={
            "session": str(locked.id),
            "tag": locked.tag_number,
            "minutes": charge.billable_minutes,
            "charge": str(charge.charge),
            "released_to": recipient.full_name,
            "registered_guardian": locked.guardian.full_name,
        },
    )
    return locked


@transaction.atomic
def override_session_charge(
    session: PlaySession, *, amount: Decimal, reason: str, user=None
) -> PlaySession:
    """
    Record what a human decided, beside — never instead of — what the system
    computed. Both figures reach the report.
    """
    config = settings_for(session.area)
    if not config["allow_charge_override"]:
        raise BusinessRuleError("تعديل قيمة الجلسة غير مسموح", code="OVERRIDE_DISABLED")
    if amount < Decimal("0"):
        raise BusinessRuleError("القيمة يجب ألا تكون سالبة", code="INVALID_AMOUNT")
    if not reason.strip():
        raise BusinessRuleError("سبب التعديل مطلوب", code="REASON_REQUIRED")
    if session.order_line_id is not None:
        raise BusinessRuleError("الجلسة محتسبة على فاتورة بالفعل", code="ALREADY_BILLED")

    session.override_charge = amount
    session.override_reason = reason[:200]
    session.override_by = user
    session.save(update_fields=["override_charge", "override_reason", "override_by", "updated_at"])

    logger.warning(
        "Play charge overridden",
        extra={
            "session": str(session.id),
            "computed": str(session.computed_charge),
            "override": str(amount),
            "reason": reason,
        },
    )
    return session


# ── billing ──────────────────────────────────────────────────────────────────


def billing_variant_for(area: PlayArea):
    """
    The catalog service product a session is billed as.

    The price comes from the tariff, never from the variant — this exists so
    play revenue lands in the ordinary sales reports beside the coffee instead
    of in a parallel universe of its own.
    """
    from apps.catalog.models import ProductVariant

    if area.billing_variant_id is not None:
        return area.billing_variant

    configured = resolver.get("kids.billing_product", _context(area))
    if configured:
        variant = ProductVariant.objects.filter(
            id=configured, product__branch_id=area.branch_id
        ).first()
        if variant is not None:
            return variant

    raise BusinessRuleError(
        "لم يتم تحديد صنف احتساب جلسات الصالة — اضبط kids.billing_product",
        code="KIDS_BILLING_PRODUCT_NOT_SET",
    )


@transaction.atomic
def bill_session(session: PlaySession, *, order=None, user=None, device_id=None):
    """
    Turn a closed session into one order line.

    Appended through the ordinary order event stream, which is the whole point
    of converting at checkout rather than inventing a parallel billing path:
    VAT, service, discounts, split payment, refunds, shift reconciliation and
    the sales reports all work unmodified.
    """
    from apps.orders import services as order_services
    from apps.orders.models import EventType, OrderType

    if session.status != SessionStatus.CHECKED_OUT:
        raise BusinessRuleError("الجلسة لم تُغلق بعد", code="SESSION_NOT_CLOSED")
    if session.order_line_id is not None:
        # Idempotent by construction: a retried checkout must not bill twice.
        return session.order

    target = order or session.order
    if target is None:
        target = order_services.open_order(
            branch=session.branch,
            order_type=OrderType.DINE_IN,
            device_id=device_id or session.device_id,
            user=user,
        )
    if target.branch_id != session.branch_id:
        raise BusinessRuleError("الطلب يخص فرعاً آخر", code="CROSS_BRANCH_ORDER")

    line_id = uuid.uuid4()
    order_services.apply_events(
        target,
        [
            {
                "id": str(uuid.uuid4()),
                "type": EventType.PLAY_SESSION_CHARGED,
                "payload": {"session_id": str(session.id), "line_id": str(line_id)},
            }
        ],
        actor=user,
        device_id=device_id,
    )

    session.order = target
    session.order_line_id = line_id
    session.save(update_fields=["order", "order_line_id", "updated_at"])

    # `apply_events` refolds a re-fetched copy of the order, so the instance we
    # were handed still carries the totals from before the line landed. Handing
    # that back would let a caller take payment for the wrong amount.
    target.refresh_from_db()
    return target


def line_description(session: PlaySession) -> str:
    """
    What the receipt says.

    Carries the times and the tag so a reprint six months later still explains
    the charge without joining to a tariff that may since have changed.
    """
    checked_in = timezone.localtime(session.checked_in_at).strftime("%H:%M")
    checked_out = (
        timezone.localtime(session.checked_out_at).strftime("%H:%M")
        if session.checked_out_at
        else "—"
    )
    return (
        f"{session.area.name_ar} — {session.tariff_name_snapshot} · "
        f"{checked_in} → {checked_out} · {session.billable_minutes} دقيقة · "
        f"تاج #{session.tag_number}"
    )


# ── the live board ───────────────────────────────────────────────────────────


def serialize_session(session: PlaySession, *, now=None) -> dict:
    """
    The single payload shape for the live board — REST and, later, the socket.

    Same discipline as the kitchen ticket: one serializer, so a client that
    falls back to polling parses exactly what it was receiving a moment earlier.
    """
    now = now or timezone.now()
    running = quote(session, at=now)
    remaining = None
    if session.expected_end_at is not None and session.is_open:
        remaining = int((session.expected_end_at - now).total_seconds() // 60)

    return {
        "id": str(session.id),
        "tag_number": session.tag_number,
        "status": session.status,
        "child_name": session.child.first_name,
        "age_months": session.child.age_months_snapshot,
        "guardian_name": session.guardian.full_name,
        "guardian_phone": session.guardian.phone,
        "area_id": str(session.area_id),
        "tariff_name": session.tariff_name_snapshot,
        "checked_in_at": session.checked_in_at.isoformat(),
        "expected_end_at": (
            session.expected_end_at.isoformat() if session.expected_end_at else None
        ),
        "checked_out_at": (session.checked_out_at.isoformat() if session.checked_out_at else None),
        "elapsed_minutes": running.elapsed_minutes,
        "remaining_minutes": remaining,
        "is_overdue": session.is_overdue(now),
        "running_charge": str(running.charge),
        "capped": running.capped,
        "billable_minutes": session.billable_minutes or running.billable_minutes,
        "computed_charge": str(session.computed_charge),
        "override_charge": (
            str(session.override_charge) if session.override_charge is not None else None
        ),
        "payable": str(session.payable if not session.is_open else running.charge),
        "order_id": str(session.order_id) if session.order_id else None,
        "medical_notes": session.child.medical_notes,
    }


def board(area: PlayArea, *, now=None) -> dict:
    now = now or timezone.now()
    refresh_overdue(area, now=now)

    sessions = (
        area.sessions.filter(status__in=OPEN_SESSION_STATUSES)
        .select_related("child", "guardian", "area")
        .order_by("checked_in_at")
    )
    rows = [serialize_session(session, now=now) for session in sessions]

    return {
        "area_id": str(area.id),
        "area_name": area.name_ar,
        "occupancy": len(rows),
        "capacity": area.max_capacity,
        "sessions": rows,
    }


# ── incidents ────────────────────────────────────────────────────────────────


def log_incident(
    *,
    area: PlayArea,
    incident_type: str,
    description: str,
    session: PlaySession | None = None,
    user=None,
    occurred_at=None,
) -> PlayIncident:
    incident = PlayIncident.objects.create(
        organization=area.organization,
        branch=area.branch,
        area=area,
        session=session,
        incident_type=incident_type,
        description=description,
        reported_by=user,
        occurred_at=occurred_at or timezone.now(),
        created_by=user,
    )
    logger.warning(
        "Play incident logged",
        extra={
            "area": area.name_ar,
            "type": incident_type,
            "session": str(session.id) if session else None,
        },
    )
    return incident


# ── reporting ────────────────────────────────────────────────────────────────


def outstanding_sessions(branch, *, now=None) -> list[dict]:
    """
    Open sessions for the Z-report.

    A shift must not close silently over a child still in the play area, and the
    running charge is real outstanding liability.
    """
    now = now or timezone.now()
    sessions = (
        PlaySession.objects.filter(branch=branch, status__in=OPEN_SESSION_STATUSES)
        .select_related("child", "guardian", "area")
        .order_by("checked_in_at")
    )
    return [serialize_session(session, now=now) for session in sessions]


def report(branch, *, since=None, until=None) -> dict:
    """
    Revenue, occupancy by hour, and average duration.

    Occupancy by hour is the operationally useful one: it tells an owner when to
    staff the area and whether weekend peak pricing is justified.
    """
    sessions = PlaySession.objects.filter(
        branch=branch, status=SessionStatus.CHECKED_OUT
    ).select_related("area")
    if since:
        sessions = sessions.filter(checked_in_at__gte=since)
    if until:
        sessions = sessions.filter(checked_in_at__lt=until)

    sessions = list(sessions)
    if not sessions:
        return {
            "sessions": 0,
            "revenue": "0.00",
            "average_minutes": 0,
            "overridden": 0,
            "by_hour": {},
            "by_tariff": {},
        }

    revenue = sum((s.payable for s in sessions), Decimal("0.00"))
    minutes = sum(s.billable_minutes for s in sessions)

    by_hour: dict[str, dict] = {}
    by_tariff: dict[str, dict] = {}

    for session in sessions:
        hour = f"{timezone.localtime(session.checked_in_at).hour:02d}"
        bucket = by_hour.setdefault(hour, {"sessions": 0, "revenue": Decimal("0.00")})
        bucket["sessions"] += 1
        bucket["revenue"] += session.payable

        name = session.tariff_name_snapshot or "—"
        tariff_bucket = by_tariff.setdefault(name, {"sessions": 0, "revenue": Decimal("0.00")})
        tariff_bucket["sessions"] += 1
        tariff_bucket["revenue"] += session.payable

    return {
        "sessions": len(sessions),
        "revenue": str(revenue),
        "average_minutes": round(minutes / len(sessions)),
        "overridden": sum(1 for s in sessions if s.override_charge is not None),
        "by_hour": {h: {**v, "revenue": str(v["revenue"])} for h, v in sorted(by_hour.items())},
        "by_tariff": {n: {**v, "revenue": str(v["revenue"])} for n, v in by_tariff.items()},
    }


__all__ = [
    "AgeNotAllowed",
    "CapacityExceeded",
    "CheckInResult",
    "GuardianNotVerified",
    "bill_session",
    "billing_variant_for",
    "board",
    "change_tariff",
    "check_in",
    "check_out",
    "line_description",
    "log_incident",
    "outstanding_sessions",
    "override_session_charge",
    "quote",
    "refresh_overdue",
    "report",
    "resolve_tariff",
    "serialize_session",
    "settings_for",
    "to_pricing_tariff",
]
