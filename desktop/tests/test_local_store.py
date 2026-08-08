"""
The local store and the outbox.

The load-bearing test is `TestOutboxAtomicity`: if the app writes a sale and
crashes before queueing it, the sale exists on this machine and never reaches
the server — a lost sale that reconciles to nothing. One transaction makes that
window zero, and this is where that claim is checked rather than asserted.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from caesar_pos.local import outbox
from caesar_pos.local.db import Database, MirrorIsReadOnly, connect, transaction
from caesar_pos.local.schema import SCHEMA_VERSION


@pytest.fixture
def db(tmp_path):
    connection = connect(tmp_path / "test.db")
    yield Database(connection)
    connection.close()


def make_order(db: Database, order_id: str = "order-1", *, number: str = "MB-01-0001") -> None:
    db.insert(
        "l_orders",
        {
            "id": order_id,
            "local_number": number,
            "opened_at": datetime.now(UTC).isoformat(),
        },
    )


# ── the schema ───────────────────────────────────────────────────────────────


class TestSchema:
    def test_it_migrates_to_the_current_version(self, db) -> None:
        assert db.scalar("PRAGMA user_version") == SCHEMA_VERSION

    def test_migrating_twice_is_a_no_op(self, tmp_path) -> None:
        """A terminal restarts constantly. Startup must be idempotent."""
        path = tmp_path / "twice.db"
        first = connect(path)
        version = first.execute("PRAGMA user_version").fetchone()[0]
        first.close()

        second = connect(path)
        assert second.execute("PRAGMA user_version").fetchone()[0] == version
        second.close()

    def test_wal_is_on(self, db) -> None:
        """
        A POS WILL lose power mid-transaction — a cleaner unplugs it, a breaker
        trips. WAL is what makes that survivable.
        """
        assert db.scalar("PRAGMA journal_mode").lower() == "wal"

    def test_foreign_keys_are_on(self, db) -> None:
        assert db.scalar("PRAGMA foreign_keys") == 1

    def test_prices_are_stored_as_text_not_real(self, db) -> None:
        """
        SQLite REAL would reintroduce exactly the imprecision the Decimal money
        module exists to avoid.
        """
        columns = db.query("PRAGMA table_info(m_variants)")
        by_name = {c["name"]: c["type"] for c in columns}

        assert by_name["price"] == "TEXT"
        assert by_name["cost"] == "TEXT"

    def test_order_money_columns_are_text_too(self, db) -> None:
        columns = {c["name"]: c["type"] for c in db.query("PRAGMA table_info(l_orders)")}
        for column in ("subtotal", "tax_total", "grand_total", "paid_total"):
            assert columns[column] == "TEXT", column


# ── the mirror is read-only ──────────────────────────────────────────────────


class TestMirrorIsReadOnly:
    def test_the_application_cannot_insert_into_a_mirror_table(self, db) -> None:
        """
        A terminal that can edit its own copy of a price is a terminal that can
        charge whatever it likes, and the drift is invisible until a customer
        complains.
        """
        with pytest.raises(MirrorIsReadOnly, match="server fact"):
            db.insert(
                "m_variants", {"id": "x", "product_id": "p", "price": "1.00", "payload": "{}"}
            )

    def test_the_application_cannot_update_a_mirror_table(self, db) -> None:
        with pytest.raises(MirrorIsReadOnly):
            db.update("m_products", {"name_ar": "مزوّر"}, where="id = ?", params=("x",))

    def test_the_puller_can_write_through_upsert_mirror(self, db) -> None:
        """The one path in, named so a grep finds every place the mirror moves."""
        db.upsert_mirror(
            "m_variants",
            {"id": "v1", "product_id": "p1", "price": "60.00", "payload": "{}"},
        )
        assert db.scalar("SELECT price FROM m_variants WHERE id = 'v1'") == "60.00"

    def test_upsert_replaces_rather_than_duplicating(self, db) -> None:
        for price in ("60.00", "70.00"):
            db.upsert_mirror(
                "m_variants",
                {"id": "v1", "product_id": "p1", "price": price, "payload": "{}"},
            )

        assert db.scalar("SELECT COUNT(*) FROM m_variants") == 1
        assert db.scalar("SELECT price FROM m_variants WHERE id = 'v1'") == "70.00"

    def test_local_tables_are_writable(self, db) -> None:
        make_order(db)
        assert db.scalar("SELECT COUNT(*) FROM l_orders") == 1


# ── atomicity ────────────────────────────────────────────────────────────────


class TestOutboxAtomicity:
    def test_the_sale_and_its_outbox_row_commit_together(self, db) -> None:
        with transaction(db.connection):
            make_order(db)
            outbox.enqueue(db, entity_type="order_open", payload={"order_id": "order-1"})

        assert db.scalar("SELECT COUNT(*) FROM l_orders") == 1
        assert db.scalar("SELECT COUNT(*) FROM sync_outbox") == 1

    def test_a_crash_before_the_commit_leaves_neither(self, db) -> None:
        """
        The window this design exists to close. Without one transaction, the sale
        is on this machine and the server never hears about it — a lost sale that
        reconciles to nothing.
        """
        with pytest.raises(RuntimeError):
            with transaction(db.connection):
                make_order(db)
                outbox.enqueue(db, entity_type="order_open", payload={"order_id": "order-1"})
                raise RuntimeError("power cut")

        assert db.scalar("SELECT COUNT(*) FROM l_orders") == 0
        assert db.scalar("SELECT COUNT(*) FROM sync_outbox") == 0

    def test_a_failure_after_the_data_write_takes_the_data_with_it(self, db) -> None:
        """The reverse direction: no orphaned outbox row pointing at nothing."""
        with pytest.raises(sqlite3.IntegrityError):
            with transaction(db.connection):
                make_order(db, "order-1")
                make_order(db, "order-1")  # duplicate primary key

        assert db.scalar("SELECT COUNT(*) FROM l_orders") == 0


# ── ordering and idempotency ─────────────────────────────────────────────────


class TestOutboxOrdering:
    def test_operations_drain_in_causal_order(self, db) -> None:
        for index in range(5):
            with transaction(db.connection):
                outbox.enqueue(db, entity_type="order_event", payload={"n": index})

        sequence = [op.payload["n"] for op in outbox.pending(db)]
        assert sequence == [0, 1, 2, 3, 4]

    def test_the_sequence_never_repeats_after_a_purge(self, db) -> None:
        """
        A counter from MAX(created_seq) would hand out a number twice once old
        rows are purged, and two operations sharing a sequence lose their order.
        """
        with transaction(db.connection):
            outbox.enqueue(db, entity_type="order_event", payload={})
        first = db.scalar("SELECT created_seq FROM sync_outbox")

        db.execute("DELETE FROM sync_outbox")
        with transaction(db.connection):
            outbox.enqueue(db, entity_type="order_event", payload={})

        assert db.scalar("SELECT created_seq FROM sync_outbox") > first

    def test_the_op_uuid_is_the_idempotency_key(self, db) -> None:
        with transaction(db.connection):
            op_uuid = outbox.enqueue(db, entity_type="order_open", payload={})

        wire = outbox.pending(db)[0].to_wire()
        assert wire["op_uuid"] == op_uuid
        assert "client_time" in wire, "the server records skew; it needs the client clock"

    def test_a_batch_is_limited(self, db) -> None:
        for _ in range(10):
            with transaction(db.connection):
                outbox.enqueue(db, entity_type="order_event", payload={})

        assert len(outbox.pending(db, limit=4)) == 4


# ── outcomes ─────────────────────────────────────────────────────────────────


class TestOutcomes:
    def _one(self, db) -> str:
        with transaction(db.connection):
            return outbox.enqueue(db, entity_type="order_open", payload={"x": 1})

    def test_a_synced_operation_leaves_the_queue(self, db) -> None:
        op_uuid = self._one(db)
        outbox.mark_synced(db, op_uuid, {"order_id": "1"})

        assert outbox.pending(db) == []
        assert outbox.counts(db)["pending"] == 0

    def test_a_rejected_operation_is_never_retried(self, db) -> None:
        """
        A structurally invalid operation is invalid forever. Retrying it every
        five minutes for a week buries the failures that matter.
        """
        op_uuid = self._one(db)
        outbox.mark_rejected(db, op_uuid, code="UNKNOWN_ENTITY_TYPE")

        assert outbox.pending(db) == []
        assert outbox.counts(db)["rejected"] == 1

    def test_a_conflict_stops_retrying_and_becomes_visible(self, db) -> None:
        op_uuid = self._one(db)
        outbox.mark_conflict(
            db, op_uuid, code="ORDER_ALREADY_CLOSED", server_state={"local_number": "MB-01-0042"}
        )

        assert outbox.pending(db) == []

        conflicts = outbox.open_conflicts(db)
        assert len(conflicts) == 1
        assert conflicts[0]["server_state"]["local_number"] == "MB-01-0042"
        assert "دفعه من جهاز آخر" in conflicts[0]["message_ar"]

    def test_an_unknown_conflict_code_still_gets_a_readable_message(self, db) -> None:
        op_uuid = self._one(db)
        outbox.mark_conflict(db, op_uuid, code="SOMETHING_NEW", server_state={})

        assert "SOMETHING_NEW" in outbox.open_conflicts(db)[0]["message_ar"]

    def test_a_retry_backs_off_and_stays_pending(self, db) -> None:
        """A network failure never discards a sale."""
        op_uuid = self._one(db)
        outbox.mark_retry(
            db, op_uuid, error="NETWORK", next_retry_at=datetime.now(UTC) + timedelta(minutes=5)
        )

        assert outbox.pending(db) == [], "not yet — it is backing off"
        assert outbox.counts(db)["pending"] == 1, "but still queued, not lost"

        later = datetime.now(UTC) + timedelta(minutes=6)
        assert len(outbox.pending(db, now=later)) == 1

    def test_a_conflict_can_be_requeued_after_a_human_fixes_it(self, db) -> None:
        op_uuid = self._one(db)
        outbox.mark_conflict(db, op_uuid, code="SEQUENCE_GAP", server_state={})
        outbox.requeue(db, op_uuid)

        assert len(outbox.pending(db)) == 1

    def test_acknowledging_clears_the_indicator(self, db) -> None:
        op_uuid = self._one(db)
        outbox.mark_conflict(db, op_uuid, code="SEQUENCE_GAP", server_state={})
        outbox.acknowledge(db, op_uuid)

        assert outbox.open_conflicts(db) == []


# ── retention ────────────────────────────────────────────────────────────────


class TestRetention:
    def test_confirmed_orders_older_than_the_window_are_purged(self, db) -> None:
        """
        The local database stays a few hundred MB rather than growing without
        bound on a counter PC nobody maintains.
        """
        old = (datetime.now(UTC) - timedelta(days=40)).isoformat()
        make_order(db, "old-order")
        db.update("l_orders", {"synced_at": old}, where="id = ?", params=("old-order",))

        assert db.purge_synced() == 1
        assert db.scalar("SELECT COUNT(*) FROM l_orders") == 0

    def test_an_unsynced_order_is_never_purged_however_old(self, db) -> None:
        """
        Purging it would destroy the only copy of a sale the server has not seen
        — the exact loss the outbox exists to prevent.
        """
        make_order(db, "unsynced")
        db.execute("UPDATE l_orders SET opened_at = ? WHERE id = 'unsynced'", ("2020-01-01",))

        db.purge_synced()
        assert db.scalar("SELECT COUNT(*) FROM l_orders") == 1

    def test_a_recently_synced_order_is_kept(self, db) -> None:
        make_order(db, "recent")
        db.update(
            "l_orders",
            {"synced_at": datetime.now(UTC).isoformat()},
            where="id = ?",
            params=("recent",),
        )

        assert db.purge_synced() == 0


def test_json_values_round_trip(db) -> None:
    with transaction(db.connection):
        outbox.enqueue(db, entity_type="order_event", payload={"items": [1, 2], "note": "بدون سكر"})

    payload = outbox.pending(db)[0].payload
    assert payload["items"] == [1, 2]
    assert payload["note"] == "بدون سكر", "Arabic survives the JSON round trip"


def test_the_payload_column_keeps_fields_this_client_does_not_know(db) -> None:
    """
    A server newer than this client sends fields it has never heard of. Keeping
    the whole payload means a re-pull after an upgrade has them, rather than
    having silently dropped them.
    """
    db.upsert_mirror(
        "m_products",
        {
            "id": "p1",
            "name_ar": "كابتشينو",
            "payload": json.dumps({"name_ar": "كابتشينو", "future_field": "kept"}),
        },
    )

    stored = json.loads(db.scalar("SELECT payload FROM m_products WHERE id = 'p1'"))
    assert stored["future_field"] == "kept"
