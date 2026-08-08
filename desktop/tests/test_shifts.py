"""
The cash drawer.

A shift is the unit money reconciles against. Until this existed the Desktop
could take payments and had nowhere to attribute them: no Z-report, no variance,
and no way to notice that a drawer is short.

The assertions below are about the arithmetic being right and about the
interaction refusing to help a cashier cheat.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from caesar_pos.local import outbox
from caesar_pos.local.db import Database, connect
from caesar_pos.orders import service as orders
from caesar_pos.shifts import service as shifts

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
    connection = connect(tmp_path / "shifts.db")
    database = Database(connection)

    for key, value in FINANCE.items():
        database.upsert_mirror(
            "m_settings",
            {"key": f"finance.{key}", "value": json.dumps(value), "payload": "{}"},
            key="key",
        )
    database.upsert_mirror(
        "m_payment_methods",
        {"id": "m-cash", "code": "CASH", "name_ar": "نقدي", "counts_as_cash": 1, "payload": "{}"},
    )
    database.upsert_mirror(
        "m_payment_methods",
        {"id": "m-card", "code": "CARD", "name_ar": "بطاقة", "counts_as_cash": 0, "payload": "{}"},
    )
    database.upsert_mirror(
        "m_products",
        {"id": "p1", "name_ar": "كابتشينو", "payload": json.dumps({"is_tax_exempt": False})},
    )
    database.upsert_mirror(
        "m_variants", {"id": "v1", "product_id": "p1", "price": "100.00", "payload": "{}"}
    )

    yield database
    connection.close()


def sell(db, *, amount: str, method: str = "m-cash", shift_id: str | None = None):
    """One order, paid. Returns the folded order."""
    settings = orders.settings_from_mirror(db)
    order = orders.open_order(db, settings=settings, shift_id=shift_id)
    order = orders.add_item(db, order.order_id, variant_id="v1")
    return orders.take_payment(
        db, order.order_id, method_id=method, amount=Decimal(amount), shift_id=shift_id
    )


# ── opening ──────────────────────────────────────────────────────────────────


class TestOpening:
    def test_a_shift_opens_with_a_float(self, db) -> None:
        shift = shifts.open_shift(db, opening_cash=Decimal("500.00"))

        assert shift["status"] == shifts.OPEN
        assert Decimal(shift["opening_cash"]) == Decimal("500.00")

    def test_the_id_is_minted_locally(self, db) -> None:
        """
        Orders queued during the shift reference this id and may reach the
        server first. A server-assigned id would leave them pointing at nothing.
        """
        shift = shifts.open_shift(db, opening_cash=Decimal("0"))
        operation = outbox.pending(db)[0]

        assert operation.entity_type == "shift_open"
        assert operation.payload["shift_id"] == shift["id"]

    def test_a_second_shift_on_one_device_is_refused(self, db) -> None:
        """
        A terminal that crashed and opened another on restart would end the day
        with two drawers to reconcile and no way to say which one was counted.
        """
        shifts.open_shift(db, opening_cash=Decimal("500.00"))

        with pytest.raises(shifts.ShiftAlreadyOpen):
            shifts.open_shift(db, opening_cash=Decimal("300.00"))

    def test_a_negative_float_is_refused(self, db) -> None:
        with pytest.raises(ValueError, match="سالبة"):
            shifts.open_shift(db, opening_cash=Decimal("-1"))

    def test_selling_without_a_shift_is_caught(self, db) -> None:
        with pytest.raises(shifts.NoOpenShift):
            shifts.require_open(db)

    def test_a_closed_shift_leaves_none_open(self, db) -> None:
        shifts.open_shift(db, opening_cash=Decimal("100.00"))
        shifts.close_shift(db, counted_cash=Decimal("100.00"))

        assert shifts.current(db) is None
        # And a new one may now be opened.
        assert shifts.open_shift(db, opening_cash=Decimal("50.00"))["status"] == shifts.OPEN


# ── the arithmetic ───────────────────────────────────────────────────────────


class TestZReport:
    def test_an_untouched_drawer_expects_its_float(self, db) -> None:
        shift = shifts.open_shift(db, opening_cash=Decimal("500.00"))
        report = shifts.z_report(db, shift["id"])

        assert report.expected_cash == Decimal("500.00")
        assert report.order_count == 0

    def test_cash_sales_raise_the_expected_total(self, db) -> None:
        shift = shifts.open_shift(db, opening_cash=Decimal("500.00"))
        sell(db, amount="114.00", shift_id=shift["id"])

        report = shifts.z_report(db, shift["id"])

        assert report.cash_sales == Decimal("114.00")
        assert report.expected_cash == Decimal("614.00")
        assert report.order_count == 1

    def test_card_sales_do_not_touch_the_drawer(self, db) -> None:
        """
        Only cash is counted. A card total that disagrees with the log is a
        processor question, and mixing them turns one clear number into two
        vague ones.
        """
        shift = shifts.open_shift(db, opening_cash=Decimal("500.00"))
        sell(db, amount="114.00", method="m-card", shift_id=shift["id"])

        report = shifts.z_report(db, shift["id"])

        assert report.non_cash_sales == Decimal("114.00")
        assert report.cash_sales == Decimal("0.00")
        assert report.expected_cash == Decimal("500.00")

    def test_an_unmirrored_method_is_treated_as_non_cash(self, db) -> None:
        """
        Guessing "cash" would inflate what the drawer should hold and
        manufacture a shortage the cashier cannot explain.
        """
        shift = shifts.open_shift(db, opening_cash=Decimal("500.00"))
        sell(db, amount="114.00", method="m-unknown", shift_id=shift["id"])

        report = shifts.z_report(db, shift["id"])

        assert report.expected_cash == Decimal("500.00")
        assert report.non_cash_sales == Decimal("114.00")

    def test_a_payout_lowers_the_expected_total(self, db) -> None:
        shift = shifts.open_shift(db, opening_cash=Decimal("500.00"))
        shifts.record_movement(
            db, movement_type=shifts.PAY_OUT, amount=Decimal("200.00"), reason="لبن"
        )

        report = shifts.z_report(db, shift["id"])

        assert report.pay_outs == Decimal("200.00")
        assert report.expected_cash == Decimal("300.00")

    def test_a_payin_raises_it(self, db) -> None:
        shift = shifts.open_shift(db, opening_cash=Decimal("500.00"))
        shifts.record_movement(
            db, movement_type=shifts.PAY_IN, amount=Decimal("100.00"), reason="فكة"
        )

        assert shifts.z_report(db, shift["id"]).expected_cash == Decimal("600.00")

    def test_everything_together(self, db) -> None:
        shift = shifts.open_shift(db, opening_cash=Decimal("500.00"))
        sell(db, amount="114.00", shift_id=shift["id"])
        sell(db, amount="114.00", shift_id=shift["id"])
        sell(db, amount="114.00", method="m-card", shift_id=shift["id"])
        shifts.record_movement(
            db, movement_type=shifts.PAY_OUT, amount=Decimal("50.00"), reason="لبن"
        )
        shifts.record_movement(
            db, movement_type=shifts.PAY_IN, amount=Decimal("20.00"), reason="فكة"
        )

        report = shifts.z_report(db, shift["id"])

        assert report.cash_sales == Decimal("228.00")
        assert report.non_cash_sales == Decimal("114.00")
        assert report.expected_cash == Decimal("698.00")
        assert report.order_count == 3

    def test_another_shifts_money_is_not_counted(self, db) -> None:
        first = shifts.open_shift(db, opening_cash=Decimal("500.00"))
        sell(db, amount="114.00", shift_id=first["id"])
        shifts.close_shift(db, counted_cash=Decimal("614.00"))

        second = shifts.open_shift(db, opening_cash=Decimal("500.00"))
        report = shifts.z_report(db, second["id"])

        assert report.cash_sales == Decimal("0.00")
        assert report.expected_cash == Decimal("500.00")

    def test_a_payment_taken_with_no_shift_belongs_to_none(self, db) -> None:
        shift = shifts.open_shift(db, opening_cash=Decimal("500.00"))
        sell(db, amount="114.00", shift_id=None)

        assert shifts.z_report(db, shift["id"]).cash_sales == Decimal("0.00")


# ── movements ────────────────────────────────────────────────────────────────


class TestCashMovements:
    def test_a_reason_is_required(self, db) -> None:
        """
        "Paid the milk man 200" reconciles. An unexplained 200 out of the drawer
        is exactly what the variance report exists to surface.
        """
        shifts.open_shift(db, opening_cash=Decimal("500.00"))

        with pytest.raises(ValueError, match="السبب"):
            shifts.record_movement(
                db, movement_type=shifts.PAY_OUT, amount=Decimal("200.00"), reason="   "
            )

    def test_a_zero_amount_is_refused(self, db) -> None:
        shifts.open_shift(db, opening_cash=Decimal("500.00"))

        with pytest.raises(ValueError):
            shifts.record_movement(
                db, movement_type=shifts.PAY_OUT, amount=Decimal("0"), reason="لا شيء"
            )

    def test_an_unknown_type_is_refused(self, db) -> None:
        shifts.open_shift(db, opening_cash=Decimal("500.00"))

        with pytest.raises(ValueError, match="نوع حركة"):
            shifts.record_movement(db, movement_type="TELEPORT", amount=Decimal("10"), reason="x")

    def test_a_movement_without_a_shift_is_refused(self, db) -> None:
        with pytest.raises(shifts.NoOpenShift):
            shifts.record_movement(
                db, movement_type=shifts.PAY_OUT, amount=Decimal("10"), reason="x"
            )

    def test_a_movement_queues_for_the_server(self, db) -> None:
        shift = shifts.open_shift(db, opening_cash=Decimal("500.00"))
        shifts.record_movement(
            db, movement_type=shifts.PAY_OUT, amount=Decimal("200.00"), reason="لبن"
        )

        operation = next(op for op in outbox.pending(db) if op.entity_type == "cash_movement")
        assert operation.payload["shift_id"] == shift["id"]
        assert operation.payload["reason"] == "لبن"


# ── closing ──────────────────────────────────────────────────────────────────


class TestClosing:
    def test_a_matching_count_has_no_variance(self, db) -> None:
        shift = shifts.open_shift(db, opening_cash=Decimal("500.00"))
        sell(db, amount="114.00", shift_id=shift["id"])

        report = shifts.close_shift(db, counted_cash=Decimal("614.00"))

        assert report.variance == Decimal("0.00")
        assert report.is_short is False

    def test_a_short_drawer_reports_a_negative_variance(self, db) -> None:
        """Signed, because short is the direction that matters."""
        shift = shifts.open_shift(db, opening_cash=Decimal("500.00"))
        sell(db, amount="114.00", shift_id=shift["id"])

        report = shifts.close_shift(db, counted_cash=Decimal("569.00"))

        assert report.variance == Decimal("-45.00")
        assert report.is_short is True

    def test_an_over_drawer_reports_a_positive_one(self, db) -> None:
        shifts.open_shift(db, opening_cash=Decimal("500.00"))
        report = shifts.close_shift(db, counted_cash=Decimal("520.00"))

        assert report.variance == Decimal("20.00")
        assert report.is_short is False

    def test_the_terminals_own_expectation_travels_with_the_close(self, db) -> None:
        """
        The server recomputes and its figure is the one that counts. When the two
        differ, that difference is itself the finding — which needs both numbers.
        """
        shift = shifts.open_shift(db, opening_cash=Decimal("500.00"))
        sell(db, amount="114.00", shift_id=shift["id"])
        shifts.close_shift(db, counted_cash=Decimal("600.00"), reason="عجز")

        operation = next(op for op in outbox.pending(db) if op.entity_type == "shift_close")

        assert operation.payload["counted_cash"] == "600.00"
        assert operation.payload["client_expected_cash"] == "614.00"
        assert operation.payload["reason"] == "عجز"

    def test_closing_without_a_shift_is_refused(self, db) -> None:
        with pytest.raises(shifts.NoOpenShift):
            shifts.close_shift(db, counted_cash=Decimal("100.00"))

    def test_a_negative_count_is_refused(self, db) -> None:
        shifts.open_shift(db, opening_cash=Decimal("500.00"))

        with pytest.raises(ValueError, match="سالباً"):
            shifts.close_shift(db, counted_cash=Decimal("-1"))

    def test_the_count_is_kept_for_a_later_reprint(self, db) -> None:
        shift = shifts.open_shift(db, opening_cash=Decimal("500.00"))
        shifts.close_shift(db, counted_cash=Decimal("480.00"))

        report = shifts.z_report(db, shift["id"])

        assert report.counted_cash == Decimal("480.00")
        assert report.variance == Decimal("-20.00")

    def test_unsettled_orders_are_counted_for_the_warning(self, db) -> None:
        """
        Closing over an unpaid table attributes its bill to a drawer nobody is
        standing at, so the close screen has to say so.
        """
        shift = shifts.open_shift(db, opening_cash=Decimal("500.00"))
        settings = orders.settings_from_mirror(db)
        order = orders.open_order(db, settings=settings, shift_id=shift["id"])
        orders.add_item(db, order.order_id, variant_id="v1")

        assert shifts.unsettled_orders(db, shift["id"]) == 1

        orders.take_payment(
            db, order.order_id, method_id="m-cash", amount=Decimal("114.00"), shift_id=shift["id"]
        )
        assert shifts.unsettled_orders(db, shift["id"]) == 0


# ── what reaches the server ──────────────────────────────────────────────────


class TestTheShiftReachesTheServer:
    def test_an_order_carries_its_shift(self, db) -> None:
        """
        Dropping it left every synced order with no shift, which empties the
        server's Z-report of exactly the sales the terminal made.
        """
        shift = shifts.open_shift(db, opening_cash=Decimal("500.00"))
        settings = orders.settings_from_mirror(db)
        order = orders.open_order(db, settings=settings, shift_id=shift["id"])

        operation = next(
            op
            for op in outbox.pending(db)
            if op.entity_type == "order_open" and op.entity_id == order.order_id
        )
        assert operation.payload["shift_id"] == shift["id"]

    def test_a_payment_carries_its_shift(self, db) -> None:
        shift = shifts.open_shift(db, opening_cash=Decimal("500.00"))
        sell(db, amount="114.00", shift_id=shift["id"])

        operation = next(op for op in outbox.pending(db) if op.entity_type == "payment")
        assert operation.payload["shift_id"] == shift["id"]

    def test_the_operations_queue_in_causal_order(self, db) -> None:
        """
        The shift must be first: the server adopts the client's id, and an order
        arriving before it would reference a drawer that does not exist yet.
        """
        shift = shifts.open_shift(db, opening_cash=Decimal("500.00"))
        sell(db, amount="114.00", shift_id=shift["id"])
        shifts.close_shift(db, counted_cash=Decimal("614.00"))

        types = [op.entity_type for op in outbox.pending(db, limit=50)]

        assert types[0] == "shift_open"
        assert types[-1] == "shift_close"
        assert types.index("payment") < types.index("shift_close")
