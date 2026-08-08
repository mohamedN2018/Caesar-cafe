"""
The operations that need a person.

`outbox.mark_conflict` stops retrying and waits for a human — that is the whole
point of it. Until this screen existed there was no human it could wait for: the
header could say "⚠️ تعارض (٢)" and nothing in the product could tell you which
two, so the most careful part of the sync design was unreachable.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from caesar_pos.local import outbox
from caesar_pos.local.db import Database, connect
from caesar_pos.orders import service
from caesar_pos.ui.sync import ConflictsDialog

FINANCE = {
    "vat_percent": "14.00",
    "vat_enabled": True,
    "vat_inclusive": False,
    "service_percent": "0.00",
    "service_enabled": False,
    "service_applies_to": [],
    "rounding_step": "0.01",
}


@pytest.fixture
def db(tmp_path):
    connection = connect(tmp_path / "conflicts.db")
    database = Database(connection)

    for key, value in FINANCE.items():
        database.upsert_mirror(
            "m_settings",
            {"key": f"finance.{key}", "value": json.dumps(value), "payload": "{}"},
            key="key",
        )
    database.upsert_mirror(
        "m_products",
        {"id": "p1", "name_ar": "كابتشينو", "payload": json.dumps({"is_tax_exempt": False})},
    )
    database.upsert_mirror(
        "m_variants", {"id": "v1", "product_id": "p1", "price": "60.00", "payload": "{}"}
    )

    yield database
    connection.close()


def a_conflict(db, *, code: str = "ORDER_ALREADY_CLOSED") -> str:
    """
    One real queued operation, refused by the server.

    Conflicts the ITEM, not the order-open: a sale queues several operations and
    only some of them fail, which is the situation the screen exists for.
    """
    order = service.open_order(db, settings=service.settings_from_mirror(db))
    service.add_item(db, order.order_id, variant_id="v1")

    operation = next(op for op in outbox.pending(db) if op.entity_type == "order_event")
    outbox.mark_conflict(db, operation.op_uuid, code=code, server_state={"status": "PAID"})
    return operation.op_uuid


def status_of(db, op_uuid: str) -> str:
    return db.one("SELECT status FROM sync_outbox WHERE op_uuid = ?", (op_uuid,))["status"]


class TestSeeingThem:
    def test_an_empty_queue_says_so(self, qtbot, db) -> None:
        dialog = ConflictsDialog(db)
        qtbot.addWidget(dialog)

        assert dialog.conflicts == []
        assert not dialog.empty.isHidden()

    def test_a_conflict_is_listed(self, qtbot, db) -> None:
        a_conflict(db)

        dialog = ConflictsDialog(db)
        qtbot.addWidget(dialog)

        assert len(dialog.conflicts) == 1
        assert dialog.empty.isHidden()

    def test_it_shows_the_message_the_outbox_wrote(self, qtbot, db) -> None:
        """
        Already in words a cashier can act on. Rewording it here would give one
        rule two vocabularies.
        """
        a_conflict(db)

        dialog = ConflictsDialog(db)
        qtbot.addWidget(dialog)

        assert "دفعه من جهاز آخر" in dialog.conflicts[0]["message_ar"]

    def test_an_unknown_code_still_gets_a_sentence(self, qtbot, db) -> None:
        a_conflict(db, code="SOMETHING_NEW")

        dialog = ConflictsDialog(db)
        qtbot.addWidget(dialog)

        assert dialog.conflicts[0]["message_ar"]


class TestResolving:
    def test_retrying_puts_it_back_in_the_queue(self, qtbot, db) -> None:
        op_uuid = a_conflict(db)
        assert status_of(db, op_uuid) == outbox.CONFLICT

        dialog = ConflictsDialog(db)
        qtbot.addWidget(dialog)
        dialog._retry({"op_uuid": op_uuid})

        assert status_of(db, op_uuid) == outbox.PENDING
        assert dialog.conflicts == []

    def test_acknowledging_does_not_retry_it(self, qtbot, db) -> None:
        """
        For the cases a person resolved in the room: the items were re-rung, the
        customer was refunded. The operation is dead, and saying so is how it
        stops nagging.
        """
        op_uuid = a_conflict(db)

        dialog = ConflictsDialog(db)
        qtbot.addWidget(dialog)
        dialog._acknowledge({"op_uuid": op_uuid})

        assert status_of(db, op_uuid) == outbox.CONFLICT, "acknowledged, not requeued"
        assert dialog.conflicts == []

    def test_neither_action_deletes_the_history(self, qtbot, db) -> None:
        """
        "What happened on the 14th" has to stay answerable next month. Deleting
        the row would make the queue tidy and the history a lie.
        """
        op_uuid = a_conflict(db)

        dialog = ConflictsDialog(db)
        qtbot.addWidget(dialog)
        dialog._acknowledge({"op_uuid": op_uuid})

        row = db.one("SELECT status, last_error FROM sync_outbox WHERE op_uuid = ?", (op_uuid,))
        assert row is not None
        assert row["last_error"] == "ORDER_ALREADY_CLOSED"

        seen = db.one("SELECT acknowledged FROM sync_conflicts WHERE op_uuid = ?", (op_uuid,))
        assert seen["acknowledged"] == 1

    def test_it_announces_that_something_changed(self, qtbot, db) -> None:
        """The header carries the count, so it has to hear about this."""
        op_uuid = a_conflict(db)

        dialog = ConflictsDialog(db)
        qtbot.addWidget(dialog)

        heard = []
        dialog.resolved.connect(lambda: heard.append(True))
        dialog._acknowledge({"op_uuid": op_uuid})

        assert heard == [True]

    def test_resolving_one_leaves_the_others(self, qtbot, db) -> None:
        first = a_conflict(db)
        a_conflict(db, code="SEQUENCE_GAP")

        dialog = ConflictsDialog(db)
        qtbot.addWidget(dialog)
        assert len(dialog.conflicts) == 2

        dialog._acknowledge({"op_uuid": first})
        assert len(dialog.conflicts) == 1


def test_a_retried_operation_can_succeed_on_the_next_drain(qtbot, db) -> None:
    """
    The point of "retry": the cases that fix themselves. A missing event that has
    since arrived, a shift the server had not seen yet.
    """
    op_uuid = a_conflict(db, code="SEQUENCE_GAP")

    dialog = ConflictsDialog(db)
    qtbot.addWidget(dialog)
    dialog._retry({"op_uuid": op_uuid})

    queued = [op.op_uuid for op in outbox.pending(db)]
    assert op_uuid in queued


def test_the_money_is_untouched_by_either_action(qtbot, db) -> None:
    """
    Resolving a sync conflict is bookkeeping about a message, not about a sale.
    The local order is exactly as it was.
    """
    op_uuid = a_conflict(db)
    before = service.load(db, db.one("SELECT id FROM l_orders")["id"]).totals.grand_total

    dialog = ConflictsDialog(db)
    qtbot.addWidget(dialog)
    dialog._acknowledge({"op_uuid": op_uuid})

    after = service.load(db, db.one("SELECT id FROM l_orders")["id"]).totals.grand_total
    assert after == before == Decimal("68.40")
