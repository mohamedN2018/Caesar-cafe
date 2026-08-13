"""
The print queue.

Printing is the one thing in this system that fails constantly and matters
briefly: a jam, a paper-out, an unplugged USB cable. So it is a queue with
retries rather than a synchronous call, and — this is the part that matters —
**a print failure never blocks a sale.**

    the payment commits → the receipt is queued → the spooler tries
                                                → it fails → it retries
                                                → a human reprints from history

The alternative, printing inside the payment transaction, means a paper jam stops
the till. That is a worse outcome than a customer waiting ten seconds for a slip,
every single time.

Queued documents survive a restart because they are rows in SQLite, not objects
in memory. A crash mid-service loses no receipts.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from ..local.db import Database, transaction
from .receipt import Receipt

logger = logging.getLogger(__name__)

PENDING = "PENDING"
PRINTED = "PRINTED"
FAILED = "FAILED"

#: After this many attempts the document stops retrying and waits for a human.
#: A printer that has refused six times is out of paper or switched off, and
#: retrying every two seconds for an hour only fills the log.
MAX_ATTEMPTS = 6

#: "This branch has printers, and none of them is right for this job." Distinct
#: from `""`, which means "nobody has configured printers, use the local
#: default." Sending a kitchen ticket to the receipt roll is worse than waiting,
#: so this one holds the job in the queue where a human can see it.
UNROUTED = "\x00unrouted"


@dataclass(frozen=True)
class Job:
    id: str
    kind: str
    printer: str
    payload: dict
    attempts: int

    @property
    def lines(self) -> list[str]:
        return self.payload.get("lines", [])


def enqueue(
    db: Database, document: Receipt, *, printer: str = "", station_id: str | None = None
) -> str:
    """
    Queue a document. Never raises for a printer problem — that comes later.

    `printer` may be given explicitly; otherwise the branch registry decides at
    DRAIN time rather than here. Resolving now would freeze a job onto a printer
    that might be swapped before it prints, and the queue outlives the decision.
    """
    job_id = str(uuid.uuid4())

    with transaction(db.connection):
        db.insert(
            "print_queue",
            {
                "id": job_id,
                "kind": document.kind,
                "payload": {
                    "lines": document.lines,
                    "serial": document.serial,
                    "order_id": document.order_id,
                    "station_id": station_id,
                    "meta": document.meta,
                },
                "printer": printer,
                "status": PENDING,
                "attempts": 0,
                "created_at": datetime.now(UTC).isoformat(),
            },
        )

    return job_id


def pending(db: Database, *, limit: int = 10) -> list[Job]:
    rows = db.query(
        "SELECT id, kind, printer, payload, attempts FROM print_queue "
        "WHERE status = ? AND attempts < ? ORDER BY created_at LIMIT ?",
        (PENDING, MAX_ATTEMPTS, limit),
    )
    return [
        Job(
            id=row["id"],
            kind=row["kind"],
            printer=row["printer"],
            payload=json.loads(row["payload"]),
            attempts=row["attempts"],
        )
        for row in rows
    ]


def mark_printed(db: Database, job_id: str) -> None:
    with transaction(db.connection):
        db.update("print_queue", {"status": PRINTED}, where="id = ?", params=(job_id,))


def mark_failed(db: Database, job_id: str, error: str) -> None:
    """
    Counts an attempt and gives up at the cap — but leaves the row PENDING until
    then, so a printer switched back on drains the backlog without anyone
    re-queueing anything.
    """
    with transaction(db.connection):
        db.execute(
            "UPDATE print_queue SET attempts = attempts + 1, last_error = ? WHERE id = ?",
            (error[:500], job_id),
        )
        db.execute(
            "UPDATE print_queue SET status = ? WHERE id = ? AND attempts >= ?",
            (FAILED, job_id, MAX_ATTEMPTS),
        )


def drain(db: Database, printer, *, route: bool = True) -> dict:
    """
    Try every pending job once.

    `printer` is anything with `.print_document(lines, printer_name)`. Injected
    rather than imported so the queue is testable without hardware, and so a
    branch with a different printer library changes one object.

    The TARGET is resolved here rather than at enqueue time: the queue outlives
    the decision, and a job frozen onto a printer that was swapped this morning
    would go to a machine nobody has plugged in.
    """
    printed = failed = unrouted = 0

    for job in pending(db):
        target = job.printer
        if route and not target:
            target = _route(db, job)
            if target is UNROUTED:
                # Left queued deliberately. Sending a kitchen ticket to the
                # receipt roll puts an order slip in a customer's hand and
                # leaves the kitchen waiting; a visible unrouted job is better.
                unrouted += 1
                continue

        try:
            printer.print_document(job.lines, target)
        except Exception as exc:
            mark_failed(db, job.id, str(exc))
            failed += 1
            logger.warning("Print failed", extra={"job": job.id, "attempt": job.attempts + 1})
        else:
            mark_printed(db, job.id)
            printed += 1

    return {"printed": printed, "failed": failed, "unrouted": unrouted}


def _route(db: Database, job: Job) -> str:
    """
    Ask the branch registry where this job belongs.

    Returns a device target, `""` for "use the terminal's own default", or
    `UNROUTED` to leave the job queued. The middle case is the upgrade path: a
    branch that has not configured printers yet must keep printing exactly as it
    did before this feature existed.
    """
    from . import registry

    resolved = registry.resolve(
        db,
        kind=registry.KITCHEN if job.kind == "KITCHEN" else registry.RECEIPT,
        station_id=job.payload.get("station_id"),
    )
    if resolved:
        return resolved.target

    return UNROUTED if registry.is_configured(db) else ""


def counts(db: Database) -> dict[str, int]:
    """What the header shows when the printer is misbehaving."""
    rows = db.query("SELECT status, COUNT(*) AS n FROM print_queue GROUP BY status")
    by_status = {row["status"]: row["n"] for row in rows}
    return {
        "pending": by_status.get(PENDING, 0),
        "printed": by_status.get(PRINTED, 0),
        "failed": by_status.get(FAILED, 0),
    }


def retry_failed(db: Database) -> int:
    """
    Put the given-up jobs back. The button next to "printer is out of paper".
    """
    with transaction(db.connection):
        cursor = db.execute(
            "UPDATE print_queue SET status = ?, attempts = 0 WHERE status = ?",
            (PENDING, FAILED),
        )
    return cursor.rowcount


class EscposPrinter:
    """
    Sends a document as a raster bitmap.

    Bitmap rather than text because thermal printers cannot shape Arabic — see
    `printing/arabic.py`. The cost is a slower print and a larger payload; the
    benefit is a receipt a customer can read.
    """

    def __init__(self, *, width: int = 576, font_size: int = 24) -> None:
        self.width = width
        self.font_size = font_size

    #: Where the printer lands when the branch has not named a device. A Windows
    #: share name or a `\\.\` port goes in the job's `printer` column instead.
    DEFAULT_DEVICE = "/dev/usb/lp0"

    def print_document(self, lines: list[str], printer_name: str = "") -> None:
        from escpos.printer import File

        from .arabic import RenderOptions, render

        image = render(lines, RenderOptions(width=self.width, font_size=self.font_size))

        device = File(printer_name or self.DEFAULT_DEVICE)
        try:
            device.image(image)
            device.cut()
        finally:
            device.close()
