"""
Orders on the terminal.

`TestGoldenParity` is the one that matters: it runs the SERVER's golden fixture
through the DESKTOP's fold. Not a copy of the fixture — the same file. If the two
implementations ever disagree by a piaster, this fails, which is the only kind of
evidence about client/server agreement worth having.

The rest checks that the terminal enforces the same rules the server does, and
that every mutation leaves an outbox row behind it.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from caesar_pos.local import outbox
from caesar_pos.local.db import Database, connect, transaction
from caesar_pos.orders import service
from caesar_pos.orders.events import EventType, ItemStatus, OrderStatus
from caesar_pos.orders.fold import fold
from caesar_pos.vendored.money import TaxRules

GOLDEN = Path(__file__).resolve().parents[2] / "backend" / "tests" / "fixtures" / "money_cases.json"


@pytest.fixture
def db(tmp_path):
    connection = connect(tmp_path / "orders.db")
    yield Database(connection)
    connection.close()


@pytest.fixture
def menu(db):
    """A minimal catalog in the mirror, as the puller would have left it."""
    db.upsert_mirror(
        "m_products",
        {
            "id": "p-capp",
            "name_ar": "كابتشينو",
            "station_id": "st-coffee",
            "payload": json.dumps({"is_tax_exempt": False}),
        },
    )
    db.upsert_mirror(
        "m_variants",
        {
            "id": "v-capp",
            "product_id": "p-capp",
            "name_ar": "",
            "price": "60.00",
            "cost": "18.00",
            "payload": "{}",
        },
    )
    db.upsert_mirror(
        "m_products",
        {"id": "p-tea", "name_ar": "شاي", "payload": json.dumps({"is_tax_exempt": True})},
    )
    db.upsert_mirror(
        "m_variants",
        {"id": "v-tea", "product_id": "p-tea", "price": "25.00", "cost": "4.00", "payload": "{}"},
    )
    return {"cappuccino": "v-capp", "tea": "v-tea"}


@pytest.fixture
def settings():
    """14% VAT, no service — the golden file's defaults."""
    return service.Settings(
        vat_percent=Decimal("14.00"),
        vat_enabled=True,
        vat_inclusive=False,
        service_percent=Decimal("0.00"),
        service_enabled=False,
        rounding_step=Decimal("0.01"),
    )


def an_order(db, settings, **kwargs):
    return service.open_order(db, settings=settings, **kwargs)


# ── the parity claim ─────────────────────────────────────────────────────────


class TestGoldenParity:
    """
    The SERVER's fixture, run through the DESKTOP's fold.

    A one-piaster disagreement between the two is a customer charged the wrong
    amount by whichever side happened to compute it, and nobody notices until a
    reconciliation weeks later.
    """

    fixture = json.loads(GOLDEN.read_text(encoding="utf-8"))

    @staticmethod
    def _rules(overrides: dict | None = None) -> TaxRules:
        merged = {**TestGoldenParity.fixture["defaults"]["rules"], **(overrides or {})}
        return TaxRules(
            vat_percent=Decimal(merged["vat_percent"]),
            vat_enabled=merged["vat_enabled"],
            vat_inclusive=merged["vat_inclusive"],
            service_percent=Decimal(merged["service_percent"]),
            service_enabled=merged["service_enabled"],
            rounding_step=Decimal(merged["rounding_step"]),
        )

    @pytest.mark.parametrize("case", fixture["cases"], ids=[c["id"] for c in fixture["cases"]])
    def test_the_fold_matches_the_server(self, case: dict) -> None:
        events = [
            {
                "sequence": index + 1,
                "event_type": EventType.ITEM_ADDED,
                "payload": {
                    "line_id": f"line-{index}",
                    "variant_id": f"v-{index}",
                    "name_snapshot": "بند",
                    "unit_price_snapshot": line["unit_price"],
                    "quantity": line.get("quantity", "1"),
                    "tax_exempt_snapshot": line.get("tax_exempt", False),
                    "modifiers": [
                        {"price_delta": delta} for delta in line.get("modifier_deltas", [])
                    ],
                },
            }
            for index, line in enumerate(case["lines"])
        ]

        for index, line in enumerate(case["lines"]):
            if "discount_percent" in line:
                events.append(
                    {
                        "sequence": 1000 + index,
                        "event_type": EventType.DISCOUNT_APPLIED,
                        "payload": {
                            "line_id": f"line-{index}",
                            "percent": line["discount_percent"],
                        },
                    }
                )

        order_discount = case.get(
            "order_discount_percent", self.fixture["defaults"]["order_discount_percent"]
        )
        if Decimal(order_discount) > 0:
            events.append(
                {
                    "sequence": 2000,
                    "event_type": EventType.DISCOUNT_APPLIED,
                    "payload": {"percent": order_discount},
                }
            )

        order = fold("o-1", events, self._rules(case.get("rules")))
        expected = case["expected"]

        assert str(order.totals.subtotal) == expected["subtotal"], case["id"]
        assert str(order.totals.discount_total) == expected["discount_total"], case["id"]
        assert str(order.totals.service_total) == expected["service_total"], case["id"]
        assert str(order.totals.tax_total) == expected["tax_total"], case["id"]
        assert str(order.totals.grand_total) == expected["grand_total"], case["id"]

    def test_the_fixture_is_the_backends_own_file(self) -> None:
        """Not a copy. A copy is a thing that drifts."""
        assert GOLDEN.parts[-4:] == ("backend", "tests", "fixtures", "money_cases.json")
        assert len(self.fixture["cases"]) >= 15


