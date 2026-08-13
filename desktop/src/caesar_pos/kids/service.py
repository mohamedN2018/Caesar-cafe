"""
Admitting a child from the terminal, online or not.

**This corrects an earlier decision.** The shell used to refuse offline check-in
on the grounds that capacity is a safety limit and two disconnected terminals
could both admit the last place. That reasoning is real, but the conclusion was
wrong, and the server had already settled it: `sync/handlers.py::play_check_in`
is written for a child admitted during an outage, enforces capacity a second
time on arrival, and raises a CONFLICT rather than a rejection when it fails —
"the child is already inside and nobody is going to remove them."

Refusing locally did not prevent the over-admission. It prevented the *record*
of it. A child in the room with no session is worse in every way than a session
the server later flags: nobody knows who their guardian is, nothing is billed,
and the incident log has no entry.

So capacity is checked here, hard, and the check-in proceeds. Two terminals
racing to the last place is a rare event that the server catches and a human
resolves; a terminal that cannot admit anybody because the internet is down is
an outage that closes the play area.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from ..local import outbox
from ..local.db import Database, transaction

logger = logging.getLogger(__name__)

ACTIVE = "ACTIVE"
OVERDUE = "OVERDUE"
CHECKED_OUT = "CHECKED_OUT"


class AreaFull(RuntimeError):
    """
    Capacity is a safety rule, not a target.

    Raised rather than warned: the number exists because of how many children
    one member of staff can watch, and "admit anyway" is not a decision a busy
    terminal should offer at the door.
    """


class AreaUnknown(RuntimeError):
    """The play area has not synced yet, so its capacity is unknown."""


@dataclass(frozen=True)
class Area:
    id: str
    name_ar: str
    max_capacity: int
    min_age_months: int
    max_age_months: int


def areas(db: Database) -> list[Area]:
    result = []
    for row in db.query("SELECT id, name_ar, payload FROM m_kids_areas"):
        payload = json.loads(row["payload"] or "{}")
        if payload.get("is_active") is False:
            continue
        result.append(
            Area(
                id=row["id"],
                name_ar=row["name_ar"],
                max_capacity=int(payload.get("max_capacity") or 0),
                min_age_months=int(payload.get("min_age_months") or 0),
                max_age_months=int(payload.get("max_age_months") or 0),
            )
        )
    return result


def area(db: Database, area_id: str) -> Area:
    found = next((a for a in areas(db) if a.id == area_id), None)
    if found is None:
        raise AreaUnknown("الصالة غير معروفة على هذا الجهاز — لم تتم المزامنة بعد.")
    return found


def occupancy(db: Database, area_id: str) -> int:
    return db.scalar(
        "SELECT COUNT(*) FROM l_play_sessions WHERE area_id = ? AND status IN (?, ?)",
        (area_id, ACTIVE, OVERDUE),
        default=0,
    )


def tariffs(db: Database, area_id: str) -> list[dict]:
    return [
        {"id": row["id"], "name_ar": row["name_ar"], **json.loads(row["payload"] or "{}")}
        for row in db.query(
            "SELECT id, name_ar, payload FROM m_kids_tariffs WHERE area_id = ?", (area_id,)
        )
    ]


def tag_in_use(db: Database, tag_number: str) -> bool:
    """
    A tag is how staff match a child to a guardian at the door.

    Two children on tag 14 makes that match a guess, which is the one thing this
    whole subsystem exists to prevent.
    """
    return bool(
        db.scalar(
            "SELECT COUNT(*) FROM l_play_sessions WHERE tag_number = ? AND status IN (?, ?)",
            (tag_number, ACTIVE, OVERDUE),
            default=0,
        )
    )


def check_in(
    db: Database,
    *,
    area_id: str,
    child_name: str,
    guardian_name: str,
    guardian_phone: str = "",
    tag_number: str,
    tariff_id: str | None = None,
    age_months: int | None = None,
    medical_notes: str = "",
    session_id: str | None = None,
    now: datetime | None = None,
) -> dict:
    """
    Admit a child. Works with the network down.

    The tariff is SNAPSHOTTED onto the session, exactly as the server does: a
    price edited while the child is playing must not re-price the visit, and the
    running charge on the board is computed from this copy.

    The session id is minted here so the check-out queued against it resolves
    when it arrives — the server adopts the client's id for that reason.
    """
    zone = area(db, area_id)
    now = now or datetime.now(UTC)

    if not child_name.strip() or not guardian_name.strip():
        raise ValueError("اسم الطفل واسم ولي الأمر مطلوبان")
    if not tag_number.strip():
        raise ValueError("رقم التاج مطلوب")

    inside = occupancy(db, area_id)
    if zone.max_capacity and inside >= zone.max_capacity:
        raise AreaFull(f"الصالة ممتلئة ({inside}/{zone.max_capacity}) — لا يمكن إدخال طفل آخر.")
    if tag_in_use(db, tag_number.strip()):
        raise ValueError(f"التاج {tag_number} مستخدم بالفعل مع طفل آخر")

    chosen = next((t for t in tariffs(db, area_id) if t["id"] == tariff_id), None)
    if chosen is None:
        available = tariffs(db, area_id)
        if not available:
            raise AreaUnknown("لا توجد تعريفة أسعار على هذا الجهاز — لم تتم المزامنة بعد.")
        chosen = available[0]

    session_id = session_id or str(uuid.uuid4())
    snapshot = {
        "mode": chosen.get("mode", "TIMED"),
        "entry_fee": str(chosen.get("entry_fee", "0")),
        "included_minutes": chosen.get("included_minutes", 0),
        "package_minutes": chosen.get("package_minutes", 0),
        "block_minutes": chosen.get("block_minutes", 0),
        "block_rate": str(chosen.get("block_rate", "0")),
        "grace_minutes": chosen.get("grace_minutes", 0),
        "daily_cap": str(chosen.get("daily_cap", "0")),
    }

    with transaction(db.connection):
        db.insert(
            "l_play_sessions",
            {
                "id": session_id,
                "area_id": area_id,
                "tariff_id": chosen["id"],
                "child_name": child_name.strip(),
                "guardian_name": guardian_name.strip(),
                "guardian_phone": guardian_phone.strip(),
                "medical_notes": medical_notes.strip(),
                "age_months": age_months,
                "tag_number": tag_number.strip(),
                "status": ACTIVE,
                "checked_in_at": now.isoformat(),
                "tariff_snapshot": json.dumps(snapshot, ensure_ascii=False),
            },
        )
        outbox.enqueue(
            db,
            entity_type="play_check_in",
            entity_id=session_id,
            payload={
                "session_id": session_id,
                "area_id": area_id,
                "tariff_id": chosen["id"],
                "child_name": child_name.strip(),
                "guardian_name": guardian_name.strip(),
                "guardian_phone": guardian_phone.strip(),
                "medical_notes": medical_notes.strip(),
                "age_months": age_months,
                "tag_number": tag_number.strip(),
                "checked_in_at": now.isoformat(),
            },
        )

    logger.info(
        "Child checked in",
        extra={"session": session_id, "tag": tag_number, "inside": inside + 1},
    )
    return dict(db.one("SELECT * FROM l_play_sessions WHERE id = ?", (session_id,)))


def check_out(db: Database, session_id: str, *, now: datetime | None = None) -> dict:
    """
    Mark a child as collected and queue the billing.

    The CHARGE is computed on the server. The terminal shows a running figure
    from the same vendored engine, but what the customer pays is the server's
    number — one authority for money, as everywhere else.
    """
    row = db.one("SELECT * FROM l_play_sessions WHERE id = ?", (session_id,))
    if row is None:
        raise ValueError("الجلسة غير موجودة")
    if row["status"] == CHECKED_OUT:
        raise ValueError("الطفل خرج بالفعل")

    now = now or datetime.now(UTC)

    with transaction(db.connection):
        db.update(
            "l_play_sessions",
            {"status": CHECKED_OUT, "checked_out_at": now.isoformat()},
            where="id = ?",
            params=(session_id,),
        )
        outbox.enqueue(
            db,
            entity_type="play_check_out",
            entity_id=session_id,
            payload={"session_id": session_id, "checked_out_at": now.isoformat(), "bill": True},
        )

    return dict(db.one("SELECT * FROM l_play_sessions WHERE id = ?", (session_id,)))
