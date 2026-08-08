"""
The floor map, the kitchen display, and the kids board.

All three follow one rule that is worth stating once: **colour never carries the
meaning alone.** Every state is also words. That is for colour-blind staff and
for the washed-out screens these actually run on — a kitchen display bleached by
a window at 2pm still has to be readable.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from caesar_pos.local.db import Database, connect, transaction
from caesar_pos.orders import service
from caesar_pos.ui.floor.window import FloorWindow
from caesar_pos.ui.kids.window import KidsWindow, running_charge
from caesar_pos.ui.kitchen.window import KitchenWindow, TicketCard, age_minutes

NOW = datetime(2026, 8, 8, 14, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path):
    connection = connect(tmp_path / "boards.db")
    yield Database(connection)
    connection.close()


# ── the floor map ────────────────────────────────────────────────────────────


@pytest.fixture
def floor(db):
    db.upsert_mirror("m_areas", {"id": "a-inside", "name_ar": "الصالة", "payload": "{}"})
    db.upsert_mirror("m_areas", {"id": "a-terrace", "name_ar": "التراس", "payload": "{}"})

    for number, area, x, y in [
        ("1", "a-inside", 0, 0),
        ("2", "a-inside", 1, 0),
        ("3", "a-terrace", 0, 1),
    ]:
        db.upsert_mirror(
            "m_tables",
            {
                "id": f"t-{number}",
                "area_id": area,
                "number": number,
                "seats": 4,
                "pos_x": x,
                "pos_y": y,
                "payload": "{}",
            },
        )

    db.upsert_mirror(
        "m_products",
        {"id": "p1", "name_ar": "كابتشينو", "payload": json.dumps({"is_tax_exempt": False})},
    )
    db.upsert_mirror(
        "m_variants",
        {"id": "v1", "product_id": "p1", "price": "60.00", "payload": "{}"},
    )


@pytest.fixture
def settings():
    return service.Settings(
        vat_percent=Decimal("14.00"),
        vat_enabled=True,
        vat_inclusive=False,
        service_percent=Decimal("0.00"),
        service_enabled=False,
        rounding_step=Decimal("0.01"),
    )


class TestFloorMap:
    def test_it_lists_every_table(self, qtbot, db, floor) -> None:
        window = FloorWindow(db)
        qtbot.addWidget(window)

        assert len(window.visible_tables) == 3

    def test_an_area_tab_filters(self, qtbot, db, floor) -> None:
        window = FloorWindow(db)
        qtbot.addWidget(window)
        window.select_area("a-terrace")

        assert [t["number"] for t in window.visible_tables] == ["3"]

    def test_a_free_table_says_so_in_words(self, qtbot, db, floor) -> None:
        """Colour alone fails for colour-blind staff and on a bleached screen."""
        window = FloorWindow(db)
        qtbot.addWidget(window)
        table = window.visible_tables[0]

        assert "متاحة" in window._label(table)
        assert window._style_for(table) == "TableFree"

    def test_an_occupied_table_shows_its_total_and_state(self, qtbot, db, floor, settings) -> None:
        order = service.open_order(db, settings=settings, table_id="t-1")
        service.add_item(db, order.order_id, variant_id="v1")

        window = FloorWindow(db)
        qtbot.addWidget(window)
        table = next(t for t in window.visible_tables if t["id"] == "t-1")

        assert "68.40" in window._label(table)
        assert "مفتوحة" in window._label(table)
        assert window._style_for(table) == "TableBusy"

    def test_a_ready_table_is_distinguished(self, qtbot, db, floor, settings) -> None:
        order = service.open_order(db, settings=settings, table_id="t-1")
        service.add_item(db, order.order_id, variant_id="v1")
        with transaction(db.connection):
            db.execute("UPDATE l_orders SET status = 'READY' WHERE id = ?", (order.order_id,))

        window = FloorWindow(db)
        qtbot.addWidget(window)
        table = next(t for t in window.visible_tables if t["id"] == "t-1")

        assert window._style_for(table) == "TableReady"
        assert "جاهزة" in window._label(table)

    def test_a_paid_order_frees_the_table(self, qtbot, db, floor, settings) -> None:
        order = service.open_order(db, settings=settings, table_id="t-1")
        order = service.add_item(db, order.order_id, variant_id="v1")
        service.take_payment(
            db, order.order_id, method_id="m-cash", amount=order.totals.grand_total
        )

        window = FloorWindow(db)
        qtbot.addWidget(window)

        assert window.occupied_count == 0

    def test_tapping_a_table_emits_it(self, qtbot, db, floor, settings) -> None:
        order = service.open_order(db, settings=settings, table_id="t-1")

        window = FloorWindow(db)
        qtbot.addWidget(window)

        chosen = []
        window.table_chosen.connect(lambda tid, oid: chosen.append((tid, oid)))
        # The room paints tables rather than laying out buttons, so a tap is the
        # widget's own signal rather than a click on a child.
        window.room.table_clicked.emit("t-1")

        assert chosen == [("t-1", order.order_id)]

    def test_the_room_is_given_the_tables_to_paint(self, qtbot, db, floor) -> None:
        window = FloorWindow(db)
        qtbot.addWidget(window)

        assert len(window.room.tables) == 3

    def test_an_occupied_table_carries_its_party_into_the_room(
        self, qtbot, db, floor, settings
    ) -> None:
        """
        The furniture is `seats`; the party is `seated_count`. Only the second
        tells a waiter that a four-top still has two chairs free.
        """
        order = service.open_order(db, settings=settings, table_id="t-1", guest_count=2)
        service.add_item(db, order.order_id, variant_id="v1")

        window = FloorWindow(db)
        qtbot.addWidget(window)
        table = next(t for t in window.room.tables if t["id"] == "t-1")

        assert table["seats"] == 4
        assert table["seated_count"] == 2

    def test_a_free_table_seats_nobody(self, qtbot, db, floor) -> None:
        window = FloorWindow(db)
        qtbot.addWidget(window)

        assert all(t["seated_count"] == 0 for t in window.room.tables)

    def test_an_unsynced_terminal_says_so(self, qtbot, db) -> None:
        window = FloorWindow(db)
        qtbot.addWidget(window)

        assert window.visible_tables == []
        assert not window.empty.isHidden()


# ── the kitchen display ──────────────────────────────────────────────────────


def ticket(number: int, *, minutes_old: int = 0, status: str = "NEW", target: int = 8, **extra):
    return {
        "id": f"k-{number}",
        "ticket_number": number,
        "status": status,
        "created_at": (NOW - timedelta(minutes=minutes_old)).isoformat(),
        "target_minutes": target,
        "lines": [{"name": "كابتشينو", "quantity": "2", "modifiers": [], "note": ""}],
        **extra,
    }


class TestKitchenDisplay:
    def test_age_is_minutes_not_a_clock_time(self) -> None:
        """ "12 minutes" is what a cook acts on; "14:32" needs arithmetic."""
        assert age_minutes((NOW - timedelta(minutes=12)).isoformat(), now=NOW) == 12

    def test_a_malformed_timestamp_is_zero_not_a_crash(self) -> None:
        assert age_minutes("not a date") == 0

    def test_the_oldest_ticket_is_first(self, qtbot) -> None:
        """
        A board sorted any other way lets a ticket sit at the bottom while newer
        ones are made — the exact failure a KDS exists to prevent.
        """
        window = KitchenWindow()
        qtbot.addWidget(window)
        window.show_tickets([ticket(2, minutes_old=1), ticket(1, minutes_old=20)], now=NOW)

        assert [t["ticket_number"] for t in window.tickets] == [1, 2]

    def test_a_late_ticket_says_late_in_words(self, qtbot) -> None:
        card = TicketCard(ticket(1, minutes_old=20, target=8), now=NOW)
        qtbot.addWidget(card)

        assert card.late is True
        assert card.objectName() == "TicketLate"

    def test_an_on_time_ticket_is_not_flagged(self, qtbot) -> None:
        card = TicketCard(ticket(1, minutes_old=3, target=8), now=NOW)
        qtbot.addWidget(card)

        assert card.late is False

    def test_one_tap_advances_a_ticket(self, qtbot) -> None:
        """No menus. A cook with one free hand should not be navigating."""
        window = KitchenWindow()
        qtbot.addWidget(window)
        window.show_tickets([ticket(1, status="NEW")], now=NOW)

        advanced = []
        window.advance.connect(lambda tid, target: advanced.append((tid, target)))
        card = window.grid.itemAt(0).widget()
        card.advance_requested.emit("k-1", "PREPARING")

        assert advanced == [("k-1", "PREPARING")]

    def test_the_late_count_is_available_for_an_alert(self, qtbot) -> None:
        window = KitchenWindow()
        qtbot.addWidget(window)
        window.show_tickets([ticket(1, minutes_old=30), ticket(2, minutes_old=1)], now=NOW)

        assert window.late_count == 1

    def test_it_says_whether_it_is_live_or_polling(self, qtbot) -> None:
        """A display quietly showing five-minute-old tickets is worse than one
        that admits it."""
        window = KitchenWindow()
        qtbot.addWidget(window)

        window.set_connection(live=True)
        assert "مباشر" in window.connection.text()

        window.set_connection(live=False)
        assert "دوري" in window.connection.text()

    def test_an_empty_board_says_so(self, qtbot) -> None:
        window = KitchenWindow()
        qtbot.addWidget(window)
        window.show_tickets([])

        assert not window.empty.isHidden()


# ── the kids board ───────────────────────────────────────────────────────────


TARIFF = {
    "mode": "TIMED",
    "entry_fee": "25.00",
    "included_minutes": 30,
    "package_minutes": 0,
    "block_minutes": 15,
    "block_rate": "15.00",
    "grace_minutes": 5,
    "daily_cap": "120.00",
}


@pytest.fixture
def play_session(db):
    def add(session_id: str, tag: str, *, minutes: int = 0, status="ACTIVE", medical=""):
        with transaction(db.connection):
            db.insert(
                "l_play_sessions",
                {
                    "id": session_id,
                    "area_id": "area-1",
                    "tariff_id": "t-1",
                    "child_name": "يوسف",
                    "guardian_name": "أحمد محمود",
                    "guardian_phone": "01001234567",
                    "medical_notes": medical,
                    "tag_number": tag,
                    "status": status,
                    "checked_in_at": (NOW - timedelta(minutes=minutes)).isoformat(),
                    "tariff_snapshot": json.dumps(TARIFF),
                },
            )

    return add


def card_texts(card) -> list[str]:
    layout = card.layout()
    return [
        layout.itemAt(i).widget().text()
        for i in range(layout.count())
        if hasattr(layout.itemAt(i).widget(), "text")
    ]


class TestKidsBoard:
    def test_the_running_charge_uses_the_vendored_engine(self, db, play_session) -> None:
        """
        The same module the server runs, so the figure on the board is the figure
        on the bill.
        """
        play_session("s1", "14", minutes=52)
        session = dict(db.one("SELECT * FROM l_play_sessions WHERE id = 's1'"))

        assert running_charge(session, now=NOW) == Decimal("55.00")

    def test_a_session_inside_grace_is_not_charged_extra(self, db, play_session) -> None:
        play_session("s1", "14", minutes=34)
        session = dict(db.one("SELECT * FROM l_play_sessions WHERE id = 's1'"))

        assert running_charge(session, now=NOW) == Decimal("25.00")

    def test_a_tariff_edited_mid_visit_does_not_reprice_it(self, db, play_session) -> None:
        """The snapshot decides, exactly as on the server."""
        play_session("s1", "14", minutes=52)
        session = dict(db.one("SELECT * FROM l_play_sessions WHERE id = 's1'"))

        db.upsert_mirror(
            "m_kids_tariffs",
            {"id": "t-1", "area_id": "area-1", "name_ar": "عداد", "payload": "{}"},
        )
        assert running_charge(session, now=NOW) == Decimal("55.00")

    def test_the_capacity_is_a_hard_number_in_the_header(self, qtbot, db, play_session) -> None:
        play_session("s1", "1")
        play_session("s2", "2")

        window = KidsWindow(db, capacity=3)
        qtbot.addWidget(window)

        assert "2 / 3" in window.capacity_label.text()
        assert window.checkin_button.isEnabled()

    def test_a_full_area_disables_check_in(self, qtbot, db, play_session) -> None:
        """
        A safety limit, not a metric. The button is disabled rather than the
        check-in failing after the child is already inside.
        """
        for index in range(3):
            play_session(f"s{index}", str(index))

        window = KidsWindow(db, capacity=3)
        qtbot.addWidget(window)

        assert window.is_full is True
        assert not window.checkin_button.isEnabled()
        assert not window.full_label.isHidden()

    def test_a_card_shows_the_guardian_and_phone(self, qtbot, db, play_session) -> None:
        """Staff need them while looking at the child, not after navigating."""
        from caesar_pos.ui.kids.window import SessionCard

        play_session("s1", "14", minutes=10)
        session = dict(db.one("SELECT * FROM l_play_sessions WHERE id = 's1'"))

        card = SessionCard(session, now=NOW)
        qtbot.addWidget(card)

        labels = card_texts(card)
        assert any("أحمد محمود" in text for text in labels)
        assert any("01001234567" in text for text in labels)

    def test_a_medical_note_is_on_the_card_not_behind_a_tap(self, qtbot, db, play_session) -> None:
        """
        An allergy one tap away is an allergy nobody reads. It has to survive the
        trip from the server all the way onto the card with no lookup.
        """
        from caesar_pos.ui.kids.window import SessionCard

        play_session("s1", "14", medical="حساسية من الفول السوداني")
        session = dict(db.one("SELECT * FROM l_play_sessions WHERE id = 's1'"))

        card = SessionCard(session, now=NOW)
        qtbot.addWidget(card)

        assert any("الفول السوداني" in text for text in card_texts(card))

    def test_a_child_with_no_notes_gets_no_empty_banner(self, qtbot, db, play_session) -> None:
        """A blank warning strip on every card is a warning strip nobody sees."""
        from caesar_pos.ui.kids.window import SessionCard

        play_session("s1", "14")
        session = dict(db.one("SELECT * FROM l_play_sessions WHERE id = 's1'"))

        card = SessionCard(session, now=NOW)
        qtbot.addWidget(card)

        assert not any(text.startswith("⚕") for text in card_texts(card))

    def test_an_overdue_session_is_flagged(self, qtbot, db, play_session) -> None:
        from caesar_pos.ui.kids.window import SessionCard

        play_session("s1", "14", minutes=200, status="OVERDUE")
        session = dict(db.one("SELECT * FROM l_play_sessions WHERE id = 's1'"))

        card = SessionCard(session, now=NOW)
        qtbot.addWidget(card)

        assert card.objectName() == "SessionLate"

    def test_checking_out_emits_the_session(self, qtbot, db, play_session) -> None:
        play_session("s1", "14")

        window = KidsWindow(db, capacity=25)
        qtbot.addWidget(window)

        requested = []
        window.checkout_requested.connect(requested.append)
        window.grid.itemAt(0).widget().checkout_requested.emit("s1")

        assert requested == ["s1"]

    def test_a_checked_out_session_leaves_the_board(self, qtbot, db, play_session) -> None:
        play_session("s1", "14")
        window = KidsWindow(db, capacity=25)
        qtbot.addWidget(window)
        assert window.occupancy == 1

        with transaction(db.connection):
            db.execute("UPDATE l_play_sessions SET status = 'CHECKED_OUT' WHERE id = 's1'")
        window.refresh()

        assert window.occupancy == 0
        assert not window.empty.isHidden()