# ── the fold ─────────────────────────────────────────────────────────────────


class TestFold:
    def _rules(self) -> TaxRules:
        return TaxRules(
            vat_percent=Decimal("14"),
            vat_enabled=True,
            vat_inclusive=False,
            service_percent=Decimal("0"),
            service_enabled=False,
            rounding_step=Decimal("0.01"),
        )

    def test_the_documented_example_totals_204_29(self) -> None:
        """
        2× cappuccino + 1× turkish, 12% service, 14% VAT — the figure in docs/04,
        the Phase 1 golden fixture, and the server's own test.
        """
        rules = TaxRules(
            vat_percent=Decimal("14"),
            vat_enabled=True,
            vat_inclusive=False,
            service_percent=Decimal("12"),
            service_enabled=True,
            rounding_step=Decimal("0.01"),
        )
        events = [
            {
                "sequence": 1,
                "event_type": EventType.ITEM_ADDED,
                "payload": {
                    "line_id": "l1",
                    "variant_id": "v1",
                    "unit_price_snapshot": "60.00",
                    "quantity": "2",
                },
            },
            {
                "sequence": 2,
                "event_type": EventType.ITEM_ADDED,
                "payload": {
                    "line_id": "l2",
                    "variant_id": "v2",
                    "unit_price_snapshot": "40.00",
                    "quantity": "1",
                },
            },
        ]

        assert str(fold("o", events, rules).totals.grand_total) == "204.29"

    def test_a_voided_line_is_marked_not_removed(self) -> None:
        """
        A deleted line is an unexplained gap in a financial record; a voided one
        is an auditable decision.
        """
        events = [
            {
                "sequence": 1,
                "event_type": EventType.ITEM_ADDED,
                "payload": {"line_id": "l1", "variant_id": "v1", "unit_price_snapshot": "60.00"},
            },
            {
                "sequence": 2,
                "event_type": EventType.ITEM_VOIDED,
                "payload": {"line_id": "l1", "reason": "غلط"},
            },
        ]
        order = fold("o", events, self._rules())

        assert len(order.items) == 1, "still there"
        assert order.items[0].status == ItemStatus.VOIDED
        assert order.active_items == []
        assert order.totals.grand_total == Decimal("0.00")

    def test_events_are_folded_in_sequence_not_arrival_order(self) -> None:
        """Events from another device arrive out of order. The sequence decides."""
        events = [
            {
                "sequence": 2,
                "event_type": EventType.ITEM_VOIDED,
                "payload": {"line_id": "l1"},
            },
            {
                "sequence": 1,
                "event_type": EventType.ITEM_ADDED,
                "payload": {"line_id": "l1", "variant_id": "v1", "unit_price_snapshot": "60.00"},
            },
        ]

        assert fold("o", events, self._rules()).items[0].status == ItemStatus.VOIDED

    def test_an_unknown_event_is_skipped_rather_than_fatal(self) -> None:
        """
        A terminal older than the server. Refusing to render the order would take
        the whole table off the screen over one unfamiliar event.
        """
        events = [
            {
                "sequence": 1,
                "event_type": EventType.ITEM_ADDED,
                "payload": {"line_id": "l1", "variant_id": "v1", "unit_price_snapshot": "60.00"},
            },
            {"sequence": 2, "event_type": "LOYALTY_POINTS_AWARDED", "payload": {"points": 5}},
        ]
        order = fold("o", events, self._rules())

        assert len(order.active_items) == 1
        assert order.totals.grand_total == Decimal("68.40")

    def test_a_payment_reduces_the_balance(self) -> None:
        events = [
            {
                "sequence": 1,
                "event_type": EventType.ITEM_ADDED,
                "payload": {"line_id": "l1", "variant_id": "v1", "unit_price_snapshot": "100.00"},
            },
            {"sequence": 2, "event_type": EventType.PAYMENT_TAKEN, "payload": {"amount": "50.00"}},
        ]
        order = fold("o", events, self._rules())

        assert order.paid_total == Decimal("50.00")
        assert order.balance_due == Decimal("64.00")
        assert order.is_settled is False


