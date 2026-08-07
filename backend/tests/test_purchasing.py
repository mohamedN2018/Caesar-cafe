"""
Purchasing, recipes and costing.

The rule under test throughout: a purchase order is an intention and moves no
stock; a goods receipt is a fact and moves everything.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from apps.catalog.models import Category, Product, ProductVariant
from apps.inventory import services as inventory
from apps.inventory.models import (
    InventoryItem,
    MovementType,
    StockLevel,
    StockMovement,
    Unit,
    UnitConversion,
)
from apps.purchasing import services as purchasing
from apps.purchasing.models import (
    GoodsReceipt,
    GRLine,
    POLine,
    POStatus,
    PurchaseOrder,
    PurchaseReturn,
    PurchaseReturnLine,
)
from apps.recipes import services as recipes
from apps.recipes.models import Recipe, RecipeLine
from apps.suppliers import services as supplier_services
from apps.suppliers.models import LedgerEntryType, Supplier

pytestmark = pytest.mark.django_db


@pytest.fixture
def units(organization):
    kg = Unit.objects.create(organization=organization, code="KG", name_ar="كيلو")
    g = Unit.objects.create(organization=organization, code="G", name_ar="جرام")
    litre = Unit.objects.create(organization=organization, code="L", name_ar="لتر")
    ml = Unit.objects.create(organization=organization, code="ML", name_ar="مللي")
    UnitConversion.objects.create(from_unit=kg, to_unit=g, factor=Decimal("1000"))
    UnitConversion.objects.create(from_unit=litre, to_unit=ml, factor=Decimal("1000"))
    return {"KG": kg, "G": g, "L": litre, "ML": ml}


@pytest.fixture
def supplier(organization, branch):
    return Supplier.objects.create(organization=organization, branch=branch, name="مورد البن")


@pytest.fixture
def beans(organization, branch, units):
    return InventoryItem.objects.create(
        organization=organization, branch=branch, code="BEANS", name_ar="بن", base_unit=units["G"]
    )


@pytest.fixture
def milk(organization, branch, units):
    return InventoryItem.objects.create(
        organization=organization, branch=branch, code="MILK", name_ar="لبن", base_unit=units["ML"]
    )


@pytest.fixture
def cappuccino(organization, branch, beans, milk, units):
    category = Category.objects.create(organization=organization, branch=branch, name_ar="قهوة")
    product = Product.objects.create(
        organization=organization,
        branch=branch,
        category=category,
        sku="CAPP",
        name_ar="كابتشينو",
    )
    variant = ProductVariant.objects.create(
        product=product, sku="CAPP-M", price=Decimal("60.00"), is_default=True
    )
    recipe = Recipe.objects.create(variant=variant)
    RecipeLine.objects.create(recipe=recipe, item=beans, quantity=Decimal("18"), unit=units["G"])
    RecipeLine.objects.create(recipe=recipe, item=milk, quantity=Decimal("150"), unit=units["ML"])
    return variant


class TestPurchaseOrderMovesNoStock:
    def test_submitting_a_po_leaves_stock_untouched(
        self, organization, branch, supplier, beans, units
    ) -> None:
        """docs/02 §25 — the rule this whole app exists to enforce."""
        po = PurchaseOrder.objects.create(
            organization=organization, branch=branch, supplier=supplier, po_number="PO-001"
        )
        POLine.objects.create(
            purchase_order=po,
            item=beans,
            unit=units["KG"],
            quantity_ordered=Decimal("5"),
            unit_price=Decimal("350"),
        )

        purchasing.submit_purchase_order(po)
        po.refresh_from_db()

        assert po.status == POStatus.SUBMITTED
        assert not StockMovement.objects.filter(item=beans).exists()
        assert not StockLevel.objects.filter(item=beans, quantity_on_hand__gt=0).exists()
        assert supplier.ledger.count() == 0

    def test_an_empty_po_cannot_be_submitted(self, organization, branch, supplier) -> None:
        from apps.core.exceptions import AppError

        po = PurchaseOrder.objects.create(
            organization=organization, branch=branch, supplier=supplier, po_number="PO-002"
        )
        with pytest.raises(AppError):
            purchasing.submit_purchase_order(po)

    def test_a_submitted_po_cannot_be_submitted_again(
        self, organization, branch, supplier, beans, units
    ) -> None:
        from apps.core.exceptions import AppError

        po = PurchaseOrder.objects.create(
            organization=organization, branch=branch, supplier=supplier, po_number="PO-003"
        )
        POLine.objects.create(
            purchase_order=po,
            item=beans,
            unit=units["KG"],
            quantity_ordered=Decimal("5"),
            unit_price=Decimal("350"),
        )
        purchasing.submit_purchase_order(po)
        with pytest.raises(AppError):
            purchasing.submit_purchase_order(po)


class TestGoodsReceipt:
    def _receipt(self, organization, branch, supplier, item, unit, quantity, cost, po=None):
        receipt = GoodsReceipt.objects.create(
            organization=organization,
            branch=branch,
            supplier=supplier,
            purchase_order=po,
            grn_number=f"GRN-{item.code}-{quantity}",
            received_date=date(2026, 8, 7),
        )
        GRLine.objects.create(
            receipt=receipt,
            item=item,
            unit=unit,
            quantity_received=Decimal(quantity),
            unit_cost=Decimal(cost),
        )
        return receipt

    def test_receiving_raises_stock_and_bills_the_supplier(
        self, organization, branch, supplier, beans, units
    ) -> None:
        receipt = self._receipt(organization, branch, supplier, beans, units["KG"], "5", "350")
        purchasing.post_receipt(receipt)

        level = StockLevel.objects.get(item=beans)
        assert level.quantity_on_hand == Decimal("5000.000")  # 5 KG in grams

        supplier.refresh_from_db()
        assert supplier.current_balance == Decimal("1750.00")  # 5 × 350
        assert supplier.ledger.filter(entry_type=LedgerEntryType.INVOICE).exists()

    def test_unit_cost_is_converted_to_the_base_unit(
        self, organization, branch, supplier, beans, units
    ) -> None:
        """
        A 5kg sack at 350/kg is 0.35 per gram. Recording 350 per gram would
        overstate stock value by a factor of a thousand.
        """
        receipt = self._receipt(organization, branch, supplier, beans, units["KG"], "5", "350")
        purchasing.post_receipt(receipt)

        assert StockLevel.objects.get(item=beans).weighted_avg_cost == Decimal("0.3500")

    def test_posting_twice_is_refused(self, organization, branch, supplier, beans, units) -> None:
        receipt = self._receipt(organization, branch, supplier, beans, units["KG"], "5", "350")
        purchasing.post_receipt(receipt)
        with pytest.raises(purchasing.AlreadyPosted):
            purchasing.post_receipt(receipt)

    def test_receiving_against_a_po_advances_its_status(
        self, organization, branch, supplier, beans, units
    ) -> None:
        po = PurchaseOrder.objects.create(
            organization=organization, branch=branch, supplier=supplier, po_number="PO-010"
        )
        line = POLine.objects.create(
            purchase_order=po,
            item=beans,
            unit=units["KG"],
            quantity_ordered=Decimal("10"),
            unit_price=Decimal("350"),
        )
        purchasing.submit_purchase_order(po)

        receipt = GoodsReceipt.objects.create(
            organization=organization,
            branch=branch,
            supplier=supplier,
            purchase_order=po,
            grn_number="GRN-P1",
            received_date=date(2026, 8, 7),
        )
        GRLine.objects.create(
            receipt=receipt,
            po_line=line,
            item=beans,
            unit=units["KG"],
            quantity_received=Decimal("4"),
            unit_cost=Decimal("350"),
        )
        purchasing.post_receipt(receipt)

        po.refresh_from_db()
        line.refresh_from_db()
        assert po.status == POStatus.PARTIAL
        assert line.quantity_received == Decimal("4.000")
        assert line.outstanding == Decimal("6.000")

    def test_full_receipt_closes_the_po(self, organization, branch, supplier, beans, units) -> None:
        po = PurchaseOrder.objects.create(
            organization=organization, branch=branch, supplier=supplier, po_number="PO-011"
        )
        line = POLine.objects.create(
            purchase_order=po,
            item=beans,
            unit=units["KG"],
            quantity_ordered=Decimal("10"),
            unit_price=Decimal("350"),
        )
        purchasing.submit_purchase_order(po)

        receipt = GoodsReceipt.objects.create(
            organization=organization,
            branch=branch,
            supplier=supplier,
            purchase_order=po,
            grn_number="GRN-F1",
            received_date=date(2026, 8, 7),
        )
        GRLine.objects.create(
            receipt=receipt,
            po_line=line,
            item=beans,
            unit=units["KG"],
            quantity_received=Decimal("10"),
            unit_cost=Decimal("360"),  # the supplier raised the price
        )
        purchasing.post_receipt(receipt)

        po.refresh_from_db()
        assert po.status == POStatus.RECEIVED
        # Received at the ACTUAL invoiced price, not the ordered one.
        assert StockLevel.objects.get(item=beans).weighted_avg_cost == Decimal("0.3600")

    def test_a_receipt_needs_no_purchase_order(
        self, organization, branch, supplier, milk, units
    ) -> None:
        """Cafes buy milk from the shop down the road when they run out."""
        receipt = self._receipt(organization, branch, supplier, milk, units["L"], "4", "25")
        purchasing.post_receipt(receipt)
        assert StockLevel.objects.get(item=milk).quantity_on_hand == Decimal("4000.000")


class TestPurchaseReturn:
    def test_returning_reduces_stock_and_the_balance(
        self, organization, branch, supplier, beans, units
    ) -> None:
        receipt = GoodsReceipt.objects.create(
            organization=organization,
            branch=branch,
            supplier=supplier,
            grn_number="GRN-R1",
            received_date=date(2026, 8, 7),
        )
        GRLine.objects.create(
            receipt=receipt,
            item=beans,
            unit=units["KG"],
            quantity_received=Decimal("5"),
            unit_cost=Decimal("350"),
        )
        purchasing.post_receipt(receipt)

        ret = PurchaseReturn.objects.create(
            organization=organization,
            branch=branch,
            supplier=supplier,
            receipt=receipt,
            reference="RET-1",
            reason="بن فاسد",
            returned_date=date(2026, 8, 8),
        )
        PurchaseReturnLine.objects.create(
            purchase_return=ret,
            item=beans,
            unit=units["KG"],
            quantity=Decimal("2"),
            unit_cost=Decimal("350"),
        )
        purchasing.post_return(ret)

        assert StockLevel.objects.get(item=beans).quantity_on_hand == Decimal("3000.000")
        supplier.refresh_from_db()
        assert supplier.current_balance == Decimal("1050.00")  # 1750 − 700


class TestSupplierLedger:
    def test_payment_reduces_what_we_owe(self, supplier) -> None:
        supplier_services.record_ledger_entry(
            supplier=supplier, entry_type=LedgerEntryType.INVOICE, amount=Decimal("1000")
        )
        supplier_services.record_payment(supplier=supplier, amount=Decimal("400"))

        supplier.refresh_from_db()
        assert supplier.current_balance == Decimal("600.00")

    def test_the_balance_matches_a_ledger_replay(self, supplier) -> None:
        for amount in (Decimal("1000"), Decimal("-250"), Decimal("500")):
            supplier_services.record_ledger_entry(
                supplier=supplier, entry_type=LedgerEntryType.ADJUSTMENT, amount=amount
            )
        assert supplier_services.reconcile(supplier) == Decimal("0.00")

    def test_a_zero_payment_is_refused(self, supplier) -> None:
        from apps.core.exceptions import AppError

        with pytest.raises(AppError):
            supplier_services.record_payment(supplier=supplier, amount=Decimal("0"))


class TestRecipeCosting:
    def test_cost_rolls_up_from_ingredients(
        self, organization, branch, supplier, beans, milk, cappuccino, units
    ) -> None:
        """
        18g beans @ 0.35 = 6.30
        150ml milk @ 0.025 = 3.75
        ------------------------
                            10.05
        """
        inventory.set_opening_balance(
            item=beans, quantity=Decimal("10000"), unit_cost=Decimal("0.35")
        )
        inventory.set_opening_balance(
            item=milk, quantity=Decimal("20000"), unit_cost=Decimal("0.025")
        )

        cost = recipes.compute_cost(cappuccino.recipe)
        assert cost.total == Decimal("10.05")
        assert cost.missing_costs == ()

    def test_missing_ingredient_costs_are_reported_not_hidden(
        self, beans, milk, cappuccino
    ) -> None:
        """A margin that looks great because an ingredient is missing is worse
        than no margin at all."""
        inventory.set_opening_balance(
            item=beans, quantity=Decimal("1000"), unit_cost=Decimal("0.35")
        )
        cost = recipes.compute_cost(cappuccino.recipe)
        assert "MILK" in cost.missing_costs

    def test_waste_percent_raises_the_cost(self, beans, milk, cappuccino) -> None:
        inventory.set_opening_balance(
            item=beans, quantity=Decimal("10000"), unit_cost=Decimal("0.35")
        )
        inventory.set_opening_balance(
            item=milk, quantity=Decimal("20000"), unit_cost=Decimal("0.025")
        )
        base = recipes.compute_cost(cappuccino.recipe).total

        line = cappuccino.recipe.lines.get(item=beans)
        line.waste_percent = Decimal("10")
        line.save()

        assert recipes.compute_cost(cappuccino.recipe).total > base

    def test_a_receipt_refreshes_every_affected_variant_cost(
        self, organization, branch, supplier, beans, milk, cappuccino, units
    ) -> None:
        """A new coffee price changes the margin on every drink with coffee in it."""
        inventory.set_opening_balance(
            item=milk, quantity=Decimal("20000"), unit_cost=Decimal("0.025")
        )

        receipt = GoodsReceipt.objects.create(
            organization=organization,
            branch=branch,
            supplier=supplier,
            grn_number="GRN-C1",
            received_date=date(2026, 8, 7),
        )
        GRLine.objects.create(
            receipt=receipt,
            item=beans,
            unit=units["KG"],
            quantity_received=Decimal("5"),
            unit_cost=Decimal("350"),
        )
        purchasing.post_receipt(receipt)

        cappuccino.refresh_from_db()
        assert cappuccino.cost == Decimal("10.05")
        assert cappuccino.margin == Decimal("49.95")

    def test_margin_percent(self, cappuccino) -> None:
        cappuccino.cost = Decimal("15.00")
        assert cappuccino.margin_percent == Decimal("75.00")


class TestSaleConsumption:
    def test_selling_deducts_the_recipe(self, beans, milk, cappuccino) -> None:
        inventory.set_opening_balance(
            item=beans, quantity=Decimal("1000"), unit_cost=Decimal("0.35")
        )
        inventory.set_opening_balance(
            item=milk, quantity=Decimal("5000"), unit_cost=Decimal("0.025")
        )

        recipes.consume_for_sale(variant=cappuccino, quantity=Decimal("2"))

        assert StockLevel.objects.get(item=beans).quantity_on_hand == Decimal("964.000")
        assert StockLevel.objects.get(item=milk).quantity_on_hand == Decimal("4700.000")

    def test_movements_are_typed_as_sales(self, beans, milk, cappuccino) -> None:
        inventory.set_opening_balance(
            item=beans, quantity=Decimal("1000"), unit_cost=Decimal("0.35")
        )
        inventory.set_opening_balance(
            item=milk, quantity=Decimal("5000"), unit_cost=Decimal("0.025")
        )
        recipes.consume_for_sale(variant=cappuccino, quantity=Decimal("1"))

        assert StockMovement.objects.filter(movement_type=MovementType.SALE).count() == 2

    def test_a_product_without_a_recipe_consumes_nothing(self, organization, branch) -> None:
        category = Category.objects.create(organization=organization, branch=branch, name_ar="مياه")
        product = Product.objects.create(
            organization=organization,
            branch=branch,
            category=category,
            sku="WATER",
            name_ar="مياه معدنية",
            track_inventory=False,
        )
        variant = ProductVariant.objects.create(
            product=product, sku="WATER-S", price=Decimal("15.00"), is_default=True
        )
        assert recipes.consume_for_sale(variant=variant, quantity=Decimal("3")) == []

    def test_batch_recipes_divide_per_portion(self, beans, milk, cappuccino) -> None:
        inventory.set_opening_balance(
            item=beans, quantity=Decimal("10000"), unit_cost=Decimal("0.35")
        )
        inventory.set_opening_balance(
            item=milk, quantity=Decimal("50000"), unit_cost=Decimal("0.025")
        )
        recipe = cappuccino.recipe
        recipe.yield_quantity = Decimal("10")
        recipe.save()

        recipes.consume_for_sale(variant=cappuccino, quantity=Decimal("1"))
        assert StockLevel.objects.get(item=beans).quantity_on_hand == Decimal("9998.200")


class TestReorderAndValuation:
    def test_reorder_suggestions_surface_low_items(self, branch, beans, supplier) -> None:
        beans.reorder_level = Decimal("2000")
        beans.reorder_quantity = Decimal("5000")
        beans.default_supplier = supplier
        beans.save()
        inventory.set_opening_balance(
            item=beans, quantity=Decimal("500"), unit_cost=Decimal("0.35")
        )

        suggestions = purchasing.reorder_suggestions(branch)
        assert len(suggestions) == 1
        assert suggestions[0]["item_code"] == "BEANS"
        assert suggestions[0]["suggested_quantity"] == Decimal("5000.000")
        assert suggestions[0]["supplier"] == "مورد البن"

    def test_a_healthy_item_is_not_suggested(self, branch, beans) -> None:
        beans.reorder_level = Decimal("2000")
        beans.save()
        inventory.set_opening_balance(
            item=beans, quantity=Decimal("9000"), unit_cost=Decimal("0.35")
        )
        assert purchasing.reorder_suggestions(branch) == []

    def test_valuation_totals_stock_at_average_cost(self, branch, beans, milk) -> None:
        inventory.set_opening_balance(
            item=beans, quantity=Decimal("1000"), unit_cost=Decimal("0.35")
        )
        inventory.set_opening_balance(
            item=milk, quantity=Decimal("2000"), unit_cost=Decimal("0.025")
        )
        assert purchasing.valuation(branch)["total"] == Decimal("400.00")
