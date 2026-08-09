"""
The POS REST surface, end to end through HTTP.

The domain services are tested in test_orders.py; these prove the API enforces
the same rules — permissions, idempotency headers, tenant scoping — that the
services assume.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from apps.catalog.models import Category, Product, ProductVariant
from apps.floor.models import Area, Table
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


@pytest.fixture
def table(organization, branch):
    area = Area.objects.create(organization=organization, branch=branch, name_ar="الصالة")
    return Table.objects.create(area=area, number="T-05", seats=4)


@pytest.fixture
def cashier(make_user, branch):
    return make_user(role="CASHIER", branch=branch, pin="1234")


@pytest.fixture
def pos(authed, cashier, branch):
    """A cashier on an activated device, with an open shift."""
    client = authed(cashier, branch=branch, kind="POS", device_id=uuid.uuid4())
    response = client.post("/api/v1/shifts/open/", {"opening_cash": "500.00"}, format="json")
    assert response.status_code == 201, response.json()
    client.shift_id = response.json()["data"]["id"]
    return client


def _open_order(client, **kwargs):
    response = client.post("/api/v1/orders/", {"order_type": "DINE_IN", **kwargs}, format="json")
    assert response.status_code == 201, response.json()
    return response.json()["data"]


def _add_item(client, order_id, variant, quantity=1):
    return client.post(
        f"/api/v1/orders/{order_id}/events/",
        {
            "events": [
                {
                    "id": str(uuid.uuid4()),
                    "type": "ITEM_ADDED",
                    "payload": {
                        "line_id": str(uuid.uuid4()),
                        "variant_id": str(variant.id),
                        "quantity": str(quantity),
                    },
                }
            ]
        },
        format="json",
    )


class TestShiftEndpoints:
    def test_opening_a_shift(self, pos) -> None:
        response = pos.get("/api/v1/shifts/current/")
        assert response.status_code == 200
        assert response.json()["data"]["shift"]["opening_cash"] == "500.00"

    def test_a_second_shift_on_one_device_is_refused(self, pos) -> None:
        response = pos.post("/api/v1/shifts/open/", {"opening_cash": "100"}, format="json")
        assert response.status_code == 409
        assert response.json()["code"] == "SHIFT_ALREADY_OPEN"

    def test_selling_without_a_shift_is_refused(self, authed, cashier, branch) -> None:
        client = authed(cashier, branch=branch, kind="POS", device_id=uuid.uuid4())
        response = client.post("/api/v1/orders/", {"order_type": "DINE_IN"}, format="json")
        assert response.status_code == 400
        assert response.json()["code"] == "SHIFT_REQUIRED"

    def test_cash_movement_shifts_the_expectation(self, pos) -> None:
        response = pos.post(
            f"/api/v1/shifts/{pos.shift_id}/cash-movements/",
            {"movement_type": "EXPENSE", "amount": "120.00", "reason": "شراء مياه"},
            format="json",
        )
        assert response.status_code == 201

        report = pos.get(f"/api/v1/shifts/{pos.shift_id}/x-report/").json()["data"]
        assert report["cash_out"] == "120.00"

    def test_blind_close_withholds_the_expected_figure(self, pos) -> None:
        """The cashier's count must be an observation, not a target."""
        report = pos.get(f"/api/v1/shifts/{pos.shift_id}/x-report/").json()["data"]
        assert report["expected_cash"] is None

    def test_a_manager_sees_the_expected_figure(self, make_user, authed, branch, pos) -> None:
        manager = make_user(email="mgr@caesar.test", role="BRANCH_MANAGER", branch=branch)
        client = authed(manager, branch=branch)
        report = client.get(f"/api/v1/shifts/{pos.shift_id}/x-report/").json()["data"]
        assert report["expected_cash"] == "500.00"

    def test_closing_and_the_z_report(self, pos) -> None:
        closed = pos.post(
            f"/api/v1/shifts/{pos.shift_id}/close/",
            {"counted_cash": "500.00"},
            format="json",
        )
        assert closed.status_code == 200
        assert closed.json()["data"]["variance"] == "0.00"

        z = pos.get(f"/api/v1/shifts/{pos.shift_id}/z-report/")
        assert z.status_code == 200
        assert z.json()["data"]["is_final"] is True


