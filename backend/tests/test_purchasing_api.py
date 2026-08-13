"""
The purchasing, supplier and recipe endpoints.

`test_purchasing.py` covers the domain rules through the services. This covers
the API surface over them, and the properties it asserts are the ones an HTTP
layer can break on its own:

  * the PO/GRN distinction survives being exposed as routes — the ordering
    endpoint moves no stock, and only posting a receipt does;
  * a document that has been posted cannot be edited back into a different
    shape than the ledger already describes;
  * the money-moving action is separately permissioned from the record-keeping
    one;
  * none of it is reachable across tenants.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from apps.catalog.models import Category, Product, ProductVariant
from apps.inventory.models import InventoryItem, StockLevel, Unit, UnitConversion
from apps.purchasing.models import GoodsReceipt, GRLine, POStatus, PurchaseOrder
from apps.recipes.models import Recipe, RecipeLine
from apps.suppliers.models import Supplier

pytestmark = pytest.mark.django_db


@pytest.fixture
def units(organization):
    kg = Unit.objects.create(organization=organization, code="KG", name_ar="كيلو")
    g = Unit.objects.create(organization=organization, code="G", name_ar="جرام")
    UnitConversion.objects.create(from_unit=kg, to_unit=g, factor=Decimal("1000"))
    return {"KG": kg, "G": g}


@pytest.fixture
def supplier(organization, branch):
    return Supplier.objects.create(
        organization=organization, branch=branch, name="مورد البن", phone="0100"
    )


@pytest.fixture
def beans(organization, branch, units):
    return InventoryItem.objects.create(
        organization=organization,
        branch=branch,
        code="BEANS",
        name_ar="بن",
        base_unit=units["G"],
        reorder_level=Decimal("500"),
        reorder_quantity=Decimal("2000"),
    )


@pytest.fixture
def manager(make_user):
    return make_user(role="BRANCH_MANAGER")


@pytest.fixture
def client(authed, manager, branch):
    return authed(manager, branch=branch)


def po_payload(supplier, beans, units, *, number="PO-001", quantity="5"):
    return {
        "supplier": str(supplier.id),
        "po_number": number,
        "expected_date": "2026-08-20",
        "lines": [
            {
                "item": str(beans.id),
                "unit": str(units["KG"].id),
                "quantity_ordered": quantity,
                "unit_price": "180.0000",
            }
        ],
    }


def grn_payload(supplier, beans, units, *, number="GRN-001", quantity="5", cost="180.0000"):
    return {
        "supplier": str(supplier.id),
        "grn_number": number,
        "supplier_invoice_no": "INV-9911",
        "received_date": "2026-08-08",
        "lines": [
            {
                "item": str(beans.id),
                "unit": str(units["KG"].id),
                "quantity_received": quantity,
                "unit_cost": cost,
            }
        ],
    }


# ── purchase orders ──────────────────────────────────────────────────────────


class TestPurchaseOrderEndpoint:
    def test_an_order_is_created_with_its_lines(self, client, supplier, beans, units) -> None:
        """
        Lines go with the header. An order without lines is not a smaller order,
        it is an invalid one — and a two-request create leaves that state
        reachable between the calls.
        """
        response = client.post(
            "/api/v1/purchasing/purchase-orders/",
            po_payload(supplier, beans, units),
            format="json",
        )

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["status"] == POStatus.DRAFT
        assert len(data["lines"]) == 1
        assert data["subtotal"] == "900.00"

    def test_creating_an_order_moves_no_stock(self, client, supplier, beans, units) -> None:
        """The rule the whole app exists for, asserted through HTTP."""
        client.post(
            "/api/v1/purchasing/purchase-orders/",
            po_payload(supplier, beans, units),
            format="json",
        )
        assert not StockLevel.objects.filter(item=beans, quantity_on_hand__gt=0).exists()

    def test_submitting_an_order_still_moves_no_stock(self, client, supplier, beans, units) -> None:
        created = client.post(
            "/api/v1/purchasing/purchase-orders/",
            po_payload(supplier, beans, units),
            format="json",
        ).json()["data"]

        response = client.post(f"/api/v1/purchasing/purchase-orders/{created['id']}/submit/")

        assert response.status_code == 200
        assert response.json()["data"]["status"] == POStatus.SUBMITTED
        assert not StockLevel.objects.filter(item=beans, quantity_on_hand__gt=0).exists()

    def test_an_empty_order_cannot_be_submitted(self, client, supplier) -> None:
        order = PurchaseOrder.objects.create(
            organization_id=supplier.organization_id,
            branch_id=supplier.branch_id,
            supplier=supplier,
            po_number="PO-EMPTY",
        )
        response = client.post(f"/api/v1/purchasing/purchase-orders/{order.id}/submit/")

        assert response.status_code == 400
        assert response.json()["code"] == "EMPTY_PURCHASE_ORDER"

    def test_a_submitted_order_is_not_editable(self, client, supplier, beans, units) -> None:
        """
        Once it has gone to the supplier, changing the quantities here would make
        the paperwork disagree with what was actually ordered.
        """
        created = client.post(
            "/api/v1/purchasing/purchase-orders/",
            po_payload(supplier, beans, units),
            format="json",
        ).json()["data"]
        client.post(f"/api/v1/purchasing/purchase-orders/{created['id']}/submit/")

        response = client.patch(
            f"/api/v1/purchasing/purchase-orders/{created['id']}/",
            {"notes": "غيّرت رأيي"},
            format="json",
        )

        assert response.status_code == 409
        assert response.json()["code"] == "PO_NOT_EDITABLE"

    def test_a_draft_order_is_editable(self, client, supplier, beans, units) -> None:
        created = client.post(
            "/api/v1/purchasing/purchase-orders/",
            po_payload(supplier, beans, units),
            format="json",
        ).json()["data"]

        response = client.patch(
            f"/api/v1/purchasing/purchase-orders/{created['id']}/",
            {"notes": "أضف كيس إضافي"},
            format="json",
        )

        assert response.status_code == 200
        assert response.json()["data"]["notes"] == "أضف كيس إضافي"

    def test_quantity_received_cannot_be_written_by_editing_the_order(
        self, client, supplier, beans, units
    ) -> None:
        """
        An order that could mark itself received would make the PO/GRN
        distinction decorative.
        """
        payload = po_payload(supplier, beans, units)
        payload["lines"][0]["quantity_received"] = "5"

        created = client.post("/api/v1/purchasing/purchase-orders/", payload, format="json").json()[
            "data"
        ]

        assert created["lines"][0]["quantity_received"] == "0.000"

    def test_a_submitted_order_cannot_be_deleted(self, client, supplier, beans, units) -> None:
        created = client.post(
            "/api/v1/purchasing/purchase-orders/",
            po_payload(supplier, beans, units),
            format="json",
        ).json()["data"]
        client.post(f"/api/v1/purchasing/purchase-orders/{created['id']}/submit/")

        response = client.delete(f"/api/v1/purchasing/purchase-orders/{created['id']}/")

        assert response.status_code == 409
        assert PurchaseOrder.objects.filter(id=created["id"]).exists()

    def test_a_partially_received_order_can_still_be_cancelled(
        self, client, supplier, beans, units
    ) -> None:
        """
        The delivered half stays on the shelf; the outstanding half stops being
        expected. Refusing would leave the order open forever.
        """
        created = client.post(
            "/api/v1/purchasing/purchase-orders/",
            po_payload(supplier, beans, units, quantity="10"),
            format="json",
        ).json()["data"]
        client.post(f"/api/v1/purchasing/purchase-orders/{created['id']}/submit/")

        order = PurchaseOrder.objects.get(id=created["id"])
        line = order.lines.first()
        receipt = GoodsReceipt.objects.create(
            organization=order.organization,
            branch=order.branch,
            purchase_order=order,
            supplier=supplier,
            grn_number="GRN-PART",
            received_date=date(2026, 8, 8),
        )
        GRLine.objects.create(
            receipt=receipt,
            po_line=line,
            item=beans,
            unit=units["KG"],
            quantity_received=Decimal("4"),
            unit_cost=Decimal("180"),
        )
        client.post(f"/api/v1/purchasing/receipts/{receipt.id}/post/")

        response = client.post(f"/api/v1/purchasing/purchase-orders/{created['id']}/cancel/")

        assert response.status_code == 200
        assert response.json()["data"]["status"] == POStatus.CANCELLED

    def test_a_fully_received_order_cannot_be_cancelled(
        self, client, supplier, beans, units
    ) -> None:
        order = PurchaseOrder.objects.create(
            organization_id=supplier.organization_id,
            branch_id=supplier.branch_id,
            supplier=supplier,
            po_number="PO-DONE",
            status=POStatus.RECEIVED,
        )
        response = client.post(f"/api/v1/purchasing/purchase-orders/{order.id}/cancel/")

        assert response.status_code == 409
        assert response.json()["code"] == "INVALID_PO_STATUS"


# ── goods receipts: the only thing here that touches stock ───────────────────


class TestGoodsReceiptEndpoint:
    def test_creating_a_receipt_does_not_move_stock_until_posted(
        self, client, supplier, beans, units
    ) -> None:
        """
        A receipt is a document until somebody posts it. Otherwise a typo in a
        draft is already a stock movement.
        """
        response = client.post(
            "/api/v1/purchasing/receipts/", grn_payload(supplier, beans, units), format="json"
        )

        assert response.status_code == 201
        assert response.json()["data"]["is_posted"] is False
        assert not StockLevel.objects.filter(item=beans, quantity_on_hand__gt=0).exists()

    def test_posting_a_receipt_raises_stock_and_bills_the_supplier(
        self, client, supplier, beans, units
    ) -> None:
        created = client.post(
            "/api/v1/purchasing/receipts/", grn_payload(supplier, beans, units), format="json"
        ).json()["data"]

        response = client.post(f"/api/v1/purchasing/receipts/{created['id']}/post/")

        assert response.status_code == 200
        assert response.json()["data"]["is_posted"] is True

        level = StockLevel.objects.get(item=beans)
        assert level.quantity_on_hand == Decimal("5000.000"), "5 kg, stored as grams"

        supplier.refresh_from_db()
        assert supplier.current_balance == Decimal("900.00")

    def test_the_cost_recorded_is_the_invoiced_one_not_the_ordered_one(
        self, client, supplier, beans, units
    ) -> None:
        """
        Receiving at the ACTUAL invoiced cost is what keeps weighted-average cost
        honest when a supplier raises prices between order and delivery.
        """
        created = client.post(
            "/api/v1/purchasing/purchase-orders/",
            po_payload(supplier, beans, units),
            format="json",
        ).json()["data"]
        client.post(f"/api/v1/purchasing/purchase-orders/{created['id']}/submit/")

        receipt = client.post(
            "/api/v1/purchasing/receipts/",
            {
                **grn_payload(supplier, beans, units, cost="200.0000"),
                "purchase_order": created["id"],
            },
            format="json",
        ).json()["data"]
        client.post(f"/api/v1/purchasing/receipts/{receipt['id']}/post/")

        level = StockLevel.objects.get(item=beans)
        assert level.weighted_avg_cost == Decimal("0.2000"), "200 per kg = 0.2 per gram"

        supplier.refresh_from_db()
        assert supplier.current_balance == Decimal("1000.00"), "billed at the invoice, not the PO"

    def test_posting_twice_is_refused(self, client, supplier, beans, units) -> None:
        """Not idempotent-silent: a second post would double the stock."""
        created = client.post(
            "/api/v1/purchasing/receipts/", grn_payload(supplier, beans, units), format="json"
        ).json()["data"]
        client.post(f"/api/v1/purchasing/receipts/{created['id']}/post/")

        response = client.post(f"/api/v1/purchasing/receipts/{created['id']}/post/")

        assert response.status_code == 409
        assert response.json()["code"] == "ALREADY_POSTED"
        assert StockLevel.objects.get(item=beans).quantity_on_hand == Decimal("5000.000")

    def test_a_posted_receipt_cannot_be_edited(self, client, supplier, beans, units) -> None:
        """
        Its lines are already stock movements and a supplier invoice. Editing
        them would leave the ledger describing a delivery that no longer matches
        the document it came from.
        """
        created = client.post(
            "/api/v1/purchasing/receipts/", grn_payload(supplier, beans, units), format="json"
        ).json()["data"]
        client.post(f"/api/v1/purchasing/receipts/{created['id']}/post/")

        response = client.patch(
            f"/api/v1/purchasing/receipts/{created['id']}/",
            {"supplier_invoice_no": "INV-DIFFERENT"},
            format="json",
        )

        assert response.status_code == 409
        assert response.json()["code"] == "ALREADY_POSTED"

    def test_a_posted_receipt_cannot_be_deleted(self, client, supplier, beans, units) -> None:
        created = client.post(
            "/api/v1/purchasing/receipts/", grn_payload(supplier, beans, units), format="json"
        ).json()["data"]
        client.post(f"/api/v1/purchasing/receipts/{created['id']}/post/")

        response = client.delete(f"/api/v1/purchasing/receipts/{created['id']}/")

        assert response.status_code == 409
        assert GoodsReceipt.objects.filter(id=created["id"]).exists()

    def test_a_receipt_without_a_purchase_order_is_allowed(
        self, client, supplier, beans, units
    ) -> None:
        """Cafes buy milk from the shop down the road, and that has to record."""
        created = client.post(
            "/api/v1/purchasing/receipts/", grn_payload(supplier, beans, units), format="json"
        ).json()["data"]

        assert created["purchase_order"] is None
        assert client.post(f"/api/v1/purchasing/receipts/{created['id']}/post/").status_code == 200

    def test_an_empty_receipt_is_refused(self, client, supplier) -> None:
        receipt = GoodsReceipt.objects.create(
            organization_id=supplier.organization_id,
            branch_id=supplier.branch_id,
            supplier=supplier,
            grn_number="GRN-EMPTY",
            received_date=date(2026, 8, 8),
        )
        response = client.post(f"/api/v1/purchasing/receipts/{receipt.id}/post/")

        assert response.status_code == 400
        assert response.json()["code"] == "EMPTY_RECEIPT"


# ── suppliers ────────────────────────────────────────────────────────────────


class TestSupplierEndpoint:
    def test_the_balance_cannot_be_written_directly(self, client, organization, branch) -> None:
        """
        A settable balance would let a typo erase a debt with no ledger entry to
        explain it — the same discipline `StockLevel` follows.
        """
        response = client.post(
            "/api/v1/suppliers/",
            {"name": "مورد اللبن", "current_balance": "-5000.00"},
            format="json",
        )

        assert response.status_code == 201
        assert response.json()["data"]["current_balance"] == "0.00"

    def test_the_statement_lists_every_entry_and_proves_it_adds_up(
        self, client, supplier, beans, units
    ) -> None:
        created = client.post(
            "/api/v1/purchasing/receipts/", grn_payload(supplier, beans, units), format="json"
        ).json()["data"]
        client.post(f"/api/v1/purchasing/receipts/{created['id']}/post/")

        response = client.get(f"/api/v1/suppliers/{supplier.id}/statement/")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["current_balance"] == "900.00"
        assert data["drift"] == "0.00", "a non-zero drift is a bug in a write path"
        assert len(data["entries"]) == 1
        assert data["entries"][0]["entry_type"] == "INVOICE"

    def test_paying_a_supplier_reduces_what_we_owe(self, client, supplier, beans, units) -> None:
        created = client.post(
            "/api/v1/purchasing/receipts/", grn_payload(supplier, beans, units), format="json"
        ).json()["data"]
        client.post(f"/api/v1/purchasing/receipts/{created['id']}/post/")

        response = client.post(
            f"/api/v1/suppliers/{supplier.id}/pay/",
            {"amount": "400.00", "reference": "شيك 44"},
            format="json",
        )

        assert response.status_code == 200
        assert response.json()["data"]["current_balance"] == "500.00"

    def test_a_zero_payment_is_refused(self, client, supplier) -> None:
        response = client.post(
            f"/api/v1/suppliers/{supplier.id}/pay/", {"amount": "0"}, format="json"
        )
        assert response.status_code == 400

    def test_paying_needs_its_own_permission(self, authed, make_user, branch, supplier) -> None:
        """
        Keeping a supplier's phone number current and moving money out of the
        business are not the same act.

        The store manager holds `purchasing.manage_suppliers` and can do all the
        record-keeping; paying is the owner's, and the role catalogue draws that
        line deliberately.
        """
        from apps.authz import catalog

        held = set(catalog.SYSTEM_ROLES["INVENTORY_MANAGER"]["permissions"])
        assert "purchasing.manage_suppliers" in held
        assert "purchasing.pay_supplier" not in held, "the split this test exists for"

        clerk = authed(
            make_user(email="clerk@caesar.test", role="INVENTORY_MANAGER"), branch=branch
        )

        assert (
            clerk.patch(
                f"/api/v1/suppliers/{supplier.id}/", {"phone": "0111"}, format="json"
            ).status_code
            == 200
        ), "record-keeping is allowed"

        response = clerk.post(
            f"/api/v1/suppliers/{supplier.id}/pay/", {"amount": "10.00"}, format="json"
        )
        assert response.status_code == 403
        supplier.refresh_from_db()
        assert supplier.current_balance == Decimal("0.00")


# ── recipes ──────────────────────────────────────────────────────────────────


@pytest.fixture
def cappuccino(organization, branch, units):
    category = Category.objects.create(organization=organization, branch=branch, name_ar="قهوة")
    product = Product.objects.create(
        organization=organization,
        branch=branch,
        category=category,
        sku="CAPP",
        name_ar="كابتشينو",
    )
    return ProductVariant.objects.create(
        product=product, sku="CAPP-M", price=Decimal("60.00"), is_default=True
    )


class TestRecipeEndpoint:
    def test_a_recipe_is_created_with_its_lines(self, client, cappuccino, beans, units) -> None:
        response = client.post(
            "/api/v1/recipes/",
            {
                "variant": str(cappuccino.id),
                "yield_quantity": "1",
                "lines": [
                    {
                        "item": str(beans.id),
                        "unit": str(units["G"].id),
                        "quantity": "18",
                        "waste_percent": "5",
                    }
                ],
            },
            format="json",
        )

        assert response.status_code == 201
        data = response.json()["data"]
        assert len(data["lines"]) == 1
        assert data["lines"][0]["effective_quantity"] == "18.900", "18g plus 5% shrinkage"

    def test_saving_a_recipe_costs_the_variant_immediately(
        self, client, cappuccino, beans, units
    ) -> None:
        """
        Without this the product still shows the margin it had before the
        ingredients existed.
        """
        client.post(
            "/api/v1/recipes/",
            {
                "variant": str(cappuccino.id),
                "lines": [{"item": str(beans.id), "unit": str(units["G"].id), "quantity": "18"}],
            },
            format="json",
        )

        cappuccino.refresh_from_db()
        assert cappuccino.cost == Decimal("0.00"), "no receipts yet, so the ingredient has no cost"

    def test_the_cost_endpoint_names_what_it_could_not_price(
        self, client, cappuccino, beans, units
    ) -> None:
        """
        A margin that looks excellent because an ingredient is silently
        contributing zero is worse than no margin at all.
        """
        recipe = Recipe.objects.create(variant=cappuccino)
        RecipeLine.objects.create(
            recipe=recipe, item=beans, unit=units["G"], quantity=Decimal("18")
        )

        response = client.get(f"/api/v1/recipes/{recipe.id}/cost/")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["missing_costs"] == ["BEANS"]
        assert data["total"] == "0.00"

    def test_the_cost_endpoint_reports_a_real_margin(
        self, client, cappuccino, beans, units, supplier
    ) -> None:
        created = client.post(
            "/api/v1/purchasing/receipts/", grn_payload(supplier, beans, units), format="json"
        ).json()["data"]
        client.post(f"/api/v1/purchasing/receipts/{created['id']}/post/")

        recipe = Recipe.objects.create(variant=cappuccino)
        RecipeLine.objects.create(
            recipe=recipe, item=beans, unit=units["G"], quantity=Decimal("18")
        )

        data = client.get(f"/api/v1/recipes/{recipe.id}/cost/").json()["data"]

        assert data["missing_costs"] == []
        assert data["total"] == "3.24", "18 g at 0.18 per g — 180 per kg over 1000 g"
        assert data["price"] == "60.00"
        assert data["margin"] == "56.76"

    def test_a_goods_receipt_recosts_every_recipe_using_the_ingredient(
        self, client, cappuccino, beans, units, supplier
    ) -> None:
        """A new coffee price changes the margin on every drink containing coffee."""
        recipe = Recipe.objects.create(variant=cappuccino)
        RecipeLine.objects.create(
            recipe=recipe, item=beans, unit=units["G"], quantity=Decimal("18")
        )

        created = client.post(
            "/api/v1/purchasing/receipts/", grn_payload(supplier, beans, units), format="json"
        ).json()["data"]
        client.post(f"/api/v1/purchasing/receipts/{created['id']}/post/")

        cappuccino.refresh_from_db()
        assert cappuccino.cost == Decimal("3.24")

    def test_the_variant_lookup_finds_a_recipe(self, client, cappuccino, beans, units) -> None:
        recipe = Recipe.objects.create(variant=cappuccino)
        RecipeLine.objects.create(
            recipe=recipe, item=beans, unit=units["G"], quantity=Decimal("18")
        )

        response = client.get(f"/api/v1/recipes/for-variant/{cappuccino.id}/")

        assert response.status_code == 200
        assert response.json()["data"]["id"] == str(recipe.id)

    def test_a_variant_with_no_recipe_says_so(self, client, cappuccino) -> None:
        response = client.get(f"/api/v1/recipes/for-variant/{cappuccino.id}/")

        assert response.status_code == 404
        assert response.json()["code"] == "RECIPE_NOT_FOUND"


# ── planning endpoints ───────────────────────────────────────────────────────


class TestPlanningEndpoints:
    def test_reorder_suggestions_name_what_is_low(self, client, beans, units, supplier) -> None:
        StockLevel.objects.update_or_create(
            item=beans, defaults={"quantity_on_hand": Decimal("100")}
        )

        response = client.get("/api/v1/purchasing/reorder-suggestions/")

        assert response.status_code == 200
        rows = response.json()["data"]
        assert [row["item_code"] for row in rows] == ["BEANS"]
        assert rows[0]["suggested_quantity"] == "2000.000"

    def test_a_well_stocked_item_is_not_suggested(self, client, beans) -> None:
        StockLevel.objects.update_or_create(
            item=beans, defaults={"quantity_on_hand": Decimal("9000")}
        )
        assert client.get("/api/v1/purchasing/reorder-suggestions/").json()["data"] == []

    def test_valuation_totals_stock_at_weighted_average_cost(
        self, client, supplier, beans, units
    ) -> None:
        created = client.post(
            "/api/v1/purchasing/receipts/", grn_payload(supplier, beans, units), format="json"
        ).json()["data"]
        client.post(f"/api/v1/purchasing/receipts/{created['id']}/post/")

        response = client.get("/api/v1/purchasing/valuation/")

        assert response.status_code == 200
        assert response.json()["data"]["total"] == "900.00"

    def test_outstanding_lists_only_what_has_not_arrived(
        self, client, supplier, beans, units
    ) -> None:
        """The screen an owner checks before ordering the same thing twice."""
        draft = client.post(
            "/api/v1/purchasing/purchase-orders/",
            po_payload(supplier, beans, units, number="PO-DRAFT"),
            format="json",
        ).json()["data"]

        submitted = client.post(
            "/api/v1/purchasing/purchase-orders/",
            po_payload(supplier, beans, units, number="PO-SENT"),
            format="json",
        ).json()["data"]
        client.post(f"/api/v1/purchasing/purchase-orders/{submitted['id']}/submit/")

        rows = client.get("/api/v1/purchasing/outstanding/").json()["data"]

        numbers = [row["po_number"] for row in rows]
        assert numbers == ["PO-SENT"]
        assert draft["po_number"] not in numbers, "a draft has not been sent to anyone"


# ── tenancy ──────────────────────────────────────────────────────────────────


class TestCrossTenantIsolation:
    """Threat I1, on the routes this batch adds."""

    @pytest.fixture
    def foreign_supplier(self, other_organization, other_branch):
        return Supplier.objects.create(
            organization=other_organization, branch=other_branch, name="مورد آخر"
        )

    def test_another_tenants_supplier_is_not_listed(self, client, foreign_supplier) -> None:
        names = [row["name"] for row in client.get("/api/v1/suppliers/").json()["data"]]
        assert foreign_supplier.name not in names

    def test_another_tenants_supplier_is_not_readable(self, client, foreign_supplier) -> None:
        assert client.get(f"/api/v1/suppliers/{foreign_supplier.id}/").status_code == 404

    def test_another_tenants_supplier_cannot_be_paid(self, client, foreign_supplier) -> None:
        response = client.post(
            f"/api/v1/suppliers/{foreign_supplier.id}/pay/", {"amount": "100.00"}, format="json"
        )
        assert response.status_code == 404
        foreign_supplier.refresh_from_db()
        assert foreign_supplier.current_balance == Decimal("0.00")

    def test_another_tenants_purchase_order_is_not_readable(self, client, foreign_supplier) -> None:
        order = PurchaseOrder.objects.create(
            organization=foreign_supplier.organization,
            branch=foreign_supplier.branch,
            supplier=foreign_supplier,
            po_number="PO-FOREIGN",
        )
        assert client.get(f"/api/v1/purchasing/purchase-orders/{order.id}/").status_code == 404

    def test_another_tenants_receipt_cannot_be_posted(self, client, foreign_supplier) -> None:
        receipt = GoodsReceipt.objects.create(
            organization=foreign_supplier.organization,
            branch=foreign_supplier.branch,
            supplier=foreign_supplier,
            grn_number="GRN-FOREIGN",
            received_date=date(2026, 8, 8),
        )
        assert client.post(f"/api/v1/purchasing/receipts/{receipt.id}/post/").status_code == 404
        receipt.refresh_from_db()
        assert receipt.posted_at is None