# ── the service ──────────────────────────────────────────────────────────────


class TestOrderService:
    def test_opening_queues_the_order(self, db, settings) -> None:
        order = an_order(db, settings)

        assert order.status == OrderStatus.OPEN
        assert order.local_number.startswith("MB-01-")
        assert outbox.counts(db)["pending"] == 1

    def test_adding_an_item_snapshots_the_price(self, db, settings, menu) -> None:
        """
        The mirror may be updated by the puller thirty seconds from now. This
        line keeps the price the customer was quoted.
        """
        order = an_order(db, settings)
        order = service.add_item(db, order.order_id, variant_id=menu["cappuccino"])

        item = order.active_items[0]
        assert item.unit_price_snapshot == Decimal("60.00")
        assert item.name_snapshot == "كابتشينو"
        assert item.station_id == "st-coffee"

        db.upsert_mirror(
            "m_variants",
            {"id": "v-capp", "product_id": "p-capp", "price": "999.00", "payload": "{}"},
        )
        assert service.load(db, order.order_id).active_items[0].unit_price_snapshot == Decimal(
            "60.00"
        )

    def test_a_tax_exempt_product_is_snapshotted_as_exempt(self, db, settings, menu) -> None:
        order = an_order(db, settings)
        order = service.add_item(db, order.order_id, variant_id=menu["tea"])

        assert order.active_items[0].tax_exempt_snapshot is True
        assert order.totals.tax_total == Decimal("0.00")

    def test_every_mutation_leaves_an_outbox_row(self, db, settings, menu) -> None:
        order = an_order(db, settings)
        order = service.add_item(db, order.order_id, variant_id=menu["cappuccino"])
        line_id = order.active_items[0].line_id

        service.change_quantity(db, order.order_id, line_id, Decimal("3"))
        service.set_note(db, order.order_id, line_id, "بدون سكر")
        service.apply_discount(db, order.order_id, percent=Decimal("10"))

        # open + add + quantity + note + discount
        assert outbox.counts(db)["pending"] == 5

    def test_the_projection_matches_the_fold(self, db, settings, menu) -> None:
        """
        The projection is a cache. If it ever disagrees with the events, the
        events win — so it is rewritten wholesale rather than diffed.
        """
        order = an_order(db, settings)
        order = service.add_item(
            db, order.order_id, variant_id=menu["cappuccino"], quantity=Decimal("2")
        )

        row = db.one("SELECT * FROM l_orders WHERE id = ?", (order.order_id,))
        assert row["grand_total"] == str(order.totals.grand_total) == "136.80"
        assert (
            db.scalar("SELECT COUNT(*) FROM l_order_items WHERE order_id = ?", (order.order_id,))
            == 1
        )

    def test_voiding_keeps_the_line_in_the_projection(self, db, settings, menu) -> None:
        order = an_order(db, settings)
        order = service.add_item(db, order.order_id, variant_id=menu["cappuccino"])
        order = service.void_item(db, order.order_id, order.items[0].line_id, reason="طلب العميل")

        assert (
            db.scalar("SELECT status FROM l_order_items WHERE order_id = ?", (order.order_id,))
            == ItemStatus.VOIDED
        )
        assert (
            db.scalar("SELECT grand_total FROM l_orders WHERE id = ?", (order.order_id,)) == "0.00"
        )

    def test_a_terminal_without_settings_refuses_to_price(self, db) -> None:
        """Selling at a guessed VAT rate is worse than refusing to sell."""
        with pytest.raises(RuntimeError, match="has not synced"):
            service.settings_from_mirror(db)

    def test_settings_come_from_the_mirror(self, db) -> None:
        for key, value in [
            ("vat_percent", "14.00"),
            ("vat_enabled", True),
            ("vat_inclusive", False),
            ("service_percent", "12.00"),
            ("service_enabled", True),
            ("service_applies_to", ["DINE_IN"]),
            ("rounding_step", "0.01"),
        ]:
            db.upsert_mirror(
                "m_settings",
                {
                    "key": f"finance.{key}",
                    "value": json.dumps(value),
                    "payload": "{}",
                },
                key="key",
            )

        resolved = service.settings_from_mirror(db, order_type="DINE_IN")
        assert resolved.vat_percent == Decimal("14.00")
        assert resolved.service_enabled is True

        takeaway = service.settings_from_mirror(db, order_type="TAKE_AWAY")
        assert takeaway.service_enabled is False, "service does not apply to takeaway here"


