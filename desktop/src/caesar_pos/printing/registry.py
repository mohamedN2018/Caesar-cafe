r"""
Which printer a job goes to.

The branch defines its printers in the Web Admin and they arrive here on the
config stream. This resolves a job to one of them, and holds the one fact the
server cannot know: **where the cable is on THIS machine.**

A serial port is a property of a terminal, not of a cafe. `\\.\COM3` on the till
by the door is a different device from `\\.\COM3` at the back, so a branch-wide
registry that claimed to know it would be wrong on two terminals out of three.
The registry says what the printer IS and where it belongs in the workflow;
`l_printer_bindings` says how this box reaches it.

Resolution order:

  1. a printer whose stations include the one that raised the job
  2. the default printer for that kind
  3. any active printer of that kind
  4. `UNROUTED` — and the job stays queued rather than being sent somewhere

There is one exception to (4), and it is the important one. **An EMPTY registry
means "not configured yet", not "configured wrong."** A cafe upgrading into this
release has no printer rows until somebody adds them, and a terminal that
stopped printing receipts the moment it updated would be a far worse bug than
any routing mistake. So with no printers at all, jobs go to the terminal's local
default exactly as they did before.

Once printers ARE configured, a job with no match stays queued. Silently sending
a kitchen ticket to the receipt roll puts an order slip in a customer's hand and
leaves the kitchen waiting.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from ..local.db import Database, transaction

logger = logging.getLogger(__name__)

RECEIPT = "RECEIPT"
KITCHEN = "KITCHEN"
REPORT = "REPORT"

#: What the Arabic rasteriser draws to when a printer is unknown. 80mm is the
#: overwhelmingly common roll; guessing 58 would crop every receipt.
DEFAULT_DOTS = 576


@dataclass(frozen=True)
class Printer:
    id: str
    name_ar: str
    code: str
    kind: str
    connection: str
    host: str
    port: int
    device_path: str
    dots: int
    copies: int
    cut_after: bool
    is_default: bool
    station_ids: tuple[str, ...]

    @property
    def target(self) -> str:
        """
        What `EscposPrinter` opens. A host:port for a network printer, a device
        path otherwise.
        """
        if self.connection == "NETWORK" and self.host:
            return f"{self.host}:{self.port}"
        return self.device_path


def _row_to_printer(row, binding: str | None) -> Printer:
    payload = json.loads(row["payload"] or "{}")
    return Printer(
        id=row["id"],
        name_ar=row["name_ar"],
        code=row["code"],
        kind=row["kind"],
        connection=row["connection"],
        host=row["host"],
        port=int(row["port"] or 9100),
        # The local binding wins. This is the whole reason the table exists.
        device_path=binding or row["device_path"],
        dots=int(row["dots"] or DEFAULT_DOTS),
        copies=int(row["copies"] or 1),
        cut_after=bool(row["cut_after"]),
        is_default=bool(row["is_default"]),
        station_ids=tuple(payload.get("station_ids") or ()),
    )


def _bindings(db: Database) -> dict[str, str]:
    return {
        row["printer_id"]: row["device_path"]
        for row in db.query("SELECT printer_id, device_path FROM l_printer_bindings")
    }


def printers(db: Database, *, kind: str | None = None) -> list[Printer]:
    where = "WHERE is_active = 1"
    params: tuple = ()
    if kind:
        where += " AND kind = ?"
        params = (kind,)

    binding = _bindings(db)
    return [
        _row_to_printer(row, binding.get(row["id"]))
        for row in db.query(
            f"SELECT * FROM m_printers {where} ORDER BY is_default DESC, name_ar",  # noqa: S608
            params,
        )
    ]


def bind(db: Database, printer_id: str, device_path: str) -> None:
    """Point one printer at a port on THIS machine."""
    with transaction(db.connection):
        db.execute(
            "INSERT INTO l_printer_bindings (printer_id, device_path, bound_at) VALUES (?, ?, ?) "
            "ON CONFLICT(printer_id) DO UPDATE SET device_path = excluded.device_path, "
            "bound_at = excluded.bound_at",
            (printer_id, device_path, datetime.now(UTC).isoformat()),
        )


def is_configured(db: Database) -> bool:
    """
    Has anybody set this branch's printers up at all?

    The difference between "no printers" and "no matching printer" decides
    whether an unroutable job falls back to the terminal's own default or waits.
    """
    return bool(db.scalar("SELECT COUNT(*) FROM m_printers WHERE is_active = 1", default=0))


def resolve(db: Database, *, kind: str, station_id: str | None = None) -> Printer | None:
    """
    The printer this job belongs on, or None.

    None is a real answer: the job stays queued and visible rather than being
    sent to whatever happens to be plugged in. A kitchen ticket on the receipt
    roll puts an order slip in a customer's hand and leaves the kitchen waiting.

    Callers must treat None differently depending on `is_configured()` — see the
    module docstring. This function only reports what the registry says.
    """
    candidates = printers(db, kind=kind)
    if not candidates:
        logger.warning("No printer configured for this kind", extra={"kind": kind})
        return None

    if station_id:
        for printer in candidates:
            if station_id in printer.station_ids:
                return printer

    return next((p for p in candidates if p.is_default), candidates[0])
