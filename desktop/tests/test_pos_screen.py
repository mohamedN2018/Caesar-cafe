"""
The POS screen.

Widget tests, but the assertions are about behaviour a cashier would notice: a
tap adds an item, a paid order clears the till, a void after firing asks why, and
the panel never invents a number the receipt would disagree with.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from caesar_pos.local.db import Database, connect
from caesar_pos.orders import service
from caesar_pos.security.session import Session
from caesar_pos.ui.pos import catalog
from caesar_pos.ui.pos.order_panel import OrderPanel
from caesar_pos.ui.pos.payment_dialog import PaymentDialog
from caesar_pos.ui.pos.product_grid import ProductGrid
from caesar_pos.ui.pos.window import PosWindow


@pytest.fixture
def db(tmp_path):
    connection = connect(tmp_path / "pos.db")
    yield Database(connection)
    connection.close()


@pytest.fixture
def menu(db):
    """A small catalog, as the puller would have left it."""
    db.upsert_mirror(
        "m_categories",
        {"id": "c-drinks", "name_ar": "مشروبات", "sort_order": 1, "payload": "{}"},
    )
    db.upsert_mirror(
        "m_categories",
        {"id": "c-food", "name_ar": "مأكولات", "sort_order": 2, "payload": "{}"},
    )

    def product(pid, name, category, price, *, sort=0, variants=None):
        db.upsert_mirror(
            "m_products",
            {
                "id": pid,
                "name_ar": name,
                "category_id": category,
                "sort_order": sort,
                "payload": json.dumps({"is_tax_exempt": False}),
            },
        )
        if variants is None:
            db.upsert_mirror(
                "m_variants",
                {
                    "id": f"v-{pid}",
                    "product_id": pid,
                    "price": price,
                    "cost": "10.00",
                    "is_default": 1,
                    "payload": "{}",
                },
            )
        else:
            for index, (name_ar, variant_price) in enumerate(variants):
                db.upsert_mirror(
                    "m_variants",
                    {
                        "id": f"v-{pid}-{index}",
                        "product_id": pid,
                        "name_ar": name_ar,
                        "price": variant_price,
                        "cost": "10.00",
                        "is_default": 1 if index == 0 else 0,
                        # As the server sends it. Without it the chooser falls
                        # back to alphabetical, which puts "كبير" before "وسط".
                        "sort_order": index,
                        "payload": "{}",
                    },
                )

    product("p-capp", "كابتشينو", "c-drinks", "60.00", sort=1)
    product("p-tea", "شاي", "c-drinks", "25.00", sort=2)
    product("p-cake", "تشيز كيك", "c-food", "75.00", sort=1)
    product(
        "p-juice",
        "عصير",
        "c-drinks",
        "35.00",
        sort=3,
        variants=[("وسط", "35.00"), ("كبير", "45.00")],
    )

    db.upsert_mirror(
        "m_payment_methods",
        {"id": "m-cash", "code": "CASH", "name_ar": "نقدي", "counts_as_cash": 1, "payload": "{}"},
    )
    db.upsert_mirror(
        "m_payment_methods",
        {"id": "m-card", "code": "CARD", "name_ar": "بطاقة", "counts_as_cash": 0, "payload": "{}"},
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


@pytest.fixture
def cashier():
    return Session(
        user_id="u1",
        full_name_ar="أحمد",
        permissions=frozenset({"orders.create", "orders.discount", "payments.take"}),
        started_at=datetime.now(UTC),
        roles=("CASHIER",),
    )


@pytest.fixture
def window(qtbot, db, menu, settings, cashier):
    win = PosWindow(db, cashier, settings)
    qtbot.addWidget(win)
    return win


def tile_named(window, name: str):
    return next(t for t in window.grid.visible_tiles if t.name_ar == name)


# ── the catalog query ────────────────────────────────────────────────────────


class TestCatalogQueries:
    def test_only_the_default_variant_gets_a_tile(self, db, menu) -> None:
        """
        Four products, four tiles — the juice has two sizes but the grid shows
        one button, and the chooser opens on tap.
        """
        assert len(catalog.tiles(db)) == 4

    def test_a_multi_variant_product_is_flagged(self, db, menu) -> None:
        juice = next(t for t in catalog.tiles(db) if t.name_ar == "عصير")
        assert juice.needs_variant_choice is True

    def test_a_single_variant_product_is_not(self, db, menu) -> None:
        """Making a cashier pick from a list of one is a tax on every order."""
        capp = next(t for t in catalog.tiles(db) if t.name_ar == "كابتشينو")
        assert capp.needs_variant_choice is False

    def test_the_admins_sort_order_is_honoured(self, db, menu) -> None:
        """
        Not alphabetical. The layout is a decision the manager made about what
        staff reach for, and muscle memory is what makes a busy hour survivable.
        """
        drinks = [t.name_ar for t in catalog.tiles(db, category_id="c-drinks")]
        assert drinks == ["كابتشينو", "شاي", "عصير"]

    def test_an_inactive_product_is_not_sellable(self, db, menu) -> None:
        db.upsert_mirror(
            "m_products",
            {"id": "p-capp", "name_ar": "كابتشينو", "is_active": 0, "payload": "{}"},
        )
        assert all(t.name_ar != "كابتشينو" for t in catalog.tiles(db))

    def test_variants_of_lists_every_size(self, db, menu) -> None:
        options = catalog.variants_of(db, "p-juice")
        assert [o.price for o in options] == [Decimal("35.00"), Decimal("45.00")]


# ── the grid ─────────────────────────────────────────────────────────────────


class TestProductGrid:
    def test_it_starts_showing_everything(self, qtbot, db, menu) -> None:
        grid = ProductGrid()
        qtbot.addWidget(grid)
        grid.load(catalog.categories(db), catalog.tiles(db))

        assert len(grid.visible_tiles) == 4

    def test_a_category_tab_filters(self, qtbot, db, menu) -> None:
        grid = ProductGrid()
        qtbot.addWidget(grid)
        grid.load(catalog.categories(db), catalog.tiles(db))

        grid.select_category("c-food")
        assert [t.name_ar for t in grid.visible_tiles] == ["تشيز كيك"]

    def test_search_spans_every_category(self, qtbot, db, menu) -> None:
        """
        Someone typing a name is looking for a product, not for a product within
        the tab they happen to be on.
        """
        grid = ProductGrid()
        qtbot.addWidget(grid)
        grid.load(catalog.categories(db), catalog.tiles(db))

        grid.select_category("c-drinks")
        grid.search.setText("تشيز")

        assert [t.name_ar for t in grid.visible_tiles] == ["تشيز كيك"]

    def test_tapping_a_tile_emits_it(self, qtbot, db, menu) -> None:
        grid = ProductGrid()
        qtbot.addWidget(grid)
        grid.load(catalog.categories(db), catalog.tiles(db))

        chosen = []
        grid.chosen.connect(chosen.append)
        grid.grid.itemAt(0).widget().click()

        assert chosen and chosen[0].name_ar == "كابتشينو"

    def test_an_empty_result_says_so(self, qtbot, db, menu) -> None:
        grid = ProductGrid()
        qtbot.addWidget(grid)
        grid.load(catalog.categories(db), catalog.tiles(db))
        grid.search.setText("لا يوجد")

        assert grid.visible_tiles == []
        assert not grid.empty.isHidden()


# ── the order panel ──────────────────────────────────────────────────────────


class TestOrderPanel:
    def _order(self, db, settings, menu_ids=("v-p-capp",)):
        order = service.open_order(db, settings=settings)
        for variant_id in menu_ids:
            order = service.add_item(db, order.order_id, variant_id=variant_id)
        return order

    def test_it_shows_the_folds_numbers_verbatim(self, qtbot, db, menu, settings) -> None:
        """
        A panel that added its own subtotal would eventually show a number the
        receipt disagrees with.
        """
        panel = OrderPanel()
        qtbot.addWidget(panel)
        order = self._order(db, settings)
        panel.show_order(order)

        assert panel.grand_value.text() == str(order.totals.grand_total) == "68.40"
        assert panel._rows["tax"][1].text() == "8.40"

    def test_a_voided_line_stays_visible_struck_through(self, qtbot, db, menu, settings) -> None:
        """The cashier can see what was removed, and so can the customer."""
        panel = OrderPanel()
        qtbot.addWidget(panel)

        order = self._order(db, settings)
        order = service.void_item(db, order.order_id, order.items[0].line_id, reason="غلط")
        panel.show_order(order)

        assert panel.table.rowCount() == 1
        assert panel.table.item(0, 0).font().strikeOut() is True
        assert panel.grand_value.text() == "0.00"

    def test_the_remaining_line_appears_only_after_a_partial_payment(
        self, qtbot, db, menu, settings
    ) -> None:
        """A 'remaining' that always equals the total is noise the eye skips."""
        panel = OrderPanel()
        qtbot.addWidget(panel)

        order = self._order(db, settings)
        panel.show_order(order)
        assert panel.due_value.isHidden()

        order = service.take_payment(
            db, order.order_id, method_id="m-cash", amount=Decimal("20.00")
        )
        panel.show_order(order)

        assert not panel.due_value.isHidden()
        assert panel.due_value.text() == "48.40"

    def test_fire_is_disabled_when_there_is_nothing_new(self, qtbot, db, menu, settings) -> None:
        panel = OrderPanel()
        qtbot.addWidget(panel)

        order = self._order(db, settings)
        panel.show_order(order)
        assert panel.fire_button.isEnabled()

        order = service.fire(db, order.order_id)
        panel.show_order(order)
        assert not panel.fire_button.isEnabled()

    def test_an_empty_panel_offers_nothing(self, qtbot) -> None:
        panel = OrderPanel()
        qtbot.addWidget(panel)
        panel.show_order(None)

        assert not panel.pay_button.isEnabled()
        assert not panel.empty.isHidden()

    def test_quantities_are_trimmed(self, qtbot, db, menu, settings) -> None:
        """`2.000` reads as noise; `2` reads as a quantity."""
        panel = OrderPanel()
        qtbot.addWidget(panel)

        order = service.open_order(db, settings=settings)
        order = service.add_item(db, order.order_id, variant_id="v-p-capp", quantity=Decimal("2"))
        panel.show_order(order)

        assert panel.table.item(0, 1).text() == "2"


# ── the payment dialog ───────────────────────────────────────────────────────


class TestPaymentDialog:
    methods = [
        {"id": "m-cash", "name_ar": "نقدي", "counts_as_cash": True},
        {"id": "m-card", "name_ar": "بطاقة", "counts_as_cash": False},
    ]

    def test_the_amount_is_prefilled_with_the_balance(self, qtbot) -> None:
        """Paying in full is the common case and should take one tap."""
        dialog = PaymentDialog(balance_due=Decimal("68.40"), methods=self.methods)
        qtbot.addWidget(dialog)

        assert dialog.entered_amount == Decimal("68.40")
        assert dialog.confirm_button.isEnabled()

    def test_a_tender_shows_the_change_before_confirming(self, qtbot) -> None:
        """A cashier handed a 100 needs the change figure before the drawer opens."""
        dialog = PaymentDialog(balance_due=Decimal("68.40"), methods=self.methods)
        qtbot.addWidget(dialog)

        dialog.tender(Decimal("100"))

        assert "31.60" in dialog.change.text()
        assert dialog.entered_amount == Decimal("68.40"), "still a 68.40 sale"

    def test_a_partial_amount_is_allowed_and_named(self, qtbot) -> None:
        """Split payment is not a special mode."""
        dialog = PaymentDialog(balance_due=Decimal("100.00"), methods=self.methods)
        qtbot.addWidget(dialog)

        dialog.amount.setText("40")

        assert dialog.confirm_button.isEnabled()
        assert "يتبقى 60.00" in dialog.change.text()

    def test_overpaying_is_refused_with_the_reason(self, qtbot) -> None:
        dialog = PaymentDialog(balance_due=Decimal("68.40"), methods=self.methods)
        qtbot.addWidget(dialog)

        dialog.amount.setText("200")

        assert not dialog.confirm_button.isEnabled()
        assert "أكبر من المستحق" in dialog.change.text()

    def test_nonsense_input_disables_confirm(self, qtbot) -> None:
        dialog = PaymentDialog(balance_due=Decimal("68.40"), methods=self.methods)
        qtbot.addWidget(dialog)

        dialog.amount.setText("abc")
        assert not dialog.confirm_button.isEnabled()

    def test_quick_tenders_never_offer_a_note_below_the_bill(self, qtbot) -> None:
        """A button that can only ever produce an error should not be there."""
        dialog = PaymentDialog(balance_due=Decimal("120.00"), methods=self.methods)
        qtbot.addWidget(dialog)

        labels = [
            dialog.layout().itemAt(i).widget().text()
            for i in range(dialog.layout().count())
            if dialog.layout().itemAt(i).widget() is not None
        ]
        assert "50" not in labels

    def test_confirming_emits_the_method_and_amount(self, qtbot) -> None:
        dialog = PaymentDialog(balance_due=Decimal("68.40"), methods=self.methods)
        qtbot.addWidget(dialog)

        emitted = []
        dialog.confirmed.connect(lambda *args: emitted.append(args))
        dialog.select_method("m-card")
        dialog._confirm()

        assert emitted == [("m-card", Decimal("68.40"), None)]


# ── the window ───────────────────────────────────────────────────────────────


class TestPosWindow:
    def test_tapping_a_product_opens_an_order_implicitly(self, window) -> None:
        """
        Pressing "new order" before the first item is a step that exists only
        because the software wanted it.
        """
        assert window.order_id is None

        window._add(tile_named(window, "كابتشينو"))

        assert window.order_id is not None
        assert window.panel.grand_value.text() == "68.40"

    def test_a_second_tap_adds_a_second_line(self, window) -> None:
        window._add(tile_named(window, "كابتشينو"))
        window._add(tile_named(window, "شاي"))

        assert window.panel.table.rowCount() == 2
        assert window.panel.grand_value.text() == "96.90"

    def test_the_header_names_who_is_on_the_till(self, window) -> None:
        """The screen is shared and the person changes hourly."""
        assert "أحمد" in window.findChild(type(window.sync_label), "User").text()

    def test_the_sync_state_is_shown(self, window) -> None:
        window.set_sync_label("🔴 غير متصل (12)")
        assert "غير متصل" in window.sync_label.text()

    def test_paying_in_full_clears_the_till(self, window, db) -> None:
        """
        Leaving a paid order on screen is how the next customer's coffee ends up
        on the last one's bill.
        """
        window._add(tile_named(window, "كابتشينو"))
        window._take_payment("m-cash", Decimal("68.40"), None)

        assert window.order_id is None
        assert window.panel.order is None

    def test_a_partial_payment_keeps_the_order_on_screen(self, window) -> None:
        window._add(tile_named(window, "كابتشينو"))
        window._take_payment("m-cash", Decimal("20.00"), None)

        assert window.order_id is not None
        assert window.panel.due_value.text() == "48.40"

    def test_a_cashier_without_the_permission_cannot_discount(
        self, qtbot, db, menu, settings, monkeypatch
    ) -> None:
        """
        The dialog does not even open. The service would refuse too — this is
        the courtesy layer, not the rule.
        """
        limited = Session(
            user_id="u2",
            full_name_ar="متدرب",
            permissions=frozenset({"orders.create"}),
            started_at=datetime.now(UTC),
        )
        window = PosWindow(db, limited, settings)
        qtbot.addWidget(window)

        refusals = []
        monkeypatch.setattr(window, "_refuse", refusals.append)
        window._add(tile_named(window, "كابتشينو"))
        window._discount()

        assert refusals and "صلاحية" in refusals[0]

    def test_a_service_refusal_is_shown_as_written(self, window, monkeypatch) -> None:
        """
        The service messages are already in Arabic and already name the remedy.
        Rewording them here would produce two vocabularies for the same rule.
        """
        refusals = []
        monkeypatch.setattr(window, "_refuse", refusals.append)

        window._add(tile_named(window, "كابتشينو"))
        window._fire()
        window._fire()

        assert refusals == ["لا توجد أصناف جديدة لإرسالها"]

    def test_firing_moves_the_order_to_the_kitchen(self, window) -> None:
        window._add(tile_named(window, "كابتشينو"))
        window._fire()

        assert not window.panel.fire_button.isEnabled()

    def test_refreshing_the_catalog_picks_up_a_new_product(self, window, db) -> None:
        """A new product appears without restarting the terminal."""
        assert len(window.grid.visible_tiles) == 4

        db.upsert_mirror(
            "m_products",
            {"id": "p-new", "name_ar": "كرواسون", "category_id": "c-food", "payload": "{}"},
        )
        db.upsert_mirror(
            "m_variants",
            {
                "id": "v-new",
                "product_id": "p-new",
                "price": "40.00",
                "is_default": 1,
                "payload": "{}",
            },
        )
        window.refresh_catalog()

        assert len(window.grid.visible_tiles) == 5

    def test_every_action_leaves_the_outbox_growing(self, window, db) -> None:
        """The screen is a view over the outbox pattern, not a bypass of it."""
        from caesar_pos.local import outbox

        window._add(tile_named(window, "كابتشينو"))
        window._fire()

        # open + add + fire
        assert outbox.counts(db)["pending"] == 3