class TestOrderEndpoints:
    def test_open_add_and_total(self, pos, variant) -> None:
        order = _open_order(pos)
        response = _add_item(pos, order["id"], variant, 2)

        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data["applied"]) == 1
        assert data["order"]["subtotal"] == "120.00"
        assert data["order"]["grand_total"] == "136.80"

    def test_a_replayed_batch_is_reported_as_skipped(self, pos, variant) -> None:
        order = _open_order(pos)
        event = {
            "id": str(uuid.uuid4()),
            "type": "ITEM_ADDED",
            "payload": {
                "line_id": str(uuid.uuid4()),
                "variant_id": str(variant.id),
                "quantity": "1",
            },
        }
        body = {"events": [event]}

        pos.post(f"/api/v1/orders/{order['id']}/events/", body, format="json")
        second = pos.post(f"/api/v1/orders/{order['id']}/events/", body, format="json")

        assert second.json()["data"]["skipped"] == [event["id"]]
        assert len(second.json()["data"]["order"]["items"]) == 1

    def test_the_event_stream_is_readable(self, pos, variant) -> None:
        order = _open_order(pos)
        _add_item(pos, order["id"], variant)

        events = pos.get(f"/api/v1/orders/{order['id']}/events/").json()["data"]
        assert [e["event_type"] for e in events] == ["ORDER_OPENED", "ITEM_ADDED"]

    def test_a_cashier_cannot_exceed_the_discount_ceiling(self, pos, variant) -> None:
        order = _open_order(pos)
        _add_item(pos, order["id"], variant)

        response = pos.post(
            f"/api/v1/orders/{order['id']}/events/",
            {
                "events": [
                    {
                        "id": str(uuid.uuid4()),
                        "type": "DISCOUNT_APPLIED",
                        "payload": {"percent": "50", "reason": "عميل دائم"},
                    }
                ]
            },
            format="json",
        )
        assert response.status_code == 403
        assert response.json()["code"] == "DISCOUNT_EXCEEDS_LIMIT"

    def test_a_discount_within_the_ceiling_is_allowed(self, pos, variant) -> None:
        order = _open_order(pos)
        _add_item(pos, order["id"], variant)

        response = pos.post(
            f"/api/v1/orders/{order['id']}/events/",
            {
                "events": [
                    {
                        "id": str(uuid.uuid4()),
                        "type": "DISCOUNT_APPLIED",
                        "payload": {"percent": "10", "reason": "عميل دائم"},
                    }
                ]
            },
            format="json",
        )
        assert response.status_code == 200

    def test_firing_moves_the_order_to_the_kitchen(self, pos, variant) -> None:
        order = _open_order(pos)
        _add_item(pos, order["id"], variant)

        response = pos.post(
            f"/api/v1/orders/{order['id']}/events/",
            {"events": [{"id": str(uuid.uuid4()), "type": "ORDER_FIRED", "payload": {}}]},
            format="json",
        )
        assert response.json()["data"]["order"]["status"] == "IN_KITCHEN"

    def test_the_open_board_lists_active_orders(self, pos, variant) -> None:
        order = _open_order(pos)
        _add_item(pos, order["id"], variant)

        board = pos.get("/api/v1/orders/?open=true").json()["data"]
        assert len(board) == 1
        assert board[0]["item_count"] == 1

    def test_orders_are_scoped_to_the_branch(
        self, pos, variant, make_user, authed, other_organization, other_branch
    ) -> None:
        order = _open_order(pos)
        outsider = make_user(
            email="out@other.test",
            role="BRANCH_MANAGER",
            org=other_organization,
            branch=other_branch,
        )
        client = authed(outsider, branch=other_branch)

        assert client.get(f"/api/v1/orders/{order['id']}/").status_code == 404
        assert client.get("/api/v1/orders/").json()["data"] == []


