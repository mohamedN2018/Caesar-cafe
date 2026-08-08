"""
The shell — where the pieces become an application.

The assertions here are about the joins, not the parts: that a cook does not get
a till, that a restricted licence still settles the tables it already has, that a
failing printer does not stop the sync timer, and that a tick which raises is
logged rather than fatal. Each of these is a way the whole thing can be wrong
while every individual module is right.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from caesar_pos.api.client import ApiError, NetworkUnavailable
from caesar_pos.local import outbox
from caesar_pos.local.db import Database, connect
from caesar_pos.orders import service
from caesar_pos.printing import receipt, spooler
from caesar_pos.security.session import Session
from caesar_pos.sync.engine import State, SyncEngine
from caesar_pos.ui.shell import TICKET_ACTIONS, Shell, available_boards

NOW = datetime(2026, 8, 8, 14, 0, tzinfo=UTC)

ALL_PERMISSIONS = frozenset(
    {
        "orders.create",
        "orders.discount",
        "payments.take",
        "floor.view",
        "kitchen.view",
        "kitchen.update_status",
        "kids.view",
    }
)


@pytest.fixture
def db(tmp_path):
    connection = connect(tmp_path / "shell.db")
    yield Database(connection)
    connection.close()


#: What the config stream leaves behind. The till refuses to price without it,
#: so a fixture that skipped it would be testing a terminal that cannot sell.
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
def synced_config(db):
    for key, value in FINANCE.items():
        db.upsert_mirror(
            "m_settings",
            {"key": f"finance.{key}", "value": json.dumps(value), "payload": "{}"},
            key="key",
        )


@pytest.fixture
def menu(db, synced_config):
    db.upsert_mirror(
        "m_categories", {"id": "c1", "name_ar": "مشروبات", "sort_order": 1, "payload": "{}"}
    )
    db.upsert_mirror(
        "m_products",
        {
            "id": "p1",
            "category_id": "c1",
            "name_ar": "كابتشينو",
            "payload": json.dumps({"is_tax_exempt": False}),
        },
    )
    db.upsert_mirror(
        "m_variants",
        {"id": "v1", "product_id": "p1", "price": "60.00", "is_default": 1, "payload": "{}"},
    )
    db.upsert_mirror("m_areas", {"id": "a1", "name_ar": "الصالة", "payload": "{}"})
    db.upsert_mirror(
        "m_tables",
        {"id": "t-1", "area_id": "a1", "number": "1", "seats": 4, "payload": "{}"},
    )


def session(*permissions: str, name: str = "أحمد") -> Session:
    return Session(
        user_id="u1",
        full_name_ar=name,
        permissions=frozenset(permissions) if permissions else ALL_PERMISSIONS,
        started_at=NOW,
    )


class FakeClient:
    """Stands in for the API. Records calls; raises what it is told to."""

    def __init__(self, *, raises: Exception | None = None, tickets: list | None = None) -> None:
        self.raises = raises
        self.tickets = tickets or []
        self.posts: list[str] = []
        self.access_token = None

    def get(self, path, **kwargs):
        if self.raises:
            raise self.raises
        return {"results": self.tickets}

    def post(self, path, json=None, **kwargs):
        self.posts.append(path)
        if self.raises:
            raise self.raises
        return {}


@pytest.fixture
def engine(db):
    return SyncEngine(db=db, client=FakeClient())


def make_shell(db, engine, *, user=None, printer=None, **kwargs) -> Shell:
    return Shell(db, user or session(), engine, printer=printer, **kwargs)


# ── who sees what ────────────────────────────────────────────────────────────


class TestBoardsOnOffer:
    def test_a_cashier_gets_the_till_not_the_kitchen(self) -> None:
        assert available_boards(session("orders.create", "payments.take")) == ["pos"]

    def test_a_cook_gets_the_kitchen_not_the_till(self) -> None:
        """
        A cook has no business on the till. The tab is absent rather than
        disabled — a screen full of things that refuse is a screen nobody reads.
        """
        assert available_boards(session("kitchen.view")) == ["kitchen"]

    def test_a_manager_gets_everything(self) -> None:
        assert available_boards(session()) == ["pos", "floor", "kitchen", "kids"]

    def test_a_user_with_nothing_gets_no_boards(self) -> None:
        """Not a crash and not a blank till — simply no tabs to open."""
        assert available_boards(session("reports.sales")) == []

    def test_only_the_permitted_boards_are_constructed(self, qtbot, db, menu, engine) -> None:
        """
        A board that is built but hidden still has a live refresh reading the
        database for a screen nobody can open.
        """
        shell = make_shell(db, engine, user=session("kitchen.view"))
        qtbot.addWidget(shell)

        assert set(shell.boards) == {"kitchen"}
        assert shell.stack.count() == 1

    def test_an_empty_shell_does_not_crash(self, qtbot, db, engine) -> None:
        shell = make_shell(db, engine, user=session("reports.sales"))
        qtbot.addWidget(shell)

        assert shell.boards == {}
        assert shell.current_board == ""

    def test_an_unsynced_terminal_still_opens_its_other_boards(self, qtbot, db, engine) -> None:
        """
        `settings_from_mirror` rightly refuses to guess a VAT rate. That refusal
        must not take the whole application down: the kitchen and the kids area
        work from local state, and the till says what is missing.
        """
        from caesar_pos.ui.shell import NotSyncedBoard

        shell = make_shell(db, engine)  # no `menu` fixture — nothing has pulled
        qtbot.addWidget(shell)

        assert isinstance(shell.boards["pos"], NotSyncedBoard)
        assert set(shell.boards) == {"pos", "floor", "kitchen", "kids"}

    def test_the_unsynced_till_survives_a_refresh_tick(self, qtbot, db, engine) -> None:
        """The timers keep running against it; it must not raise on every beat."""
        shell = make_shell(db, engine)
        qtbot.addWidget(shell)
        shell.show_board("pos")

        shell.tick_boards()
        shell.refresh_status()


class TestNavigation:
    def test_the_first_board_is_open_on_arrival(self, qtbot, db, menu, engine) -> None:
        shell = make_shell(db, engine)
        qtbot.addWidget(shell)

        assert shell.current_board == "pos"

    def test_a_tab_switches_board(self, qtbot, db, menu, engine) -> None:
        shell = make_shell(db, engine)
        qtbot.addWidget(shell)
        shell.tabs["floor"].click()

        assert shell.current_board == "floor"

    def test_the_open_tab_is_marked(self, qtbot, db, menu, engine) -> None:
        shell = make_shell(db, engine)
        qtbot.addWidget(shell)
        shell.show_board("kids")

        assert shell.tabs["kids"].objectName() == "TabActive"
        assert shell.tabs["pos"].objectName() == "Tab"

    def test_tapping_an_occupied_table_lands_on_its_order(self, qtbot, db, menu, engine) -> None:
        """The point of the floor map: a table is a route to its bill."""
        settings = service.settings_from_mirror(db)
        order = service.open_order(db, settings=settings, table_id="t-1")
        service.add_item(db, order.order_id, variant_id="v1")

        shell = make_shell(db, engine)
        qtbot.addWidget(shell)
        shell.show_board("floor")
        shell.boards["floor"].room.table_clicked.emit("t-1")

        assert shell.current_board == "pos"
        assert shell.boards["pos"].order_id == order.order_id

    def test_tapping_a_free_table_seats_it(self, qtbot, db, menu, engine) -> None:
        shell = make_shell(db, engine)
        qtbot.addWidget(shell)
        shell._open_table("t-1", None)

        assert shell.boards["pos"].order_id is not None
        assert service.load(db, shell.boards["pos"].order_id).table_id == "t-1"


# ── the licence still applies after login ────────────────────────────────────


class TestRestrictedLicence:
    def test_a_restricted_terminal_will_not_seat_a_new_table(
        self, qtbot, db, menu, engine, monkeypatch
    ) -> None:
        """
        C5: RESTRICTED opens and settles, but starts nothing new. Getting this
        wrong in the shell would make the whole gate decorative.
        """
        warned = []
        monkeypatch.setattr(
            "caesar_pos.ui.shell.QMessageBox.warning",
            lambda *a, **k: warned.append(a[2]),
        )

        shell = make_shell(db, engine, can_open_new_orders=False)
        qtbot.addWidget(shell)
        shell._open_table("t-1", None)

        assert warned, "the refusal has to be said out loud"
        assert shell.boards["pos"].order_id is None

    def test_a_restricted_terminal_still_settles_an_open_table(
        self, qtbot, db, menu, engine
    ) -> None:
        """
        The important half. A cafe with money on its tables and no way to take it
        is a worse outcome than an expired subscription.
        """
        settings = service.settings_from_mirror(db)
        order = service.open_order(db, settings=settings, table_id="t-1")
        order = service.add_item(db, order.order_id, variant_id="v1")

        shell = make_shell(db, engine, can_open_new_orders=False)
        qtbot.addWidget(shell)
        shell._open_table("t-1", order.order_id)

        assert shell.boards["pos"].order_id == order.order_id

        settled = service.take_payment(
            db, order.order_id, method_id="m-cash", amount=order.totals.grand_total
        )
        assert settled.is_settled

    def test_the_restriction_reaches_the_till_itself(self, qtbot, db, menu, engine) -> None:
        shell = make_shell(db, engine, can_open_new_orders=False)
        qtbot.addWidget(shell)

        assert shell.boards["pos"].can_open_new_orders is False


# ── the kitchen board's one online dependency ────────────────────────────────


class TestKitchenTransitions:
    def test_every_board_state_has_an_api_verb(self) -> None:
        """
        The board thinks in states, the API takes verbs. A state with no verb
        would send a guessed URL and get a 404 that looks like a network fault.
        """
        from caesar_pos.ui.kitchen.window import NEXT_ACTION

        targets = {target for _, target in NEXT_ACTION.values()}
        assert targets <= set(TICKET_ACTIONS)

    def test_advancing_a_ticket_calls_its_verb_not_its_state(self, qtbot, db, menu, engine) -> None:
        shell = make_shell(db, engine)
        qtbot.addWidget(shell)
        shell._advance_ticket("k-1", "PREPARING")

        assert engine.client.posts == ["/kitchen/tickets/k-1/start/"]

    def test_an_unknown_state_is_not_sent_as_a_guess(self, qtbot, db, menu, engine) -> None:
        shell = make_shell(db, engine)
        qtbot.addWidget(shell)
        shell._advance_ticket("k-1", "TELEPORTED")

        assert engine.client.posts == []

    def test_offline_the_tap_is_refused_out_loud(self, qtbot, db, menu, monkeypatch) -> None:
        """
        Ticket state is NOT queued offline: two cooks advancing the same ticket
        while disconnected produce a conflict with no sensible resolution.
        """
        warned = []
        monkeypatch.setattr(
            "caesar_pos.ui.shell.QMessageBox.warning", lambda *a, **k: warned.append(a[1])
        )

        engine = SyncEngine(db=db, client=FakeClient(raises=NetworkUnavailable()))
        shell = make_shell(db, engine)
        qtbot.addWidget(shell)
        shell._advance_ticket("k-1", "READY")

        assert warned == ["غير متصل"]

    def test_a_cook_who_may_not_advance_gets_no_button(self, qtbot, db, engine) -> None:
        shell = make_shell(db, engine, user=session("kitchen.view"))
        qtbot.addWidget(shell)

        assert shell.boards["kitchen"].can_advance is False

    def test_an_offline_kitchen_board_keeps_what_it_has(self, qtbot, db) -> None:
        """Blanking the board would lose tickets a cook is still working from."""
        engine = SyncEngine(db=db, client=FakeClient(tickets=[]))
        shell = make_shell(db, engine, user=session("kitchen.view"))
        qtbot.addWidget(shell)

        board = shell.boards["kitchen"]
        board.show_tickets(
            [
                {
                    "id": "k-1",
                    "ticket_number": 7,
                    "status": "NEW",
                    "created_at": NOW.isoformat(),
                    "lines": [],
                }
            ]
        )

        engine.client.raises = ApiError("SERVER_ERROR", "خطأ")
        shell._load_tickets(board)

        assert len(board.tickets) == 1
        assert "دوري" in board.connection.text()


# ── the timers ───────────────────────────────────────────────────────────────


class TestTimers:
    def test_three_timers_run_at_three_cadences(self, qtbot, db, menu, engine) -> None:
        """
        Coupling them means the customer waits for the network: a busy sync would
        delay a receipt, and a jammed printer would delay the outbox.
        """
        shell = make_shell(db, engine)
        qtbot.addWidget(shell)

        intervals = {
            shell.sync_timer.interval(),
            shell.print_timer.interval(),
            shell.board_timer.interval(),
        }
        assert len(intervals) == 3
        assert all(
            timer.isActive() for timer in (shell.sync_timer, shell.print_timer, shell.board_timer)
        )

    def test_a_failing_sync_tick_does_not_stop_the_till(
        self, qtbot, db, menu, engine, caplog
    ) -> None:
        """A POS that dies because one poll failed is worse than a stale one."""
        shell = make_shell(db, engine)
        qtbot.addWidget(shell)

        def explode(**kwargs):
            raise RuntimeError("the network card melted")

        engine.tick = explode
        shell.tick_sync()  # must not raise

        assert shell.sync_timer.isActive()
        assert "Sync tick failed" in caplog.text

    def test_a_failing_printer_does_not_stop_the_sync(self, qtbot, db, menu, engine) -> None:
        class Exploding:
            def print_document(self, lines, printer_name=""):
                raise OSError("no paper")

        shell = make_shell(db, engine, printer=Exploding())
        qtbot.addWidget(shell)

        order = service.open_order(db, settings=service.settings_from_mirror(db))
        order = service.add_item(db, order.order_id, variant_id="v1")
        spooler.enqueue(db, receipt.build(order))

        shell.tick_print()  # must not raise

        assert shell.sync_timer.isActive()
        assert spooler.counts(db)["pending"] == 1, "the job is kept, not lost"

    def test_printing_drains_when_the_printer_works(self, qtbot, db, menu, engine) -> None:
        class Working:
            def __init__(self):
                self.printed = []

            def print_document(self, lines, printer_name=""):
                self.printed.append(lines)

        printer = Working()
        shell = make_shell(db, engine, printer=printer)
        qtbot.addWidget(shell)

        order = service.open_order(db, settings=service.settings_from_mirror(db))
        order = service.add_item(db, order.order_id, variant_id="v1")
        spooler.enqueue(db, receipt.build(order))

        shell.tick_print()

        assert printer.printed
        assert spooler.counts(db)["pending"] == 0

    def test_only_the_open_board_is_refreshed(self, qtbot, db, menu, engine) -> None:
        """
        Rebuilding four boards on a timer is work nobody sees, and on the hardware
        these run on it shows as jitter on the one board that is open.
        """
        shell = make_shell(db, engine)
        qtbot.addWidget(shell)
        shell.show_board("kids")

        refreshed = []
        shell.boards["kids"].refresh = lambda *a, **k: refreshed.append("kids")
        shell.boards["floor"].refresh = lambda *a, **k: refreshed.append("floor")
        shell.tick_boards()

        assert refreshed == ["kids"]

    def test_closing_stops_every_timer(self, qtbot, db, menu, engine) -> None:
        """A timer firing against a torn-down widget is a crash on shutdown."""
        shell = make_shell(db, engine)
        qtbot.addWidget(shell)
        shell.close()

        assert not shell.sync_timer.isActive()
        assert not shell.print_timer.isActive()
        assert not shell.board_timer.isActive()


# ── the header ───────────────────────────────────────────────────────────────


class TestHeader:
    def test_it_names_who_is_on_the_till(self, qtbot, db, menu, engine) -> None:
        shell = make_shell(db, engine, user=session(name="منى"))
        qtbot.addWidget(shell)

        assert "منى" in shell.findChild(type(shell.sync_label), "ShellUser").text()

    def test_it_shows_the_sync_state(self, qtbot, db, menu, engine) -> None:
        shell = make_shell(db, engine)
        qtbot.addWidget(shell)

        assert shell.engine.status().state == State.ONLINE
        assert "متصل" in shell.sync_label.text()

    def test_a_queued_terminal_says_so(self, qtbot, db, menu, engine) -> None:
        """A terminal queueing since Tuesday must say so on every screen."""
        service.open_order(db, settings=service.settings_from_mirror(db))
        engine.online = False

        shell = make_shell(db, engine)
        qtbot.addWidget(shell)

        assert "غير متصل" in shell.sync_label.text()

    def test_the_print_backlog_is_hidden_when_there_is_none(self, qtbot, db, menu, engine) -> None:
        """A permanent "printer OK" is a label staff stop reading."""
        shell = make_shell(db, engine)
        qtbot.addWidget(shell)

        assert shell.print_label.isHidden()

    def test_the_print_backlog_appears_when_there_is_one(self, qtbot, db, menu, engine) -> None:
        order = service.open_order(db, settings=service.settings_from_mirror(db))
        order = service.add_item(db, order.order_id, variant_id="v1")
        spooler.enqueue(db, receipt.build(order))

        shell = make_shell(db, engine)
        qtbot.addWidget(shell)

        assert not shell.print_label.isHidden()
        assert "1" in shell.print_label.text()

    def test_the_till_carries_the_same_sync_text(self, qtbot, db, menu, engine) -> None:
        """Two headers disagreeing about the link is worse than one being wrong."""
        shell = make_shell(db, engine)
        qtbot.addWidget(shell)

        assert shell.boards["pos"].sync_label.text() == shell.sync_label.text()

    def test_logout_is_one_signal_from_anywhere(self, qtbot, db, menu, engine) -> None:
        shell = make_shell(db, engine)
        qtbot.addWidget(shell)

        seen = []
        shell.logout_requested.connect(lambda: seen.append(True))
        # The till has its own logout button for standalone use; inside the shell
        # it must not become a second, different way out.
        shell.boards["pos"].logout_requested.emit()

        assert seen == [True]


class TestTheDrawer:
    def test_the_header_says_there_is_no_shift(self, qtbot, db, menu, engine) -> None:
        """
        Selling into no shift produces sales that reconcile against nothing, and
        the cashier finds out at close — the one moment it cannot be fixed.
        """
        shell = make_shell(db, engine)
        qtbot.addWidget(shell)

        assert "لا توجد وردية" in shell.shift_label.text()
        assert shell.shift_button.objectName() == "ShiftNeeded"

    def test_opening_a_shift_updates_the_header_and_the_till(self, qtbot, db, menu, engine) -> None:
        shell = make_shell(db, engine)
        qtbot.addWidget(shell)
        shell._do_open_shift(Decimal("500.00"))

        assert "500.00" in shell.shift_label.text()
        assert shell.shift_button.objectName() == "Shift"
        assert shell.boards["pos"].shift_id == shell.shift["id"]

    def test_an_order_opened_after_that_carries_the_shift(self, qtbot, db, menu, engine) -> None:
        shell = make_shell(db, engine)
        qtbot.addWidget(shell)
        shell._do_open_shift(Decimal("500.00"))

        shell.boards["pos"].new_order()
        order_id = shell.boards["pos"].order_id

        # The projection row and the queued operation must agree: the first is
        # what the local Z-report reads, the second is what the server's does.
        row = db.one("SELECT shift_id FROM l_orders WHERE id = ?", (order_id,))
        assert row["shift_id"] == shell.shift["id"]

        operation = next(
            op
            for op in outbox.pending(db)
            if op.entity_type == "order_open" and op.entity_id == order_id
        )
        assert operation.payload["shift_id"] == shell.shift["id"]

    def test_closing_clears_it_again(self, qtbot, db, menu, engine, monkeypatch) -> None:
        monkeypatch.setattr("caesar_pos.ui.shell.QMessageBox.information", lambda *a, **k: None)

        shell = make_shell(db, engine)
        qtbot.addWidget(shell)
        shell._do_open_shift(Decimal("500.00"))
        shell._do_close_shift(Decimal("500.00"), "")

        assert shell.shift is None
        assert shell.boards["pos"].shift_id is None
        assert "لا توجد وردية" in shell.shift_label.text()

    def test_a_second_open_is_refused_out_loud(self, qtbot, db, menu, engine, monkeypatch) -> None:
        warned = []
        monkeypatch.setattr(
            "caesar_pos.ui.shell.QMessageBox.warning", lambda *a, **k: warned.append(a[1])
        )

        shell = make_shell(db, engine)
        qtbot.addWidget(shell)
        shell._do_open_shift(Decimal("500.00"))
        shell._do_open_shift(Decimal("300.00"))

        assert warned == ["تعذّر فتح الوردية"]
        assert Decimal(shell.shift["opening_cash"]) == Decimal("500.00")

    def test_a_cash_movement_without_a_shift_is_refused(
        self, qtbot, db, menu, engine, monkeypatch
    ) -> None:
        warned = []
        monkeypatch.setattr(
            "caesar_pos.ui.shell.QMessageBox.warning", lambda *a, **k: warned.append(a[1])
        )

        shell = make_shell(db, engine)
        qtbot.addWidget(shell)
        shell.cash_movement()

        assert warned == ["لا توجد وردية"]


class TestTheHeaderIsReachable:
    """
    Every control in the header has to have a way to be pressed. A method with
    no caller is a feature that shipped and cannot be used — the cash-movement
    dialog was exactly that until this batch.
    """

    def test_the_cash_movement_button_appears_with_a_shift(self, qtbot, db, menu, engine) -> None:
        shell = make_shell(db, engine)
        qtbot.addWidget(shell)
        shell.show()

        assert shell.movement_button.isHidden(), "nothing to move into without a drawer"

        shell._do_open_shift(Decimal("500.00"))
        assert not shell.movement_button.isHidden()
        assert not shell.xreport_button.isHidden()

    def test_the_x_report_reads_without_closing(self, qtbot, db, menu, engine) -> None:
        """
        "How are we doing?" at eight in the evening used to be answerable only
        by ending the shift.
        """
        from caesar_pos.shifts import service as shifts

        shell = make_shell(db, engine)
        qtbot.addWidget(shell)
        shell._do_open_shift(Decimal("500.00"))

        report = shifts.z_report(db, shell.shift["id"])
        assert report.expected_cash == Decimal("500.00")
        assert shell.shift is not None, "reading it must not end it"

    def test_the_x_report_without_a_shift_is_refused(
        self, qtbot, db, menu, engine, monkeypatch
    ) -> None:
        warned = []
        monkeypatch.setattr(
            "caesar_pos.ui.shell.QMessageBox.warning", lambda *a, **k: warned.append(a[1])
        )

        shell = make_shell(db, engine)
        qtbot.addWidget(shell)
        shell.x_report()

        assert warned == ["لا توجد وردية"]

    def test_the_conflicts_button_is_hidden_when_there_are_none(
        self, qtbot, db, menu, engine
    ) -> None:
        shell = make_shell(db, engine)
        qtbot.addWidget(shell)
        shell.show()

        assert shell.conflicts_button.isHidden()

    def test_a_conflict_surfaces_a_button_that_names_the_count(
        self, qtbot, db, menu, engine
    ) -> None:
        """
        Until this existed the header could say "⚠️ تعارض (٢)" with no way to
        find out which two.
        """
        order = service.open_order(db, settings=service.settings_from_mirror(db))
        service.add_item(db, order.order_id, variant_id="v1")
        operation = outbox.pending(db)[0]
        outbox.mark_conflict(db, operation.op_uuid, code="ORDER_ALREADY_CLOSED", server_state={})

        shell = make_shell(db, engine)
        qtbot.addWidget(shell)
        shell.show()

        assert not shell.conflicts_button.isHidden()
        assert "1" in shell.conflicts_button.text()

    def test_resolving_a_conflict_clears_the_button(self, qtbot, db, menu, engine) -> None:
        order = service.open_order(db, settings=service.settings_from_mirror(db))
        service.add_item(db, order.order_id, variant_id="v1")
        operation = outbox.pending(db)[0]
        outbox.mark_conflict(db, operation.op_uuid, code="SEQUENCE_GAP", server_state={})

        shell = make_shell(db, engine)
        qtbot.addWidget(shell)
        shell.show()

        outbox.acknowledge(db, operation.op_uuid)
        shell.refresh_status()

        assert shell.conflicts_button.isHidden()


class TestKidsWiring:
    def test_a_checkout_reaches_the_server(self, qtbot, db, menu, engine, monkeypatch) -> None:
        monkeypatch.setattr("caesar_pos.ui.shell.QMessageBox.information", lambda *a, **k: None)

        shell = make_shell(db, engine)
        qtbot.addWidget(shell)
        shell._checkout_child("s-1")

        assert engine.client.posts == ["/kids/sessions/s-1/check-out/"]

    def test_offline_the_checkout_says_so(self, qtbot, db, menu, monkeypatch) -> None:
        """The charge is computed on the server, so this one genuinely waits."""
        warned = []
        monkeypatch.setattr(
            "caesar_pos.ui.shell.QMessageBox.warning", lambda *a, **k: warned.append(a[1])
        )

        engine = SyncEngine(db=db, client=FakeClient(raises=NetworkUnavailable()))
        shell = make_shell(db, engine)
        qtbot.addWidget(shell)
        shell._checkout_child("s-1")

        assert warned == ["غير متصل"]

    def test_check_in_is_not_done_offline(self, qtbot, db, menu, engine, monkeypatch) -> None:
        """
        Capacity is a safety limit. Two terminals admitting the last place while
        disconnected would put a child in a room that is already full.
        """
        told = []
        monkeypatch.setattr(
            "caesar_pos.ui.shell.QMessageBox.information", lambda *a, **k: told.append(a[1])
        )

        shell = make_shell(db, engine)
        qtbot.addWidget(shell)
        shell._checkin_child()

        assert told == ["دخول جديد"]
        assert engine.client.posts == []


def test_a_paid_order_leaves_the_floor_map(qtbot, db, menu, engine) -> None:
    """The join between the till and the map, end to end."""
    settings = service.settings_from_mirror(db)
    order = service.open_order(db, settings=settings, table_id="t-1")
    order = service.add_item(db, order.order_id, variant_id="v1")

    shell = make_shell(db, engine)
    qtbot.addWidget(shell)
    shell.show_board("floor")
    assert shell.boards["floor"].occupied_count == 1

    service.take_payment(db, order.order_id, method_id="m-cash", amount=order.totals.grand_total)
    shell.tick_boards()

    assert shell.boards["floor"].occupied_count == 0


def test_the_till_never_recomputes_what_the_receipt_prints(qtbot, db, menu, engine) -> None:
    """One number, from the fold, all the way to the paper."""
    order = service.open_order(db, settings=service.settings_from_mirror(db))
    order = service.add_item(db, order.order_id, variant_id="v1", quantity=Decimal("3"))

    document = receipt.build(order)

    assert document.meta["grand_total"] == str(order.totals.grand_total)
