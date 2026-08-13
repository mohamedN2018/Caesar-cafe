"""
Appending to, and reading from, the change log.

The whole file exists to preserve one property:

    A pull must never hand out a cursor position that a not-yet-committed row
    could later fall behind.

Everything else — the deferred append, the txid column, the `pg_snapshot_xmin`
guard — is machinery in service of that sentence. Get it wrong and the failure
is a row that is skipped forever by exactly one device, which is the worst
possible shape for a bug in a system that handles money.
"""

from __future__ import annotations

import logging

from django.db import connection, transaction
from django.db.models import Func

from .models import ChangeLog, Operation, Stream

logger = logging.getLogger(__name__)


class CurrentXactId(Func):
    """
    `pg_current_xact_id()` — a 64-bit, wraparound-free transaction id.

    Stored on the row so a reader can tell whether the writer had committed.
    """

    function = "pg_current_xact_id"
    template = "%(function)s()::text::bigint"
    output_field = ChangeLog._meta.get_field("txid")


#: Rows from transactions at or above this are excluded from every pull.
#: `pg_snapshot_xmin` is the oldest transaction still in flight, so anything
#: below it has definitively committed or aborted and can never appear later.
_VISIBILITY_GUARD = "pg_snapshot_xmin(pg_current_snapshot())::text::bigint"


def record(
    *,
    branch_id,
    stream: str,
    entity_type: str,
    entity_id,
    payload: dict,
    operation: str = Operation.UPSERT,
    immediate: bool = False,
) -> None:
    """
    Append a change, AFTER the surrounding transaction commits.

    Deferring matters: appending inside a long business transaction would hold a
    seq open for its whole duration, widening the window in which a pull can
    read past it. `on_commit` reduces that window to the length of this one tiny
    insert — and the txid guard closes what remains.

    `immediate=True` is for callers already outside a transaction (a management
    command, a backfill) where waiting for a commit that will never come would
    silently drop the row.
    """
    if branch_id is None:
        # Org-level objects with no branch cannot be routed to a device.
        return

    def _append() -> None:
        try:
            ChangeLog.objects.create(
                branch_id=branch_id,
                stream=stream,
                entity_type=entity_type,
                entity_id=entity_id,
                operation=operation,
                payload=payload,
                txid=CurrentXactId(),
            )
        except Exception:
            # A failed append must not roll back a sale that has already
            # committed. The device re-pulls the entity on its next full sync,
            # so the cost is staleness, not loss — but it is loud in the log.
            logger.exception(
                "Change log append failed",
                extra={"stream": stream, "entity_type": entity_type, "entity_id": str(entity_id)},
            )

    if immediate or not connection.in_atomic_block:
        _append()
    else:
        transaction.on_commit(_append)


def visible_changes(*, branch_id, stream: str, after: int):
    """
    Changes a device has not seen, excluding anything that might still be
    in flight.

    Returns a queryset ordered by seq. The caller slices it — the limit belongs
    to the request, not here.
    """
    return (
        ChangeLog.objects.filter(branch_id=branch_id, stream=stream, seq__gt=after)
        .extra(where=[f"txid < {_VISIBILITY_GUARD}"])  # noqa: S610 — no user input
        .order_by("seq")
    )


def head(*, branch_id, stream: str) -> int:
    """The highest safely-readable seq. What an empty pull reports as its cursor."""
    latest = visible_changes(branch_id=branch_id, stream=stream, after=0).last()
    return latest.seq if latest else 0


STREAMS = tuple(Stream.values)
