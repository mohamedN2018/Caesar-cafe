"""
Today's orders, and reprinting one.

"The receipt for the table that just left — can I have it again?" is asked
several times a shift, and until this screen the only order a terminal could
show was the one on the till.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from caesar_pos.local.db import Database, connect
from caesar_pos.orders import service
from caesar_pos.printing import spooler
from caesar_pos.security.session import Session
from caesar_pos.ui.history import HistoryDialog

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
    connection = connect(tmp_path / "history.db")
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
    database.upsert_mirror(
        "m_payment_methods",
        {"id": "m-cash", "code": "CASH", "name_ar": "نقدي", "counts_as_cash": 1, "payload": "{}"},
    )

    yield database
    connection.close()


def cashier(*permissions: str) -> Session:
    from datetime import UTC, datetime

    return Session(
        user_id="u1",
        full_name_ar="منى",
        permissions=frozenset(permissions),
        started_at=datetime.now(UTC),
    )


def a_sale(db, *, paid: bool = True):
    order = service.open_order(db, settings=service.settings_from_mirror(db))
    order = service.add_item(db, order.order_id, variant_id="v1")
    if paid:
        order = service.take_payment(
            db, order.order_id, method_id="m-cash", amount=order.totals.grand_total
        )
    return order


class TestListing:
    def test_an_empty_day_says_so(self, qtbot, db) -> None:
        dialog = HistoryDialog(db, cashier("orders.reprint"))
        qtbot.addWidget(dialog)

        assert dialog.orders == []
        assert not dialog.empty.isHidden()

    def test_todays_orders_are_listed_newest_first(self, qtbot, db) -> None:
        first = a_sale(db)
        second = a_sale(db)

        dialog = HistoryDialog(db, cashier("orders.reprint"))
        qtbot.addWidget(dialog)

        numbers = [o["local_number"] for o in dialog.orders]
        assert numbers[0] == second.local_number
        assert first.local_number in numbers

    def test_it_can_be_searched_by_number(self, qtbot, db) -> None:
        wanted = a_sale(db)
        a_sale(db)

        dialog = HistoryDialog(db, cashier("orders.reprint"))
        qtbot.addWidget(dialog)
        dialog.search.setText(wanted.local_number)

        assert [o["local_number"] for o in dialog.orders] == [wanted.local_number]

    def test_a_search_with_no_match_is_empty_not_everything(self, qtbot, db) -> None:
        """A filter that silently falls back to "all" is worse than no filter."""
        a_sale(db)

        dialog = HistoryDialog(db, cashier("orders.reprint"))
        qtbot.addWidget(dialog)
        dialog.search.setText("MB-99-9999")

        assert dialog.orders == []

    def test_open_and_paid_orders_both_appear(self, qtbot, db) -> None:
        a_sale(db, paid=False)
        a_sale(db, paid=True)

        dialog = HistoryDialog(db, cashier("orders.reprint"))
        qtbot.addWidget(dialog)

        assert {o["status"] for o in dialog.orders} == {"OPEN", "PAID"}


class TestReprinting:
    def test_a_reprint_is_queued(self, qtbot, db) -> None:
        order = a_sale(db)

        dialog = HistoryDialog(db, cashier("orders.reprint"))
        qtbot.addWidget(dialog)
        dialog._reprint(dialog.orders[0])

        jobs = spooler.pending(db)
        assert len(jobs) == 1
        assert order.local_number in "\n".join(jobs[0].lines)

    def test_the_reprint_carries_the_folds_figures(self, qtbot, db) -> None:
        """
        Built through the same builder payment uses. Constructing it from the
        list row would give a second definition of what a receipt is, and the
        reprint would be the one that disagrees.
        """
        order = a_sale(db)

        dialog = HistoryDialog(db, cashier("orders.reprint"))
        qtbot.addWidget(dialog)
        dialog._reprint(dialog.orders[0])

        printed = "\n".join(spooler.pending(db)[0].lines)
        assert str(order.totals.grand_total) in printed

    def test_it_says_what_it_did(self, qtbot, db) -> None:
        """A queued job with no acknowledgement gets tapped three more times."""
        a_sale(db)

        dialog = HistoryDialog(db, cashier("orders.reprint"))
        qtbot.addWidget(dialog)
        dialog._reprint(dialog.orders[0])

        assert "طابور الطباعة" in dialog.message.text()

    def test_a_cashier_without_the_permission_gets_no_button(self, qtbot, db) -> None:
        """
        A second copy of a paid receipt is the paperwork a refund fraud needs,
        so the matrix separates reading one from duplicating it.
        """
        from PySide6.QtWidgets import QPushButton

        a_sale(db, paid=False)

        def buttons_on(dialog) -> list[str]:
            # The row is held in a local until the labels are read: an unparented
            # widget is collected the moment the expression ends, taking its C++
            # children with it.
            row = dialog._row(dialog.orders[0])
            qtbot.addWidget(row)
            return [button.text() for button in row.findChildren(QPushButton)]

        without = HistoryDialog(db, cashier("orders.view"))
        qtbot.addWidget(without)
        labels = buttons_on(without)
        assert "إعادة طباعة" not in labels
        assert "فتح" in labels, "they can still work the order, just not duplicate its receipt"

        with_permission = HistoryDialog(db, cashier("orders.view", "orders.reprint"))
        qtbot.addWidget(with_permission)
        assert "إعادة طباعة" in buttons_on(with_permission)

    def test_reprinting_twice_queues_two_copies(self, qtbot, db) -> None:
        """
        Not deduplicated: the second ask is a real second ask, and silently
        printing nothing would send the cashier to check the printer.
        """
        a_sale(db)

        dialog = HistoryDialog(db, cashier("orders.reprint"))
        qtbot.addWidget(dialog)
        dialog._reprint(dialog.orders[0])
        dialog._reprint(dialog.orders[0])

        assert len(spooler.pending(db)) == 2


class TestReopening:
    def test_an_open_order_can_go_back_on_the_till(self, qtbot, db) -> None:
        order = a_sale(db, paid=False)

        dialog = HistoryDialog(db, cashier("orders.reprint"))
        qtbot.addWidget(dialog)

        asked = []
        dialog.reopen_requested.connect(asked.append)
        dialog._reopen(dialog.orders[0])

        assert asked == [order.order_id]

    def test_a_paid_order_is_reprinted_not_resumed(self, qtbot, db) -> None:
        from PySide6.QtWidgets import QPushButton

        a_sale(db, paid=True)

        dialog = HistoryDialog(db, cashier("orders.reprint"))
        qtbot.addWidget(dialog)
        row = dialog._row(dialog.orders[0])
        qtbot.addWidget(row)

        labels = [b.text() for b in row.findChildren(QPushButton)]
        assert "فتح" not in labels
        assert "إعادة طباعة" in labels


def test_the_money_is_untouched_by_a_reprint(qtbot, db) -> None:
    """Reprinting is a printer instruction, not a financial event."""
    order = a_sale(db)

    dialog = HistoryDialog(db, cashier("orders.reprint"))
    qtbot.addWidget(dialog)
    dialog._reprint(dialog.orders[0])

    after = service.load(db, order.order_id)
    assert after.totals.grand_total == Decimal("68.40")
    assert after.status == "PAID"
