"""
Three permission codes the matrix promised and nothing checked.

`docs/05` told an owner that a waiter cannot leave a bill half-paid, that a
cashier's reprint is a tracked event, and that walking out with a CSV of the
day's takings is not the same act as reading them on a screen. None of the three
was enforced: `orders.reprint`, `payments.split` and `reports.export` were codes
in the catalogue that no route consulted.

A permission matrix describing rules the product does not have is worse than no
matrix, because somebody staffs the cafe on the strength of it.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from apps.catalog.models import Category, Product, ProductVariant
from apps.payments.models import PaymentMethod

pytestmark = pytest.mark.django_db


@pytest.fixture
def variant(organization, branch):
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


@pytest.fixture
def cash(organization, branch):
    return PaymentMethod.objects.create(
        organization=organization,
        branch=branch,
        code="CASH",
        name_ar="نقدي",
        counts_as_cash=True,
    )


def pos_client(authed, user, branch):
    client = authed(user, branch=branch, kind="POS", device_id=uuid.uuid4())
    response = client.post("/api/v1/shifts/open/", {"opening_cash": "500.00"}, format="json")
    assert response.status_code == 201, response.json()
    return client


def open_order_with_item(client, variant):
    order = client.post("/api/v1/orders/", {"order_type": "DINE_IN"}, format="json").json()["data"]
    client.post(
        f"/api/v1/orders/{order['id']}/events/",
        {
            "events": [
                {
                    "id": str(uuid.uuid4()),
                    "type": "ITEM_ADDED",
                    "payload": {
                        "line_id": str(uuid.uuid4()),
                        "variant_id": str(variant.id),
                        "quantity": "1",
                    },
                }
            ]
        },
        format="json",
    )
    return client.get(f"/api/v1/orders/{order['id']}/").json()["data"]


# ── payments.split ───────────────────────────────────────────────────────────


class TestSplitPayment:
    """
    Paying less than the balance IS a split payment — the second tender is the
    rest of it. A half-paid order is the state a walk-out hides in, so leaving
    one behind is a capability, not a side effect of `payments.take`.
    """

    def test_a_cashier_can_take_a_partial_payment(
        self, authed, make_user, branch, variant, cash
    ) -> None:
        from apps.authz import catalog

        assert "payments.split" in catalog.SYSTEM_ROLES["CASHIER"]["permissions"]

        client = pos_client(authed, make_user(role="CASHIER", branch=branch), branch)
        order = open_order_with_item(client, variant)

        response = client.post(
            "/api/v1/payments/",
            {"order": order["id"], "method": str(cash.id), "amount": "20.00"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )

        assert response.status_code == 201, response.json()
        assert Decimal(response.json()["data"]["order"]["balance_due"]) > 0

    def test_taking_money_does_not_imply_leaving_a_balance(
        self, authed, make_user, branch, variant, cash
    ) -> None:
        """
        The distinction the code exists for: someone who may settle a bill is not
        thereby allowed to leave one half-paid.
        """
        from apps.authz import catalog
        from apps.authz.models import Role

        user = make_user(role="CASHIER", branch=branch)
        role = Role.objects.get(organization=user.organization, code="CASHIER")
        role.set_permissions(
            sorted(set(catalog.SYSTEM_ROLES["CASHIER"]["permissions"]) - {"payments.split"})
        )
        assert "payments.take" in role.permission_codes, "still allowed to settle"

        client = pos_client(authed, user, branch)
        order = open_order_with_item(client, variant)

        response = client.post(
            "/api/v1/payments/",
            {"order": order["id"], "method": str(cash.id), "amount": "20.00"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )

        assert response.status_code == 403
        assert "payments.split" in response.json()["message"]

    def test_paying_in_full_never_needs_the_split_permission(
        self, authed, make_user, branch, variant, cash
    ) -> None:
        """Settling a bill is the ordinary act; it must not require the extra code."""
        from apps.authz import catalog
        from apps.authz.models import Role

        user = make_user(role="CASHIER", branch=branch)
        role = Role.objects.get(organization=user.organization, code="CASHIER")
        role.set_permissions(
            sorted(set(catalog.SYSTEM_ROLES["CASHIER"]["permissions"]) - {"payments.split"})
        )

        client = pos_client(authed, user, branch)
        order = open_order_with_item(client, variant)

        response = client.post(
            "/api/v1/payments/",
            {"order": order["id"], "method": str(cash.id), "amount": order["grand_total"]},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )

        assert response.status_code == 201, response.json()
        assert Decimal(response.json()["data"]["order"]["balance_due"]) == 0


# ── orders.reprint ───────────────────────────────────────────────────────────


class TestReprint:
    """
    Reading the receipt is `orders.view`. A duplicate copy of an already-issued
    invoice is `orders.reprint` — a second copy of a paid receipt is the
    paperwork a refund fraud needs, which is why the matrix separates them.
    """

    @pytest.fixture
    def settled(self, authed, make_user, branch, variant, cash):
        client = pos_client(authed, make_user(role="CASHIER", branch=branch), branch)
        order = open_order_with_item(client, variant)
        client.post(
            "/api/v1/payments/",
            {"order": order["id"], "method": str(cash.id), "amount": order["grand_total"]},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        return client, order["id"]

    def test_reading_the_receipt_needs_only_orders_view(self, settled) -> None:
        client, order_id = settled
        response = client.get(f"/api/v1/orders/{order_id}/receipt/")

        assert response.status_code == 200
        assert response.json()["data"]["is_final"] is True
        assert response.json()["data"]["is_reprint"] is False

    def test_a_reprint_is_allowed_for_a_cashier(self, settled) -> None:
        client, order_id = settled
        response = client.get(f"/api/v1/orders/{order_id}/receipt/?reprint=true")

        assert response.status_code == 200
        assert response.json()["data"]["is_reprint"] is True

    def test_a_reprint_is_audited(self, settled) -> None:
        from apps.audit.models import AuditLog

        client, order_id = settled
        client.get(f"/api/v1/orders/{order_id}/receipt/?reprint=true")

        entry = AuditLog.objects.filter(action="order.receipt_reprinted").latest("occurred_at")
        assert entry.object_label

    def test_a_role_without_the_code_is_refused(
        self, authed, make_user, branch, variant, cash
    ) -> None:
        from apps.authz import catalog
        from apps.authz.models import Role

        user = make_user(role="CASHIER", branch=branch)
        role = Role.objects.get(organization=user.organization, code="CASHIER")
        role.set_permissions(
            sorted(set(catalog.SYSTEM_ROLES["CASHIER"]["permissions"]) - {"orders.reprint"})
        )

        client = pos_client(authed, user, branch)
        order = open_order_with_item(client, variant)
        client.post(
            "/api/v1/payments/",
            {"order": order["id"], "method": str(cash.id), "amount": order["grand_total"]},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )

        response = client.get(f"/api/v1/orders/{order['id']}/receipt/?reprint=true")

        assert response.status_code == 403
        # Reading it on screen still works — the restriction is about the copy.
        assert client.get(f"/api/v1/orders/{order['id']}/receipt/").status_code == 200

    def test_an_unpaid_order_has_nothing_to_reprint(
        self, authed, make_user, branch, variant
    ) -> None:
        """
        A "duplicate" of a document that was never issued would hand a customer
        a slip with no serial that looks like a receipt.
        """
        client = pos_client(authed, make_user(role="CASHIER", branch=branch), branch)
        order = open_order_with_item(client, variant)

        response = client.get(f"/api/v1/orders/{order['id']}/receipt/?reprint=true")

        assert response.status_code == 409
        assert response.json()["code"] == "NO_FINAL_INVOICE"


# ── reports.export ───────────────────────────────────────────────────────────


class TestExport:
    """
    An export is not a lesser form of access to the same numbers — it is a
    broader one, because a file leaves the building.
    """

    def test_a_manager_can_read_a_report(self, authed, make_user, branch) -> None:
        client = authed(make_user(role="BRANCH_MANAGER"), branch=branch)
        assert client.get("/api/v1/reports/sales/summary/").status_code == 200

    def test_a_manager_can_export_it(self, authed, make_user, branch) -> None:
        client = authed(make_user(role="BRANCH_MANAGER"), branch=branch)
        response = client.get("/api/v1/reports/sales/summary/?export=csv")

        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/csv")

    def test_reading_without_export_still_works(self, authed, make_user, branch) -> None:
        """Removing the export code must not take the screen away too."""
        from apps.authz import catalog
        from apps.authz.models import Role

        user = make_user(role="BRANCH_MANAGER")
        role = Role.objects.get(organization=user.organization, code="BRANCH_MANAGER")
        role.set_permissions(
            sorted(set(catalog.SYSTEM_ROLES["BRANCH_MANAGER"]["permissions"]) - {"reports.export"})
        )
        client = authed(user, branch=branch)

        assert client.get("/api/v1/reports/sales/summary/").status_code == 200

    def test_the_csv_is_refused_without_the_export_code(self, authed, make_user, branch) -> None:
        from apps.authz import catalog
        from apps.authz.models import Role

        user = make_user(role="BRANCH_MANAGER")
        role = Role.objects.get(organization=user.organization, code="BRANCH_MANAGER")
        role.set_permissions(
            sorted(set(catalog.SYSTEM_ROLES["BRANCH_MANAGER"]["permissions"]) - {"reports.export"})
        )
        client = authed(user, branch=branch)

        response = client.get("/api/v1/reports/sales/summary/?export=csv")

        assert response.status_code == 403
        assert "reports.export" in response.json()["message"]
