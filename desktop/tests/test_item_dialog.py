"""
Choosing a size, the add-ons, and how many.

The assertion that matters most is the last one: **the price the dialog quotes
is the price the fold computes.** A customer at the till asks "how much?" before
the item is committed, and if the dialog answered with its own arithmetic, that
would be the one place their expectation and the bill diverge.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from caesar_pos.local.db import Database, connect
from caesar_pos.orders import service
from caesar_pos.ui.pos.catalog import Tile
from caesar_pos.ui.pos.item_dialog import ItemDialog, line_total

LATTE = Tile(
    variant_id="v-med",
    product_id="p1",
    name_ar="لاتيه وسط",
    price=Decimal("65.00"),
    category_id="c1",
    variant_count=2,
)

LARGE = Tile(
    variant_id="v-large",
    product_id="p1",
    name_ar="لاتيه كبير",
    price=Decimal("80.00"),
    category_id="c1",
    variant_count=2,
)

MODIFIERS = [
    {"id": "m-shot", "group_id": "g1", "name_ar": "شوت زيادة", "price_delta": "15.00"},
    {"id": "m-skim", "group_id": "g1", "name_ar": "لبن خالي الدسم", "price_delta": "0.00"},
    {"id": "m-caramel", "group_id": "g1", "name_ar": "كراميل", "price_delta": "10.00"},
]


@pytest.fixture
def dialog(qtbot):
    widget = ItemDialog(LATTE, variants=[LATTE, LARGE], modifiers=MODIFIERS)
    qtbot.addWidget(widget)
    return widget


class TestChoosing:
    def test_it_opens_on_the_first_size(self, dialog) -> None:
        assert dialog.chosen.variant_id == "v-med"
        assert dialog.value == Decimal("65.00")

    def test_choosing_a_size_reprices(self, dialog) -> None:
        dialog._choose_size(LARGE)
        assert dialog.value == Decimal("80.00")

    def test_an_add_on_raises_the_total(self, dialog) -> None:
        dialog._toggle(MODIFIERS[0])
        assert dialog.value == Decimal("80.00")

    def test_tapping_an_add_on_twice_removes_it(self, dialog) -> None:
        dialog._toggle(MODIFIERS[0])
        dialog._toggle(MODIFIERS[0])

        assert dialog.selected == []
        assert dialog.value == Decimal("65.00")

    def test_a_free_add_on_costs_nothing(self, dialog) -> None:
        dialog._toggle(MODIFIERS[1])
        assert dialog.value == Decimal("65.00")
        assert len(dialog.selected) == 1

    def test_add_ons_stack(self, dialog) -> None:
        dialog._toggle(MODIFIERS[0])
        dialog._toggle(MODIFIERS[2])
        assert dialog.value == Decimal("90.00")


class TestQuantity:
    def test_it_starts_at_one(self, dialog) -> None:
        assert dialog.quantity == Decimal("1")

    def test_stepping_up_multiplies_the_total(self, dialog) -> None:
        dialog._step(1)
        assert dialog.quantity == Decimal("2")
        assert dialog.value == Decimal("130.00")

    def test_it_never_goes_below_one(self, dialog) -> None:
        """
        A zero-quantity line is a line that should have been cancelled, and
        letting it exist puts nothing on a receipt somebody has to explain.
        """
        dialog._step(-1)
        dialog._step(-1)
        assert dialog.quantity == Decimal("1")

    def test_quantity_multiplies_the_add_ons_too(self, dialog) -> None:
        dialog._toggle(MODIFIERS[0])
        dialog._step(1)
        assert dialog.value == Decimal("160.00")


class TestConfirming:
    def test_it_emits_everything_the_service_needs(self, dialog) -> None:
        dialog._choose_size(LARGE)
        dialog._toggle(MODIFIERS[0])
        dialog._step(1)
        dialog.note.setText("سخن زيادة")

        emitted = []
        dialog.confirmed.connect(lambda v, q, m, n: emitted.append((v, q, m, n)))
        dialog._confirm()

        variant_id, quantity, modifiers, note = emitted[0]
        assert variant_id == "v-large"
        assert quantity == Decimal("2")
        assert [m["id"] for m in modifiers] == ["m-shot"]
        assert note == "سخن زيادة"

    def test_the_emitted_modifiers_carry_their_price(self, dialog) -> None:
        """The fold prices from what it is handed, not from the mirror again."""
        dialog._toggle(MODIFIERS[0])
        emitted = []
        dialog.confirmed.connect(lambda v, q, m, n: emitted.append(m))
        dialog._confirm()

        assert emitted[0][0]["price_delta"] == "15.00"


# ── the number on the screen is the number on the bill ───────────────────────


@pytest.fixture
def db(tmp_path):
    connection = connect(tmp_path / "item.db")
    database = Database(connection)

    for key, value in {
        "vat_percent": "14.00",
        "vat_enabled": True,
        "vat_inclusive": False,
        "service_percent": "0.00",
        "service_enabled": False,
        "service_applies_to": [],
        "rounding_step": "0.01",
    }.items():
        database.upsert_mirror(
            "m_settings",
            {"key": f"finance.{key}", "value": json.dumps(value), "payload": "{}"},
            key="key",
        )
    database.upsert_mirror(
        "m_products",
        {"id": "p1", "name_ar": "لاتيه", "payload": json.dumps({"is_tax_exempt": False})},
    )
    database.upsert_mirror(
        "m_variants",
        {"id": "v-med", "product_id": "p1", "price": "65.00", "payload": "{}"},
    )

    yield database
    connection.close()


@pytest.mark.parametrize(
    ("chosen", "quantity"),
    [([], "1"), (["m-shot"], "1"), (["m-shot", "m-caramel"], "3"), (["m-skim"], "2")],
)
def test_the_dialog_quotes_what_the_fold_will_charge(db, chosen, quantity) -> None:
    """
    Both sides compute base-plus-deltas times quantity. Two implementations of
    that would drift, and the one on screen is the one the customer heard.
    """
    modifiers = [
        {"id": m["id"], "name_ar": m["name_ar"], "price_delta": m["price_delta"]}
        for m in MODIFIERS
        if m["id"] in chosen
    ]

    order = service.open_order(db, settings=service.settings_from_mirror(db))
    order = service.add_item(
        db,
        order.order_id,
        variant_id="v-med",
        quantity=Decimal(quantity),
        modifiers=modifiers,
    )

    quoted = line_total(Decimal("65.00"), modifiers, Decimal(quantity))
    assert order.totals.subtotal == quoted
