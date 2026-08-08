"""
The outbox (docs/07 — "Push: Desktop → Server").

This is the crux of the whole offline design, and it is one sentence:

    **Every local mutation writes its data and its outbox row in ONE SQLite
    transaction.**

If the app writes the sale and crashes before queueing it, the sale exists on
this machine and never reaches the server — a lost sale that reconciles to
nothing, discovered weeks later by an accountant who cannot explain a gap. One
transaction makes that window zero.

The `enqueue`-inside-`transaction` shape below is what enforces it. There is no
API for "queue this later", deliberately: the only way to add an outbox row is
in the same block that wrote the thing it describes.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .db import Database, transaction

logger = logging.getLogger(__name__)

PENDING = "PENDING"
SYNCED = "SYNCED"
CONFLICT = "CONFLICT"
REJECTED = "REJECTED"

#: Statuses that will never be retried. A structurally invalid operation is
#: invalid forever, and retrying it every five minutes for a week accomplishes
#: nothing except burying the failures that matter.
TERMINAL = (SYNCED, REJECTED)


@dataclass(frozen=True)
class Operation:
    op_uuid: str
    entity_type: str
    entity_id: str | None
    payload: dict
    attempts: int
    created_seq: int
    aggregate_seq: int | None

    def to_wire(self) -> dict:
        """The shape `/sync/push/` expects."""
        body: dict[str, Any] = {
            "op_uuid": self.op_uuid,
            "entity_type": self.entity_type,
            "payload": self.payload,
            "client_seq": self.created_seq,
            "client_time": datetime.now(UTC).isoformat(),
        }
        if self.entity_id:
            body["entity_id"] = self.entity_id
        if self.aggregate_seq is not None:
            body["aggregate_seq"] = self.aggregate_seq
        return body


def next_sequence(db: Database) -> int:
    """
    A monotonic per-device counter, from a table rather than `MAX(created_seq)`.

    A purged outbox would let MAX() hand out a number twice, and two operations
    sharing a sequence lose their causal order — which is the one thing this
    counter exists to preserve.
    """
    db.execute("UPDATE sync_sequence SET value = value + 1 WHERE id = 1")
    return db.scalar("SELECT value FROM sync_sequence WHERE id = 1", default=0)


def enqueue(
    db: Database,
    *,
    entity_type: str,
    payload: dict,
    entity_id: str | None = None,
    aggregate_seq: int | None = None,
    op_uuid: str | None = None,
) -> str:
    """
    Queue one operation. **Call this inside the transaction that wrote the data.**

    `op_uuid` is minted here and is the server's idempotency key: replaying a
    batch cannot create a second sale, because the server's UNIQUE constraint
    makes a duplicate insert impossible.
    """
    op_uuid = op_uuid or str(uuid.uuid4())

    db.insert(
        "sync_outbox",
        {
            "op_uuid": op_uuid,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "payload": payload,
            "status": PENDING,
            "attempts": 0,
            "created_seq": next_sequence(db),
            "aggregate_seq": aggregate_seq,
            "created_at": datetime.now(UTC).isoformat(),
        },
    )
    return op_uuid


def pending(db: Database, *, limit: int = 50, now: datetime | None = None) -> list[Operation]:
    """
    The next batch, in causal order, excluding anything still backing off.

    Ordered by `created_seq` so events for one order arrive in the order they
    happened. Different orders interleave freely — there is no reason for table 3
    to wait on table 8.
    """
    now = (now or datetime.now(UTC)).isoformat()

    rows = db.query(
        """
        SELECT op_uuid, entity_type, entity_id, payload, attempts, created_seq, aggregate_seq
        FROM sync_outbox
        WHERE status = ?
          AND (next_retry_at IS NULL OR next_retry_at <= ?)
        ORDER BY created_seq
        LIMIT ?
        """,
        (PENDING, now, limit),
    )

    return [
        Operation(
            op_uuid=row["op_uuid"],
            entity_type=row["entity_type"],
            entity_id=row["entity_id"],
            payload=json.loads(row["payload"]),
            attempts=row["attempts"],
            created_seq=row["created_seq"],
            aggregate_seq=row["aggregate_seq"],
        )
        for row in rows
    ]


def mark_synced(db: Database, op_uuid: str, result: dict | None = None) -> None:
    with transaction(db.connection):
        db.update(
            "sync_outbox",
            {"status": SYNCED, "server_result": result or {}, "last_error": ""},
            where="op_uuid = ?",
            params=(op_uuid,),
        )


def mark_conflict(db: Database, op_uuid: str, *, code: str, server_state: dict) -> None:
    """
    A conflict needs a human, so it stops being retried and becomes visible.

    Silently retrying an ORDER_ALREADY_CLOSED forever would hide the one case
    where somebody has to decide who pays for food that was already made.
    """
    with transaction(db.connection):
        db.update(
            "sync_outbox",
            {"status": CONFLICT, "last_error": code},
            where="op_uuid = ?",
            params=(op_uuid,),
        )
        row = db.one("SELECT entity_type FROM sync_outbox WHERE op_uuid = ?", (op_uuid,))
        db.insert(
            "sync_conflicts",
            {
                "op_uuid": op_uuid,
                "code": code,
                "message_ar": _message_for(code),
                "server_state": server_state,
                "entity_type": row["entity_type"] if row else "",
                "seen_at": datetime.now(UTC).isoformat(),
                "acknowledged": 0,
            },
        )

    logger.warning("Sync conflict", extra={"op": op_uuid, "code": code})


def mark_rejected(db: Database, op_uuid: str, *, code: str) -> None:
    """422-class. Never retried — see the module docstring."""
    with transaction(db.connection):
        db.update(
            "sync_outbox",
            {"status": REJECTED, "last_error": code},
            where="op_uuid = ?",
            params=(op_uuid,),
        )
    logger.error("Sync operation rejected permanently", extra={"op": op_uuid, "code": code})


def mark_retry(db: Database, op_uuid: str, *, error: str, next_retry_at: datetime) -> None:
    with transaction(db.connection):
        db.execute(
            """
            UPDATE sync_outbox
            SET attempts = attempts + 1, last_error = ?, next_retry_at = ?
            WHERE op_uuid = ?
            """,
            (error[:500], next_retry_at.isoformat(), op_uuid),
        )


def counts(db: Database) -> dict[str, int]:
    """What the header indicator reads."""
    rows = db.query("SELECT status, COUNT(*) AS n FROM sync_outbox GROUP BY status")
    by_status = {row["status"]: row["n"] for row in rows}

    return {
        "pending": by_status.get(PENDING, 0),
        "synced": by_status.get(SYNCED, 0),
        "conflict": by_status.get(CONFLICT, 0),
        "rejected": by_status.get(REJECTED, 0),
        "unresolved": by_status.get(CONFLICT, 0) + by_status.get(REJECTED, 0),
    }


def open_conflicts(db: Database) -> list[dict]:
    rows = db.query(
        "SELECT * FROM sync_conflicts WHERE acknowledged = 0 ORDER BY seen_at DESC LIMIT 50"
    )
    return [
        {
            "op_uuid": row["op_uuid"],
            "code": row["code"],
            "message_ar": row["message_ar"],
            "entity_type": row["entity_type"],
            "server_state": json.loads(row["server_state"] or "{}"),
            "seen_at": row["seen_at"],
        }
        for row in rows
    ]


def acknowledge(db: Database, op_uuid: str) -> None:
    with transaction(db.connection):
        db.update("sync_conflicts", {"acknowledged": 1}, where="op_uuid = ?", params=(op_uuid,))


def requeue(db: Database, op_uuid: str) -> None:
    """
    Put a conflicted operation back in the queue.

    Used after the human has fixed whatever caused it — the SEQUENCE_GAP case
    backfills itself, but ORDER_ALREADY_CLOSED usually ends with the items moved
    to a new order and this one acknowledged instead.
    """
    with transaction(db.connection):
        db.update(
            "sync_outbox",
            {"status": PENDING, "next_retry_at": None, "attempts": 0},
            where="op_uuid = ?",
            params=(op_uuid,),
        )


#: Conflict codes the server can return, in words a cashier can act on.
_MESSAGES = {
    "SEQUENCE_GAP": "ينقص حدث في الترتيب — سيُعاد الإرسال تلقائياً.",
    "ORDER_ALREADY_CLOSED": "الطلب تم دفعه من جهاز آخر — الأصناف التالية لم تُضف.",
    "SHIFT_ALREADY_OPEN": "توجد وردية مفتوحة بالفعل على هذا الجهاز.",
    "KIDS_AREA_FULL": "الصالة ممتلئة على الخادم — الطفل بالداخل بالفعل.",
    "CLOCK_SKEW": "ساعة الجهاز مختلفة عن الخادم — راجع إعدادات الوقت.",
}


def _message_for(code: str) -> str:
    return _MESSAGES.get(code, f"تعارض في المزامنة ({code})")
