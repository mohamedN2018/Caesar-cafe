r"""
The local printer binding screen.

Its whole job is one fact the server cannot hold: which port a printer is on
THIS machine. Everything else about a printer — its name, its kind, which
stations it serves — is the branch's decision and belongs on the server, so this
dialog deliberately cannot change any of it.

Nothing here calls `exec()`. A modal dialog in a test suite does not fail; it
hangs, and a hung suite is far harder to diagnose than a failing one.
"""

from __future__ import annotations

import json

import pytest
from PySide6.QtWidgets import QLineEdit

from caesar_pos.local.db import Database, connect
from caesar_pos.printing import registry
from caesar_pos.ui.printing import PrinterBindingDialog


@pytest.fixture
def db(tmp_path):
    connection = connect(tmp_path / "binding.db")
    yield Database(connection)
    connection.close()


def add_printer(db, **overrides) -> str:
    row = {
        "id": overrides.pop("id", "pr1"),
        "name_ar": "طابعة الكاشير",
        "code": "CASHIER-1",
        "kind": "RECEIPT",
        "connection": "USB",
        "host": "",
        "port": 9100,
        "device_path": "/dev/from-server",
        "dots": 576,
        "copies": 1,
        "cut_after": 1,
        "is_default": 1,
        "is_active": 1,
        "payload": json.dumps({}),
    }
    row.update(overrides)
    db.upsert_mirror("m_printers", row)
    return row["id"]


class TestTheScreen:
    def test_it_says_so_when_the_branch_has_no_printers(self, qtbot, db) -> None:
        """
        Not an error. A branch that has not set printers up prints on each
        till's local default exactly as it did before, and the screen should say
        that rather than look broken.
        """
        dialog = PrinterBindingDialog(db)
        qtbot.addWidget(dialog)

        assert dialog.empty.isVisibleTo(dialog)
        assert not dialog.printers

    def test_it_lists_the_branch_printers(self, qtbot, db) -> None:
        add_printer(db)
        add_printer(db, id="pr2", code="KIT-1", kind="KITCHEN", is_default=0)

        dialog = PrinterBindingDialog(db)
        qtbot.addWidget(dialog)

        assert {p.code for p in dialog.printers} == {"CASHIER-1", "KIT-1"}
        assert not dialog.empty.isVisibleTo(dialog)


class TestBinding:
    def test_binding_points_this_terminal_at_its_own_port(self, qtbot, db) -> None:
        printer_id = add_printer(db)
        dialog = PrinterBindingDialog(db)
        qtbot.addWidget(dialog)

        dialog._bind(dialog.printers[0], "/dev/on-this-till")

        assert registry.resolve(db, kind="RECEIPT").target == "/dev/on-this-till"
        assert (
            db.scalar("SELECT device_path FROM m_printers WHERE id = ?", (printer_id,), default="")
            == "/dev/from-server"
        ), "the branch's value is untouched — this is a local override, not an edit"

    def test_clearing_the_binding_returns_to_the_branch_default(self, qtbot, db) -> None:
        """
        The common case is a cafe that standardised on one port everywhere. An
        override that could not be undone would strand a terminal on a path
        somebody typed once by mistake.
        """
        add_printer(db)
        dialog = PrinterBindingDialog(db)
        qtbot.addWidget(dialog)

        dialog._bind(dialog.printers[0], "/dev/typo")
        dialog._bind(dialog.printers[0], "")

        assert registry.resolve(db, kind="RECEIPT").target == "/dev/from-server"

    def test_it_signals_so_the_queue_can_be_retried(self, qtbot, db) -> None:
        """
        Somebody opened this because paper was not coming out. Waiting for the
        next timer tick to learn whether they fixed it teaches nothing.
        """
        add_printer(db)
        dialog = PrinterBindingDialog(db)
        qtbot.addWidget(dialog)

        with qtbot.waitSignal(dialog.bound, timeout=1000):
            dialog._bind(dialog.printers[0], "/dev/fixed")

    def test_a_network_printer_cannot_be_rebound_locally(self, qtbot, db) -> None:
        """
        An IP address is the same from every till, so a field here would do
        nothing — and a control that does nothing is worse than no control.
        """
        add_printer(db, connection="NETWORK", host="10.0.0.7", device_path="")
        dialog = PrinterBindingDialog(db)
        qtbot.addWidget(dialog)

        card = dialog.list.itemAt(0).widget()
        field = card.findChild(QLineEdit)

        assert field is not None, "the card still renders — only the field is dead"
        assert not field.isEnabled()