class TestFiring:
    def test_firing_marks_the_items_and_moves_the_order(self, db, settings, menu) -> None:
        order = an_order(db, settings)
        order = service.add_item(db, order.order_id, variant_id=menu["cappuccino"])
        order = service.fire(db, order.order_id)

        assert order.status == OrderStatus.IN_KITCHEN
        assert order.active_items[0].fired_at is not None
        assert order.unfired_items == []

    def test_firing_with_nothing_new_is_refused(self, db, settings, menu) -> None:
        """
        A second press that re-sent everything would have the kitchen make the
        first round twice, and the cashier would have no way to tell.
        """
        order = an_order(db, settings)
        order = service.add_item(db, order.order_id, variant_id=menu["cappuccino"])
        service.fire(db, order.order_id)

        with pytest.raises(ValueError, match="لا توجد أصناف جديدة"):
            service.fire(db, order.order_id)

    def test_a_second_round_fires_only_what_is_new(self, db, settings, menu) -> None:
        order = an_order(db, settings)
        service.add_item(db, order.order_id, variant_id=menu["cappuccino"])
        service.fire(db, order.order_id)

        service.add_item(db, order.order_id, variant_id=menu["tea"])
        order = service.load(db, order.order_id)
        assert len(order.unfired_items) == 1

        order = service.fire(db, order.order_id)
        assert order.unfired_items == []


class TestPayment:
    def _payable(self, db, settings, menu):
        order = an_order(db, settings)
        return service.add_item(db, order.order_id, variant_id=menu["cappuccino"])

    def test_paying_in_full_closes_the_order(self, db, settings, menu) -> None:
        order = self._payable(db, settings, menu)
        order = service.take_payment(
            db, order.order_id, method_id="m-cash", amount=order.totals.grand_total
        )

        assert order.status == OrderStatus.PAID
        assert order.is_settled

    def test_a_split_payment_leaves_the_order_open(self, db, settings, menu) -> None:
        order = self._payable(db, settings, menu)
        order = service.take_payment(
            db, order.order_id, method_id="m-cash", amount=Decimal("20.00")
        )

        assert order.status != OrderStatus.PAID
        assert order.balance_due == Decimal("48.40")

    def test_change_is_computed_from_the_tender(self, db, settings, menu) -> None:
        order = self._payable(db, settings, menu)
        service.take_payment(
            db,
            order.order_id,
            method_id="m-cash",
            amount=Decimal("68.40"),
            tendered=Decimal("100.00"),
        )

        assert db.scalar("SELECT change_given FROM l_payments") == "31.60"

    def test_overpaying_is_refused(self, db, settings, menu) -> None:
        order = self._payable(db, settings, menu)
        with pytest.raises(ValueError, match="المستحق"):
            service.take_payment(db, order.order_id, method_id="m-cash", amount=Decimal("1000.00"))

    def test_a_short_tender_is_refused(self, db, settings, menu) -> None:
        order = self._payable(db, settings, menu)
        with pytest.raises(ValueError, match="أقل من المطلوب"):
            service.take_payment(
                db,
                order.order_id,
                method_id="m-cash",
                amount=Decimal("68.40"),
                tendered=Decimal("50.00"),
            )

    def test_the_payment_carries_its_own_idempotency_key(self, db, settings, menu) -> None:
        """
        A push that times out and is resent must charge exactly once. The server
        returns the original payment rather than creating a second.
        """
        order = self._payable(db, settings, menu)
        service.take_payment(
            db, order.order_id, method_id="m-cash", amount=order.totals.grand_total
        )

        row = db.one("SELECT payload FROM sync_outbox WHERE entity_type = 'payment'")
        assert json.loads(row["payload"])["idempotency_key"]


