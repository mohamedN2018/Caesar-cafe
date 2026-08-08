"""
Admitting a child from the terminal.

This exists because of a decision that was wrong and got reversed. The shell
used to refuse offline check-in on the grounds that capacity is a safety limit —
but refusing locally never prevented an over-admission, only the RECORD of one.
A child in the room with no session is worse in every way: nobody knows the
guardian, nothing is billed, the incident log is blank.

The server was already written for this: `play_check_in` enforces capacity again
on arrival and raises a CONFLICT rather than a rejection, because "the child is
already inside and nobody is going to remove them."
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from caesar_pos.kids import service as kids
from caesar_pos.local import outbox
from caesar_pos.local.db import Database, connect, transaction
from caesar_pos.ui.kids.window import running_charge

AREA = {
    "max_capacity": 3,
    "min_age_months": 12,
    "max_age_months": 144,
    "is_active": True,
}

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
def db(tmp_path):
    connection = connect(tmp_path / "kids.db")
    database = Database(connection)

    database.upsert_mirror(
        "m_kids_areas",
        {"id": "area-1", "name_ar": "صالة الأطفال", "payload": json.dumps(AREA)},
    )
    database.upsert_mirror(
        "m_kids_tariffs",
        {
            "id": "t-1",
            "area_id": "area-1",
            "name_ar": "عداد بالساعة",
            "payload": json.dumps(TARIFF),
        },
    )

    yield database
    connection.close()


def admit(db, *, tag: str = "14", name: str = "يوسف", **extra):
    return kids.check_in(
        db,
        area_id="area-1",
        child_name=name,
        guardian_name="أحمد محمود",
        guardian_phone="01001234567",
        tag_number=tag,
        tariff_id="t-1",
        **extra,
    )


class TestAdmitting:
    def test_a_child_is_admitted_offline(self, db) -> None:
        session = admit(db)

        assert session["status"] == kids.ACTIVE
        assert session["child_name"] == "يوسف"
        assert kids.occupancy(db, "area-1") == 1

    def test_it_queues_for_the_server(self, db) -> None:
        session = admit(db)
        operation = next(op for op in outbox.pending(db) if op.entity_type == "play_check_in")

        # The id is ours, so the check-out queued against it resolves when it
        # arrives — the server adopts the client's id for that reason.
        assert operation.payload["session_id"] == session["id"]
        assert operation.payload["tag_number"] == "14"

    def test_the_tariff_is_snapshotted(self, db) -> None:
        """
        A price edited while the child is playing must not re-price the visit,
        exactly as the server does it.
        """
        session = admit(db)
        snapshot = json.loads(session["tariff_snapshot"])

        assert snapshot["entry_fee"] == "25.00"
        assert snapshot["included_minutes"] == 30

    def test_the_medical_note_survives_to_the_board(self, db) -> None:
        """
        A guardian mentions an allergy once, at the door. Nowhere to put it in
        that moment means it is never recorded.
        """
        session = admit(db, medical_notes="حساسية من الفول السوداني")

        assert session["medical_notes"] == "حساسية من الفول السوداني"
        queued = next(op for op in outbox.pending(db) if op.entity_type == "play_check_in")
        assert queued.payload["medical_notes"] == "حساسية من الفول السوداني"

    def test_the_running_charge_uses_the_snapshot(self, db) -> None:
        """The board's figure and the bill come from the same vendored engine."""
        session = admit(db)
        with transaction(db.connection):
            db.execute(
                "UPDATE l_play_sessions SET checked_in_at = ? WHERE id = ?",
                ((datetime.now(UTC) - timedelta(minutes=52)).isoformat(), session["id"]),
            )

        row = dict(db.one("SELECT * FROM l_play_sessions WHERE id = ?", (session["id"],)))
        assert running_charge(row) == Decimal("55.00")


class TestCapacity:
    def test_a_full_area_refuses(self, db) -> None:
        """
        A safety rule, not a target. The number exists because of how many
        children one member of staff can watch.
        """
        for index in range(3):
            admit(db, tag=str(index))

        with pytest.raises(kids.AreaFull, match="ممتلئة"):
            admit(db, tag="99")

    def test_a_refusal_records_nothing(self, db) -> None:
        for index in range(3):
            admit(db, tag=str(index))
        try:
            admit(db, tag="99")
        except kids.AreaFull:
            pass

        assert kids.occupancy(db, "area-1") == 3
        assert len([op for op in outbox.pending(db) if op.entity_type == "play_check_in"]) == 3

    def test_a_checked_out_child_frees_a_place(self, db) -> None:
        sessions = [admit(db, tag=str(index)) for index in range(3)]
        kids.check_out(db, sessions[0]["id"])

        assert kids.occupancy(db, "area-1") == 2
        assert admit(db, tag="99")["status"] == kids.ACTIVE

    def test_an_unsynced_area_is_not_guessed_at(self, db) -> None:
        """Admitting into an area whose capacity is unknown is admitting blind."""
        with pytest.raises(kids.AreaUnknown):
            kids.check_in(
                db,
                area_id="area-nope",
                child_name="سارة",
                guardian_name="منى",
                tag_number="7",
            )


class TestTags:
    def test_a_tag_in_use_is_refused(self, db) -> None:
        """
        A tag is how staff match a child to a guardian at the door. Two children
        on tag 14 makes that match a guess.
        """
        admit(db, tag="14")

        with pytest.raises(ValueError, match="مستخدم"):
            admit(db, tag="14", name="سارة")

    def test_a_tag_is_free_again_after_check_out(self, db) -> None:
        session = admit(db, tag="14")
        kids.check_out(db, session["id"])

        assert admit(db, tag="14", name="سارة")["child_name"] == "سارة"

    def test_names_are_required(self, db) -> None:
        with pytest.raises(ValueError, match="مطلوبان"):
            kids.check_in(
                db, area_id="area-1", child_name="  ", guardian_name="أحمد", tag_number="14"
            )


class TestCheckingOut:
    def test_it_records_and_queues(self, db) -> None:
        session = admit(db)
        kids.check_out(db, session["id"])

        row = db.one("SELECT * FROM l_play_sessions WHERE id = ?", (session["id"],))
        assert row["status"] == kids.CHECKED_OUT
        assert row["checked_out_at"]

        queued = next(op for op in outbox.pending(db) if op.entity_type == "play_check_out")
        assert queued.payload["session_id"] == session["id"]
        assert queued.payload["bill"] is True

    def test_it_works_with_the_network_down(self, db) -> None:
        """
        A terminal that could not release a child during an outage would leave a
        parent standing at a gate.
        """
        session = admit(db)
        kids.check_out(db, session["id"])  # no client, no network, no failure

        assert kids.occupancy(db, "area-1") == 0

    def test_checking_out_twice_is_refused(self, db) -> None:
        session = admit(db)
        kids.check_out(db, session["id"])

        with pytest.raises(ValueError, match="خرج بالفعل"):
            kids.check_out(db, session["id"])

    def test_an_unknown_session_is_refused(self, db) -> None:
        with pytest.raises(ValueError, match="غير موجودة"):
            kids.check_out(db, "nope")