class TestPaymentMethodsAreReadableByWhoeverSells:
    """
    Reading this list was gated on `payments.view_all`, and that was simply the
    wrong permission. `view_all` means "see every payment in the branch,
    including other people's" — a reporting capability a cashier does not have
    and should not. But the endpoint returns CONFIGURATION: which tenders the
    branch accepts, i.e. the buttons on the payment screen.

    So the till could not settle a bill. It failed with the very message this
    product promises never to show — "ليس لديك صلاحية" for something the user
    was offered — and the fix is the permission, not a wider role.
    """

    def test_a_cashier_can_read_the_payment_methods(self, pos, cash) -> None:
        response = pos.get("/api/v1/payments/methods/")

        assert response.status_code == 200
        assert [m["code"] for m in response.json()["data"]] == ["CASH"]

    def test_an_accountant_can_too(self, authed, make_user, branch, cash) -> None:
        """
        The other half of the "any of these" declaration. An accountant has
        `payments.view_all` and not `payments.take`, so picking either code
        alone would have locked one of the two roles out of a list they both
        legitimately need.
        """
        client = authed(make_user(email="acc@caesar.test", role="ACCOUNTANT"), branch=branch)

        assert client.get("/api/v1/payments/methods/").status_code == 200

    def test_a_cook_cannot(self, authed, make_user, branch, cash) -> None:
        """ "Any of these" must still be a closed list, not an open door."""
        client = authed(make_user(email="cook@caesar.test", role="KITCHEN"), branch=branch)

        assert client.get("/api/v1/payments/methods/").status_code == 403

    def test_a_cashier_still_cannot_change_them(self, pos) -> None:
        """Reading the tenders is not licence to invent one."""
        response = pos.post(
            "/api/v1/payments/methods/",
            {"code": "CRYPTO", "name_ar": "عملة رقمية"},
            format="json",
        )

        assert response.status_code == 403


