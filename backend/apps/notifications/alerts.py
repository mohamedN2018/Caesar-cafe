"""
What is worth a phone buzzing in somebody's pocket.

The whole feature turns on restraint. An owner who gets nine notifications a
night mutes the app, and then the one that mattered — the drawer that closed
four hundred short — is muted too. So every rule here answers three questions
before it fires:

  1. **Is it actionable?** "Sales are down" is not an alert, it is a report.
     "Table 8 has been waiting 25 minutes" is somebody walking to the kitchen.
  2. **Is it new?** The evaluator runs every few minutes and re-reads the same
     conditions. A late ticket is still late five minutes later, and without a
     dedupe key one late order becomes twelve notifications.
  3. **Is it worth waking them for?** Quiet hours suppress everything except a
     failed backup, because that one is only discoverable the morning after and
     the whole point is to discover it before the day's data depends on it.

Each rule is a plain function returning `Alert`s. No sending, no database
writes — so the thresholds can be tested against a fixture without a push
service anywhere near it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone

from apps.configuration import resolver
from apps.configuration.resolver import ScopeContext

from .models import AlertKind

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Alert:
    kind: str
    #: Identifies the SUBJECT, not the occurrence: "ticket 94 is late", so a
    #: second evaluation of the same ticket collapses onto the first.
    dedupe_key: str
    title: str
    body: str
    url: str = ""


def settings_for(branch) -> dict:
    context = ScopeContext(organization_id=branch.organization_id, branch_id=branch.id)
    return resolver.get_many(
        [
            "alerts.enabled",
            "alerts.kinds",
            "alerts.cash_variance_threshold",
            "alerts.kitchen_late_minutes",
            "alerts.kids_overdue_minutes",
            "alerts.terminal_offline_minutes",
            "alerts.quiet_hours",
        ],
        context,
    )


def in_quiet_hours(window: str, *, now: datetime | None = None) -> bool:
    """
    `HH:MM-HH:MM`, and it may wrap past midnight — which is the normal case for
    a cafe, whose quiet hours are 02:00 to 09:00.
    """
    if not window or "-" not in window:
        return False

    try:
        start_text, end_text = window.split("-", 1)
        start = time.fromisoformat(start_text.strip())
        end = time.fromisoformat(end_text.strip())
    except ValueError:
        # A malformed window means "no quiet hours", not "silence everything".
        # Failing towards delivery is the safe direction for an alert system.
        logger.warning("Unparseable quiet-hours window", extra={"window": window})
        return False

    current = (now or timezone.localtime()).time()
    if start <= end:
        return start <= current < end
    return current >= start or current < end


# ── the rules ────────────────────────────────────────────────────────────────


def cash_variance(branch, config: dict) -> list[Alert]:
    """
    A drawer that closed short or over by more than the tolerance.

    Both directions. An overage is not good news — it usually means a sale went
    unrecorded, which is the same problem as a shortage wearing a friendlier
    face.
    """
    from apps.shifts.models import Shift, ShiftStatus

    threshold = Decimal(str(config["alerts.cash_variance_threshold"]))
    since = timezone.now() - timedelta(hours=24)

    alerts = []
    for shift in Shift.objects.filter(
        branch=branch, status=ShiftStatus.CLOSED, closed_at__gte=since
    ).select_related("closed_by"):
        variance = shift.variance or Decimal("0")
        if abs(variance) < threshold:
            continue

        direction = "عجز" if variance < 0 else "زيادة"
        who = shift.closed_by.full_name_ar if shift.closed_by_id else "غير معروف"
        alerts.append(
            Alert(
                kind=AlertKind.CASH_VARIANCE,
                dedupe_key=f"shift:{shift.id}",
                title=f"{direction} في الدرج {abs(variance)} ج.م",
                body=f"وردية أُغلقت بواسطة {who}",
                url="/shifts",
            )
        )
    return alerts


def kitchen_late(branch, config: dict) -> list[Alert]:
    """
    A ticket far past its station's target.

    The threshold is ON TOP of the station's own target, not an absolute number:
    a grill that targets twelve minutes and a coffee bar that targets four
    cannot share one definition of late, and using an absolute would either
    scream about every espresso or never mention a burnt steak.
    """
    from apps.kitchen.models import OPEN_STATUSES, KitchenTicket

    grace = int(config["alerts.kitchen_late_minutes"])
    now = timezone.now()

    alerts = []
    for ticket in KitchenTicket.objects.filter(
        branch=branch, status__in=OPEN_STATUSES
    ).select_related("station", "order"):
        target = ticket.station.target_prep_minutes or 0
        waited = int((now - ticket.created_at).total_seconds() // 60)
        if waited < target + grace:
            continue

        alerts.append(
            Alert(
                kind=AlertKind.KITCHEN_LATE,
                dedupe_key=f"ticket:{ticket.id}",
                title=f"تذكرة #{ticket.ticket_number} متأخرة {waited} دقيقة",
                body=f"{ticket.station.name_ar} · المستهدف {target} دقيقة",
                url="/kitchen",
            )
        )
    return alerts


def kids_overdue(branch, config: dict) -> list[Alert]:
    """
    A child well past their expected end.

    Not a billing alert — the tariff already handles overtime. This is about a
    child still in the play area long after the guardian expected to collect
    them, which is a supervision question.
    """
    from apps.kids.models import PlaySession, SessionStatus

    grace = int(config["alerts.kids_overdue_minutes"])
    now = timezone.now()

    alerts = []
    for session in PlaySession.objects.filter(
        area__branch=branch, status__in=[SessionStatus.ACTIVE, SessionStatus.OVERDUE]
    ).select_related("child", "guardian"):
        expected = session.expected_end_at
        if expected is None or now < expected + timedelta(minutes=grace):
            continue

        over = int((now - expected).total_seconds() // 60)
        alerts.append(
            Alert(
                kind=AlertKind.KIDS_OVERDUE,
                dedupe_key=f"play:{session.id}",
                title=f"{session.child.first_name} تجاوز الوقت بـ {over} دقيقة",
                body=f"ولي الأمر: {session.guardian.full_name} · {session.guardian.phone}",
                url="/kids",
            )
        )
    return alerts


def terminal_offline(branch, config: dict) -> list[Alert]:
    """
    A device that has stopped syncing.

    Below the threshold this is not worth mentioning: the terminal keeps selling
    and the outbox keeps queueing, which is the whole design. Past it, the
    concern is not the outage but its length — an hour of sales on one machine
    is an hour the owner cannot see and a machine whose loss would cost them.
    """
    from apps.licensing.models import Device, DeviceStatus

    threshold = int(config["alerts.terminal_offline_minutes"])
    cutoff = timezone.now() - timedelta(minutes=threshold)

    alerts = []
    for device in Device.objects.filter(branch=branch, status=DeviceStatus.ACTIVE):
        seen = device.last_seen_at
        if seen is None or seen >= cutoff:
            continue

        minutes = int((timezone.now() - seen).total_seconds() // 60)
        alerts.append(
            Alert(
                kind=AlertKind.TERMINAL_OFFLINE,
                # Rounded to the hour, so a terminal offline all evening raises
                # one alert an hour rather than one every evaluation.
                dedupe_key=f"device:{device.id}:{minutes // 60}",
                title=f"{device.device_name} غير متصل من {minutes} دقيقة",
                body="الجهاز يبيع ويصطف بالعمليات، لكن مبيعاته غير ظاهرة بعد.",
                url="/devices",
            )
        )
    return alerts


def backup_failed(branch, config: dict) -> list[Alert]:
    """
    Last night's backup did not complete.

    The quietest serious failure in the product: nothing breaks, nobody notices,
    and it only matters on the day it matters. Exempt from quiet hours for that
    reason — knowing at 03:00 that there is no backup leaves a morning to fix
    it; knowing at 09:00 does not.
    """
    from apps.ops.models import BackupRecord, BackupStatus

    since = timezone.now() - timedelta(hours=26)
    latest = BackupRecord.objects.filter(started_at__gte=since).order_by("-started_at").first()

    if latest is None:
        return [
            Alert(
                kind=AlertKind.BACKUP_FAILED,
                dedupe_key=f"missing:{timezone.localdate()}",
                title="لم تُنفَّذ نسخة احتياطية أمس",
                body="لا توجد نسخة خلال ٢٦ ساعة. راجع المهمة الليلية.",
                url="/backups",
            )
        ]

    if latest.status == BackupStatus.FAILED:
        return [
            Alert(
                kind=AlertKind.BACKUP_FAILED,
                dedupe_key=f"run:{latest.id}",
                title="فشلت النسخة الاحتياطية",
                body=latest.error[:180] or "بدون تفاصيل — راجع سجل الخادم.",
                url="/backups",
            )
        ]
    return []


def sync_conflicts(branch, config: dict) -> list[Alert]:
    """
    Operations waiting on a person.

    Off by default: the Desktop already shows these in its header with a button,
    and the cashier standing at the terminal is better placed than the owner at
    home. On for an owner who wants to know their staff are ignoring them.
    """
    from apps.sync.models import SyncConflict

    waiting = SyncConflict.objects.filter(branch=branch, resolved_at__isnull=True).count()
    if not waiting:
        return []

    return [
        Alert(
            kind=AlertKind.SYNC_CONFLICT,
            dedupe_key=f"count:{waiting}",
            title=f"{waiting} عملية مزامنة تحتاج مراجعة",
            body="عمليات رفضها الخادم ولم يتعامل معها أحد بعد.",
            url="/sync",
        )
    ]


#: Every rule, by the kind it raises. Iterated in this order, which is roughly
#: "how much money is at stake".
RULES = {
    AlertKind.BACKUP_FAILED: backup_failed,
    AlertKind.CASH_VARIANCE: cash_variance,
    AlertKind.TERMINAL_OFFLINE: terminal_offline,
    AlertKind.KITCHEN_LATE: kitchen_late,
    AlertKind.KIDS_OVERDUE: kids_overdue,
    AlertKind.SYNC_CONFLICT: sync_conflicts,
}

#: Delivered even during quiet hours. Only one thing qualifies: knowing at 03:00
#: that there is no backup leaves a morning to fix it.
ALWAYS_DELIVER = {AlertKind.BACKUP_FAILED}


def evaluate(branch, *, now: datetime | None = None) -> list[Alert]:
    """
    Every alert this branch should raise right now, before deduplication.

    Deduplication is the caller's job because it needs the database; this stays
    a pure read so the thresholds can be tested directly.
    """
    config = settings_for(branch)
    if not config["alerts.enabled"]:
        return []

    enabled = set(config["alerts.kinds"] or [])
    quiet = in_quiet_hours(config["alerts.quiet_hours"], now=now)

    raised: list[Alert] = []
    for kind, rule in RULES.items():
        if kind not in enabled:
            continue
        if quiet and kind not in ALWAYS_DELIVER:
            continue
        try:
            raised.extend(rule(branch, config))
        except Exception:
            logger.exception("Alert rule failed", extra={"rule": kind})

    return raised
