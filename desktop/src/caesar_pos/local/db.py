"""
The SQLite connection.

Three PRAGMAs carry real weight here, and none of them is a preference:

  * **WAL.** A POS will lose power mid-transaction — a cleaner unplugs it, the
    breaker trips during service. WAL is crash-safe and lets a read (the floor
    board refreshing) proceed during a write (the cashier adding an item).
  * **`synchronous = NORMAL`.** With WAL this survives an application crash and
    can lose the last commits only to an OS crash or power cut. FULL would fsync
    on every write and make the POS feel slow on the cheap SSDs these machines
    have; the exposure is bounded by the outbox, which is re-pushed on restart.
  * **`foreign_keys = ON`.** Off by default in SQLite, which surprises everyone
    exactly once.

The connection is per-thread. SQLite objects are not shareable across threads,
and the sync worker runs on its own.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..config import paths
from .schema import SCHEMA_VERSION, WRITABLE_PREFIXES, apply_migrations

logger = logging.getLogger(__name__)

_local = threading.local()


class MirrorIsReadOnly(RuntimeError):
    """
    Raised when application code tries to write an `m_` table.

    The mirror is server-authoritative. A terminal that could edit its own copy
    of a price is a terminal that can charge whatever it likes, and the drift
    would be invisible until a customer complained.
    """


def connect(path: Path | None = None, *, migrate: bool = True) -> sqlite3.Connection:
    """Open (or return the thread's) connection, migrated and configured."""
    existing = getattr(_local, "connection", None)
    target = str(path or paths().database)

    if existing is not None and getattr(_local, "path", None) == target:
        # The cached handle may have been closed directly — the app does that on
        # shutdown, and the activation flow restarts in-process. Returning a
        # closed connection would fail on the first query with a message that
        # says nothing about why.
        try:
            existing.execute("SELECT 1")
        except sqlite3.ProgrammingError:
            existing = None
        else:
            return existing

    if existing is not None:
        existing.close()

    connection = sqlite3.connect(target, isolation_level=None, check_same_thread=False)
    connection.row_factory = sqlite3.Row

    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")

    if migrate:
        version = apply_migrations(connection)
        if version != SCHEMA_VERSION:
            raise RuntimeError(f"Local database is at version {version}, expected {SCHEMA_VERSION}")

    _local.connection = connection
    _local.path = target
    return connection


def close() -> None:
    connection = getattr(_local, "connection", None)
    if connection is not None:
        connection.close()
        _local.connection = None
        _local.path = None


@contextmanager
def transaction(connection: sqlite3.Connection | None = None):
    """
    An explicit transaction, because `isolation_level=None` turns autocommit on.

    This is what makes the outbox pattern work: the data write and its outbox row
    commit together or not at all. Everything that mutates local state must go
    through here.
    """
    connection = connection or connect()
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except Exception:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")


class Database:
    """
    A thin, honest wrapper. Not an ORM.

    The one behaviour worth having is `assert_writable`: every mutation names a
    table, and a mutation naming an `m_` table is a bug that should surface in
    development rather than as a mispriced order in production.
    """

    def __init__(self, connection: sqlite3.Connection | None = None) -> None:
        self.connection = connection or connect()

    # ── reads ────────────────────────────────────────────────────────────────

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.connection.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self.connection.execute(sql, params).fetchone()

    def scalar(self, sql: str, params: tuple = (), default: Any = None) -> Any:
        row = self.one(sql, params)
        return row[0] if row is not None else default

    # ── writes ───────────────────────────────────────────────────────────────

    @staticmethod
    def assert_writable(table: str) -> None:
        if not table.startswith(WRITABLE_PREFIXES):
            raise MirrorIsReadOnly(
                f"'{table}' is a mirror table. It is replaced by the puller, never "
                "written by the application — a terminal has no authority over a "
                "server fact."
            )

    def insert(self, table: str, row: dict[str, Any]) -> None:
        self.assert_writable(table)
        self._insert(table, row)

    def upsert_mirror(self, table: str, row: dict[str, Any], *, key: str = "id") -> None:
        """
        The ONLY write path into a mirror table, used by the puller.

        Named so that a grep for it returns every place the mirror is touched —
        which is the audit you want when a price on a terminal is wrong.
        """
        columns = ", ".join(row)
        placeholders = ", ".join("?" for _ in row)
        updates = ", ".join(f"{c} = excluded.{c}" for c in row if c != key)

        self.connection.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) "  # noqa: S608
            f"ON CONFLICT({key}) DO UPDATE SET {updates}",
            tuple(_encode(v) for v in row.values()),
        )

    def delete_mirror(self, table: str, entity_id: str, *, key: str = "id") -> None:
        self.connection.execute(f"DELETE FROM {table} WHERE {key} = ?", (entity_id,))  # noqa: S608

    def update(self, table: str, row: dict[str, Any], *, where: str, params: tuple) -> None:
        self.assert_writable(table)
        assignments = ", ".join(f"{column} = ?" for column in row)
        self.connection.execute(
            f"UPDATE {table} SET {assignments} WHERE {where}",  # noqa: S608
            (*(_encode(v) for v in row.values()), *params),
        )

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.connection.execute(sql, params)

    def _insert(self, table: str, row: dict[str, Any]) -> None:
        columns = ", ".join(row)
        placeholders = ", ".join("?" for _ in row)
        self.connection.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",  # noqa: S608
            tuple(_encode(v) for v in row.values()),
        )

    # ── housekeeping ─────────────────────────────────────────────────────────

    def purge_synced(self, *, older_than_days: int = 30) -> int:
        """
        Drop local rows whose sync is confirmed and which are older than the
        retention window (docs/07).

        The local database stays a few hundred MB rather than growing without
        bound on a counter PC nobody maintains. History lives on the server,
        which is what the server is for.
        """
        from datetime import UTC, datetime, timedelta

        cutoff = (datetime.now(UTC) - timedelta(days=older_than_days)).isoformat()

        with transaction(self.connection):
            cursor = self.connection.execute(
                "DELETE FROM l_orders WHERE synced_at IS NOT NULL AND synced_at < ?", (cutoff,)
            )
            removed = cursor.rowcount
            # Children go with the parent. No FK cascade, because these rows are
            # deliberately not FK-linked: an event may arrive for an order the
            # purge already removed, and that must not error.
            for table in ("l_order_events", "l_order_items", "l_payments"):
                self.connection.execute(
                    f"DELETE FROM {table} WHERE order_id NOT IN (SELECT id FROM l_orders)"  # noqa: S608
                )
            self.connection.execute(
                "DELETE FROM sync_outbox WHERE status = 'SYNCED' AND created_at < ?", (cutoff,)
            )

        self.connection.execute("VACUUM")
        return removed


def _encode(value: Any) -> Any:
    """dicts and lists become JSON; everything else passes through."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return int(value)
    return value


def decode(value: str | None) -> Any:
    if not value:
        return None
    return json.loads(value)
