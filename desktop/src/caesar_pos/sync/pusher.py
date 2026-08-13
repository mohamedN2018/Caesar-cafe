"""
Draining the outbox.

The response handling is the whole file, and each branch is a different kind of
failure with a different correct answer:

    200/207 per-op   APPLIED → done. CONFLICT → a human. REJECTED → never again.
    401 / 403        the device token expired; refresh and retry ONCE.
    5xx / timeout    the server's problem; back off with jitter and keep the
                     operation pending. Nothing is lost.
    422 batch-level  the batch itself was malformed — a client bug. Rejecting
                     each operation would discard real sales, so the batch is
                     retried and the error is loud.

The distinction that matters most: **a network failure never marks anything
rejected.** An outage is not a reason to discard a sale, and a queue that
survives a week of bad wifi is the entire point of the design.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from ..api.client import ApiClient, ApiError, NetworkUnavailable
from ..local import outbox
from ..local.db import Database
from .backoff import next_retry_at

logger = logging.getLogger(__name__)

DEFAULT_BATCH = 50
MAX_BATCH = 500


@dataclass(frozen=True)
class PushOutcome:
    attempted: int = 0
    applied: int = 0
    conflicts: int = 0
    rejected: int = 0
    deferred: int = 0
    """Left pending after a network or server failure. Nothing was lost."""
    offline: bool = False

    @property
    def made_progress(self) -> bool:
        return self.applied > 0


def drain_once(
    db: Database,
    client: ApiClient,
    *,
    batch_size: int = DEFAULT_BATCH,
    now: datetime | None = None,
) -> PushOutcome:
    """
    Push one batch. Returns what happened; never raises for an expected failure.

    Called on a timer. One batch per tick rather than "drain everything" so a
    terminal with 3,000 queued operations still repaints its UI between batches
    instead of freezing on reconnect — which is exactly when staff are watching.

    `now` is injectable so backoff behaviour is testable without waiting five
    minutes for it. A retry policy nobody can test is a retry policy nobody has
    checked.
    """
    operations = outbox.pending(db, limit=min(batch_size, MAX_BATCH), now=now)
    if not operations:
        return PushOutcome()

    batch_id = str(uuid.uuid4())
    body = {"batch_id": batch_id, "operations": [op.to_wire() for op in operations]}

    try:
        response = client.post("/sync/push/", json=body)
    except NetworkUnavailable as exc:
        _defer_all(db, operations, error=str(exc))
        return PushOutcome(attempted=len(operations), deferred=len(operations), offline=True)
    except ApiError as exc:
        return _handle_batch_error(db, operations, exc)

    return _apply_results(db, operations, response or {})


def _apply_results(db: Database, operations, response: dict) -> PushOutcome:
    by_uuid = {op.op_uuid: op for op in operations}
    results = {r["op_uuid"]: r for r in response.get("results", [])}

    applied = conflicts = rejected = deferred = 0

    for op_uuid, operation in by_uuid.items():
        result = results.get(op_uuid)

        if result is None:
            # The server did not mention it. Not an error — keep it pending and
            # send it again; the op_uuid makes that safe.
            outbox.mark_retry(
                db, op_uuid, error="NO_RESULT", next_retry_at=next_retry_at(operation.attempts)
            )
            deferred += 1
            continue

        status = result.get("status")

        if status == "APPLIED":
            outbox.mark_synced(db, op_uuid, result.get("result"))
            applied += 1
        elif status == "CONFLICT":
            outbox.mark_conflict(
                db,
                op_uuid,
                code=result.get("code") or "CONFLICT",
                server_state=result.get("server_state") or {},
            )
            conflicts += 1
        elif status == "REJECTED":
            outbox.mark_rejected(db, op_uuid, code=result.get("code") or "REJECTED")
            rejected += 1
        else:
            # PENDING on the server side — it accepted the row but has not
            # applied it. Ask again rather than assuming either outcome.
            outbox.mark_retry(
                db, op_uuid, error=str(status), next_retry_at=next_retry_at(operation.attempts)
            )
            deferred += 1

    if applied:
        _mark_orders_synced(db, [op for op in operations if op.op_uuid in results])

    logger.info(
        "Push batch complete",
        extra={"applied": applied, "conflicts": conflicts, "rejected": rejected},
    )
    return PushOutcome(
        attempted=len(operations),
        applied=applied,
        conflicts=conflicts,
        rejected=rejected,
        deferred=deferred,
    )


def _handle_batch_error(db: Database, operations, exc: ApiError) -> PushOutcome:
    """
    The whole batch failed. What that means depends on why.

    A 401 is a token that expired mid-shift — ordinary, and the engine refreshes
    and retries. A 5xx is the server's problem and costs nothing to wait out. The
    one case that must NOT discard operations is a 4xx that is not an auth
    failure: a client bug that rejected fifty real sales would be far worse than
    a queue that stops until somebody looks at it.
    """
    if exc.status in (401, 403):
        _defer_all(db, operations, error=exc.code, immediate=True)
        return PushOutcome(attempted=len(operations), deferred=len(operations))

    _defer_all(db, operations, error=f"{exc.status}:{exc.code}")

    if exc.status and 400 <= exc.status < 500:
        logger.error(
            "Push batch refused by the server — the queue is now stalled and needs a human",
            extra={"code": exc.code, "status": exc.status, "operations": len(operations)},
        )

    return PushOutcome(
        attempted=len(operations),
        deferred=len(operations),
        offline=exc.status is None or exc.status >= 500,
    )


def _defer_all(db: Database, operations, *, error: str, immediate: bool = False) -> None:
    for operation in operations:
        outbox.mark_retry(
            db,
            operation.op_uuid,
            error=error,
            next_retry_at=(datetime.now(UTC) if immediate else next_retry_at(operation.attempts)),
        )


def _mark_orders_synced(db: Database, operations) -> None:
    """
    Stamp `synced_at` so the retention purge knows what is safe to drop.

    Only orders whose operations ALL landed — a partially-synced order still has
    something the server has not seen, and purging it would lose it.
    """
    order_ids = {op.entity_id for op in operations if op.entity_id}
    if not order_ids:
        return

    now = datetime.now(UTC).isoformat()
    placeholders = ", ".join("?" for _ in order_ids)

    db.execute(
        f"""
        UPDATE l_orders SET synced_at = ?
        WHERE id IN ({placeholders})
          AND NOT EXISTS (
              SELECT 1 FROM sync_outbox
              WHERE sync_outbox.entity_id = l_orders.id
                AND sync_outbox.status != 'SYNCED'
          )
        """,  # noqa: S608 — placeholders are generated, values are bound
        (now, *order_ids),
    )