class TestPaymentEndpoints:
    def test_payment_requires_an_idempotency_key(self, pos, variant, cash) -> None:
        """§51 — the retry semantics of every client depend on this."""
        order = _open_order(pos)
        _add_item(pos, order["id"], variant)

        response = pos.post(
            "/api/v1/payments/",
            {"order": order["id"], "method": str(cash.id), "amount": "136.80"},
            format="json",
        )
        assert response.status_code == 400
        assert response.json()["code"] == "IDEMPOTENCY_KEY_REQUIRED"

    def test_payment_settles_the_order(self, pos, variant, cash) -> None:
        order = _open_order(pos)
        _add_item(pos, order["id"], variant, 2)

        response = pos.post(
            "/api/v1/payments/",
            {
                "order": order["id"],
                "method": str(cash.id),
                "amount": "136.80",
                "tendered": "200.00",
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert response.status_code == 201
        data = response.json()["data"]
        assert data["change_given"] == "63.20"
        assert data["order"]["status"] == "PAID"

    def test_a_replayed_payment_charges_once(self, pos, variant, cash) -> None:
        order = _open_order(pos)
        _add_item(pos, order["id"], variant, 2)
        key = str(uuid.uuid4())
        body = {"order": order["id"], "method": str(cash.id), "amount": "136.80"}

        first = pos.post("/api/v1/payments/", body, format="json", HTTP_IDEMPOTENCY_KEY=key)
        second = pos.post("/api/v1/payments/", body, format="json", HTTP_IDEMPOTENCY_KEY=key)

        assert first.json()["data"]["id"] == second.json()["data"]["id"]
        assert len(pos.get(f"/api/v1/payments/?order={order['id']}").json()["data"]) == 1

    def test_settling_issues_a_receipt(self, pos, variant, cash) -> None:
        order = _open_order(pos)
        _add_item(pos, order["id"], variant, 2)
        pos.post(
            "/api/v1/payments/",
            {"order": order["id"], "method": str(cash.id), "amount": "136.80"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )

        receipt = pos.get(f"/api/v1/orders/{order['id']}/receipt/").json()["data"]
        assert receipt["is_final"] is True
        assert receipt["grand_total"] == "136.80"
        assert receipt["serial"].startswith("MB-")

    def test_a_cashier_cannot_refund(self, pos, variant, cash) -> None:
        """docs/05: refunding is a separate capability from taking money."""
        order = _open_order(pos)
        _add_item(pos, order["id"], variant)
        pos.post(
            "/api/v1/payments/",
            {"order": order["id"], "method": str(cash.id), "amount": "68.40"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )

        response = pos.post(
            "/api/v1/payments/refunds/",
            {"order": order["id"], "amount": "68.40", "reason": "شكوى"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert response.status_code == 403

    def test_a_manager_can_refund(self, pos, variant, cash, make_user, authed, branch) -> None:
        order = _open_order(pos)
        _add_item(pos, order["id"], variant)
        pos.post(
            "/api/v1/payments/",
            {"order": order["id"], "method": str(cash.id), "amount": "68.40"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )

        manager = make_user(email="mgr@caesar.test", role="BRANCH_MANAGER", branch=branch)
        client = authed(manager, branch=branch)
        response = client.post(
            "/api/v1/payments/refunds/",
            {"order": order["id"], "amount": "68.40", "reason": "شكوى عميل"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert response.status_code == 201

    def test_a_device_token_alone_cannot_take_money(
        self, authed, cashier, branch, variant, cash
    ) -> None:
        """
        A bare device principal can sync at 3am but cannot take a payment —
        there would be nobody to name in the audit log.
        """
        from rest_framework.test import APIClient

        from apps.accounts import tokens

        pair = tokens.issue_pair(
            user=None,
            kind="DEVICE",
            organization_id=branch.organization_id,
            branch_id=branch.id,
            device_id=uuid.uuid4(),
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {pair['access']}")

        response = client.post(
            "/api/v1/payments/",
            {"order": str(uuid.uuid4()), "method": str(cash.id), "amount": "10"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert response.status_code in (401, 403)


class TestFloorEndpoints:
    def test_opening_a_session_occupies_the_table(self, pos, table) -> None:
        response = pos.post(
            "/api/v1/floor/sessions/", {"table": str(table.id), "guest_count": 4}, format="json"
        )
        assert response.status_code == 201

        board = pos.get("/api/v1/floor/status/").json()["data"]
        row = next(r for r in board if r["number"] == "T-05")
        assert row["status"] == "OCCUPIED"
        assert row["guest_count"] == 4

    def test_a_busy_table_cannot_be_opened_twice(self, pos, table) -> None:
        pos.post("/api/v1/floor/sessions/", {"table": str(table.id)}, format="json")
        response = pos.post("/api/v1/floor/sessions/", {"table": str(table.id)}, format="json")
        assert response.status_code == 400
        assert response.json()["code"] == "TABLE_OCCUPIED"

    def test_the_board_shows_what_is_due(self, pos, table, variant) -> None:
        session = pos.post(
            "/api/v1/floor/sessions/", {"table": str(table.id)}, format="json"
        ).json()["data"]

        order = _open_order(pos, table_session=session["id"])
        _add_item(pos, order["id"], variant, 2)

        row = next(
            r for r in pos.get("/api/v1/floor/status/").json()["data"] if r["number"] == "T-05"
        )
        assert row["order_count"] == 1
        assert row["total_due"] == "136.80"

    def test_a_session_with_unsettled_orders_cannot_close(self, pos, table, variant) -> None:
        session = pos.post(
            "/api/v1/floor/sessions/", {"table": str(table.id)}, format="json"
        ).json()["data"]
        order = _open_order(pos, table_session=session["id"])
        _add_item(pos, order["id"], variant)

        response = pos.post(f"/api/v1/floor/sessions/{session['id']}/close/", {}, format="json")
        assert response.status_code == 400
        assert response.json()["code"] == "UNSETTLED_ORDERS"

    def test_transferring_moves_the_session_and_its_orders(
        self, pos, table, variant, organization, branch
    ) -> None:
        target = Table.objects.create(area=table.area, number="T-06", seats=2)
        session = pos.post(
            "/api/v1/floor/sessions/", {"table": str(table.id)}, format="json"
        ).json()["data"]
        order = _open_order(pos, table_session=session["id"])
        _add_item(pos, order["id"], variant)

        response = pos.post(
            f"/api/v1/floor/sessions/{session['id']}/transfer/",
            {"target_table": str(target.id)},
            format="json",
        )
        assert response.status_code == 200

        board = {r["number"]: r for r in pos.get("/api/v1/floor/status/").json()["data"]}
        assert board["T-06"]["order_count"] == 1
        assert board["T-05"]["status"] == "AVAILABLE"