class TestClosedOrders:
    def _paid(self, db, settings, menu):
        order = an_order(db, settings)
        order = service.add_item(db, order.order_id, variant_id=menu["cappuccino"])
        return service.take_payment(
            db, order.order_id, method_id="m-cash", amount=order.totals.grand_total
        )

    def test_a_paid_order_takes_no_more_items(self, db, settings, menu) -> None:
        """
        Refused HERE, not just greyed out in the UI. A greyed button is a hint;
        this is the rule, and it is the one the server applies on sync.
        """
        order = self._paid(db, settings, menu)
        with pytest.raises(service.OrderClosed):
            service.add_item(db, order.order_id, variant_id=menu["tea"])

    def test_a_paid_order_cannot_be_voided(self, db, settings, menu) -> None:
        """
        PAID → OPEN is unreachable by construction. Reopening is a refund plus a
        new order — two auditable records instead of one silently altered one.
        """
        order = self._paid(db, settings, menu)
        with pytest.raises(service.OrderClosed):
            service.void_order(db, order.order_id, reason="متأخر")

    def test_a_paid_order_takes_no_more_payment(self, db, settings, menu) -> None:
        order = self._paid(db, settings, menu)
        with pytest.raises(service.OrderClosed):
            service.take_payment(db, order.order_id, method_id="m-cash", amount=Decimal("1.00"))

    def test_a_voided_order_leaves_the_board(self, db, settings, menu) -> None:
        order = an_order(db, settings)
        service.add_item(db, order.order_id, variant_id=menu["cappuccino"])
        service.void_order(db, order.order_id, reason="العميل غادر")

        assert service.open_orders(db) == []


def test_the_floor_board_reads_the_projection(db, settings, menu) -> None:
    """Twelve tables, not twelve folds."""
    for _ in range(3):
        order = an_order(db, settings)
        service.add_item(db, order.order_id, variant_id=menu["cappuccino"])

    board = service.open_orders(db)
    assert len(board) == 3
    assert all(row["grand_total"] == "68.40" for row in board)


def test_reload_rebuilds_the_projection_from_the_events(db, settings, menu) -> None:
    """
    If the two ever disagree, the events win. This is the recovery path — and the
    reason a fold bug is fixable rather than permanent corruption.
    """
    order = an_order(db, settings)
    order = service.add_item(db, order.order_id, variant_id=menu["cappuccino"])

    with transaction(db.connection):
        db.execute("DELETE FROM l_order_items WHERE order_id = ?", (order.order_id,))
        db.execute("UPDATE l_orders SET grand_total = '0.00' WHERE id = ?", (order.order_id,))

    rebuilt = service.reload(db, order.order_id)

    assert rebuilt.totals.grand_total == Decimal("68.40")
    assert db.scalar("SELECT grand_total FROM l_orders WHERE id = ?", (order.order_id,)) == "68.40"
    assert (
        db.scalar("SELECT COUNT(*) FROM l_order_items WHERE order_id = ?", (order.order_id,)) == 1
    )
