"""
The manual price on one line.

`orders.change_price` was a code in the catalogue that nothing enforced, because
the feature did not exist. Discounts were supposed to cover it — and they nearly
do, which is exactly the problem. Pushing "damaged cake, half price", "staff
meal", and "we remade the drink after a complaint" through the discount field
made the discount rate stop meaning anything, and the discount rate is the
number an owner watches for loss.

Three properties matter here, in this order:

  * the catalogue price SURVIVES the override, so afterwards the system can
    still answer "what was it supposed to be?";
  * zero is a real price and negative is not;
  * the override can be undone without voiding the line, because voiding a line
    that has already been fired has the kitchen make it twice.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from apps.audit.models import AuditLog
from apps.catalog.models import Category, Product, ProductVariant
from apps.orders import services
from apps.orders.models import EventType, Order

pytestmark = pytest.mark.django_db


@pytest.fixture
def menu(organization, branch):
    category = Category.objects.create(organization=organization, branch=branch, name_ar="حلويات")
    cake = Product.objects.create(
        organization=organization, branch=branch, category=category, sku="CAKE", name_ar="تشيز كيك"
    )
    return ProductVariant.objects.create(
        product=cake, sku="CAKE-1", price=Decimal("100.00"), cost=Decimal("30.00"), is_default=True
    )


@pytest.fixture
def order(branch, menu):
    order = services.open_order(branch=branch)
    services.apply_events(
        order,
        [
            {
                "id": str(uuid.uuid4()),
                "type": EventType.ITEM_ADDED,
                "payload": {
                    "line_id": str(LINE),
                    "variant_id": str(menu.id),
                    "quantity": "1",
                },
            }
        ],
    )
    return Order.objects.get(pk=order.pk)


LINE = uuid.UUID("11111111-1111-1111-1111-111111111111")


def override(price, reason="كيكة تالفة", line_id=LINE) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "type": EventType.ITEM_PRICE_OVERRIDDEN,
        "payload": {
            "line_id": str(line_id),
            "price": price,
            "reason": reason,
        },
    }


# ── the price itself ─────────────────────────────────────────────────────────


class TestTheOverride:
    def test_the_line_is_charged_at_the_new_price(self, order) -> None:
        services.apply_events(order, [override("50.00")])
        order.refresh_from_db()

        assert order.subtotal == Decimal("50.00")

    def test_the_catalogue_price_survives_it(self, order) -> None:
        """
        The only question anybody asks about an override afterwards is what it
        was supposed to be. Overwriting the snapshot destroys the answer and
        leaves a line that merely looks cheap.
        """
        services.apply_events(order, [override("50.00")])
        item = order.items.get(line_id=LINE)

        assert item.unit_price_snapshot == Decimal("100.00")
        assert item.price_override == Decimal("50.00")
        assert item.effective_unit_price == Decimal("50.00")

    def test_it_multiplies_by_quantity_like_any_price(self, order) -> None:
        services.apply_events(
            order,
            [
                {
                    "id": str(uuid.uuid4()),
                    "type": EventType.ITEM_QUANTITY_CHANGED,
                    "payload": {"line_id": str(LINE), "quantity": "3"},
                },
                override("50.00"),
            ],
        )
        order.refresh_from_db()

        assert order.subtotal == Decimal("150.00")

    def test_zero_is_a_real_price(self, order) -> None:
        """
        A comped item. Recording it honestly beats a 100% discount, which would
        bury a giveaway inside the number that is watched for theft.
        """
        services.apply_events(order, [override("0.00", reason="ضيافة")])
        order.refresh_from_db()
        item = order.items.get(line_id=LINE)

        assert item.price_override == Decimal("0.00")
        assert item.effective_unit_price == Decimal("0.00")
        assert order.subtotal == Decimal("0.00")

    def test_a_negative_price_is_refused(self, order) -> None:
        """That is money out of the drawer with no refund record."""
        with pytest.raises(services.EventRejected):
            services.apply_events(order, [override("-10.00")])

    def test_it_stacks_with_a_line_discount(self, order) -> None:
        """
        Both are allowed on one line, and the discount comes off the overridden
        price — anything else would be a discount computed against a price the
        customer was never charged.
        """
        services.apply_events(
            order,
            [
                override("50.00"),
                {
                    "id": str(uuid.uuid4()),
                    "type": EventType.DISCOUNT_APPLIED,
                    "payload": {"line_id": str(LINE), "percent": "10"},
                },
            ],
        )
        order.refresh_from_db()

        assert order.subtotal == Decimal("45.00")


# ── undoing it ───────────────────────────────────────────────────────────────


class TestClearingIt:
    def test_a_null_price_returns_the_line_to_the_catalogue(self, order) -> None:
        """
        Without this, undoing a typo means voiding the line and re-ringing it —
        and on an order already sent, that has the kitchen cook it twice.
        """
        services.apply_events(order, [override("50.00")])
        services.apply_events(order, [override(None, reason="")])
        order.refresh_from_db()
        item = order.items.get(line_id=LINE)

        assert item.price_override is None
        assert item.effective_unit_price == Decimal("100.00")
        assert order.subtotal == Decimal("100.00")

    def test_clearing_wipes_the_reason_too(self, order) -> None:
        """A reason left behind describes a price that is no longer charged."""
        services.apply_events(order, [override("50.00", reason="تالفة")])
        services.apply_events(order, [override(None)])

        assert order.items.get(line_id=LINE).price_override_reason == ""

    def test_overriding_twice_keeps_the_last_one(self, order) -> None:
        services.apply_events(order, [override("50.00")])
        services.apply_events(order, [override("70.00")])
        order.refresh_from_db()

        assert order.subtotal == Decimal("70.00")


# ── the trail ────────────────────────────────────────────────────────────────


class TestTheAuditTrail:
    def test_every_override_is_recorded(self, order) -> None:
        services.apply_events(order, [override("50.00")])

        assert AuditLog.objects.filter(action="order.price_overridden").exists()

    def test_the_row_carries_both_prices_and_the_reason(self, order) -> None:
        """
        A trail that recorded only the new price would say a cake sold for 50
        and leave somebody to go and look up what a cake costs — a month later,
        after the menu changed.
        """
        services.apply_events(order, [override("50.00", reason="كيكة تالفة")])
        entry = AuditLog.objects.filter(action="order.price_overridden").latest("occurred_at")

        assert entry.detail["catalog_price"] == "100.00"
        assert entry.detail["new_price"] == "50.00"
        assert entry.detail["reason"] == "كيكة تالفة"
        assert entry.detail["item"] == "تشيز كيك"

    def test_clearing_is_recorded_as_well(self, order) -> None:
        """Removing an override is as much a decision as making one."""
        services.apply_events(order, [override("50.00")])
        services.apply_events(order, [override(None)])
        entry = AuditLog.objects.filter(action="order.price_overridden").latest("occurred_at")

        assert entry.detail["previous_override"] == "50.00"
        assert entry.detail["new_price"] is None


# ── who may do it ────────────────────────────────────────────────────────────


class TestPermission:
    """
    `orders.change_price` is a STEP-UP permission, and the catalogue says so in
    as many words: not even a branch manager holds it in their role. Setting an
    arbitrary price is the shortest path from the till to the drawer, so it is
    meant to be a decision somebody stands over — not a button one person has
    all shift.

    Exercised through the API, because that is where the rule lives. A service
    call that bypassed it would prove nothing about what a cashier can do.
    """

    @pytest.fixture
    def cashier(self, authed, make_user, branch):
        return authed(make_user(email="till@caesar.test", role="CASHIER"), branch=branch)

    @pytest.fixture
    def manager(self, authed, make_user, branch):
        return authed(make_user(email="boss@caesar.test", role="BRANCH_MANAGER"), branch=branch)

    @pytest.fixture
    def owner(self, authed, make_user, branch):
        return authed(make_user(email="owner@caesar.test", role="SUPER_ADMIN"), branch=branch)

    def test_a_cashier_cannot_set_a_price(self, cashier, order) -> None:
        response = cashier.post(
            f"/api/v1/orders/{order.id}/events/", {"events": [override("50.00")]}, format="json"
        )

        assert response.status_code == 403
        order.refresh_from_db()
        assert order.subtotal == Decimal("100.00"), "refused, not merely reported"

    def test_a_branch_manager_holds_it_directly_now(self, manager, order) -> None:
        """
        It used to be a deliberate absence: the manager APPROVED an override and
        did not hold it, so a price change always carried two names.

        That split belongs to a chain. Here the manager is the owner, and a
        step-up approval you grant yourself is a dialog, not a control. The
        override is still recorded against them and still writes a PriceHistory
        row — the trail is what makes it accountable, not the second signature.

        The refusal itself is still tested, one test above, against a cashier.
        """
        response = manager.post(
            f"/api/v1/orders/{order.id}/events/", {"events": [override("50.00")]}, format="json"
        )

        assert response.status_code == 200

    def test_the_owner_holds_it_directly(self, owner, order) -> None:
        response = owner.post(
            f"/api/v1/orders/{order.id}/events/", {"events": [override("50.00")]}, format="json"
        )

        assert response.status_code == 200
        order.refresh_from_db()
        assert order.subtotal == Decimal("50.00")

    def test_a_cashier_with_an_approval_token_can(self, cashier, order, make_user) -> None:
        """
        The point of a step-up: a manager approves at the till without the
        cashier logging out and back in, which in a queue means it never
        happens and the real workaround is sharing the manager's PIN.
        """
        from apps.authz.approval import issue_approval_token

        approver = make_user(email="boss@caesar.test", role="BRANCH_MANAGER")
        token, _ttl = issue_approval_token(
            approver_id=approver.id, permission="orders.change_price", target=str(order.id)
        )

        response = cashier.post(
            f"/api/v1/orders/{order.id}/events/",
            {"events": [override("50.00")]},
            format="json",
            HTTP_X_APPROVAL_TOKEN=token,
        )

        assert response.status_code == 200
        order.refresh_from_db()
        assert order.subtotal == Decimal("50.00")

    def test_a_token_for_another_order_is_refused(self, cashier, order, make_user, branch) -> None:
        """
        Otherwise one approval, taken once, discounts every order for the rest
        of its life — which is exactly the abuse a per-object target prevents.
        """
        from apps.authz.approval import issue_approval_token

        approver = make_user(email="boss@caesar.test", role="BRANCH_MANAGER")
        other = services.open_order(branch=branch)
        token, _ttl = issue_approval_token(
            approver_id=approver.id, permission="orders.change_price", target=str(other.id)
        )

        response = cashier.post(
            f"/api/v1/orders/{order.id}/events/",
            {"events": [override("50.00")]},
            format="json",
            HTTP_X_APPROVAL_TOKEN=token,
        )

        assert response.status_code == 403

    def test_a_refused_override_leaves_no_event_behind(self, cashier, order) -> None:
        """
        The whole batch is rejected before anything is folded. A partially
        applied batch would leave an order whose events do not explain its
        totals.
        """
        cashier.post(
            f"/api/v1/orders/{order.id}/events/", {"events": [override("50.00")]}, format="json"
        )

        assert not order.events.filter(event_type=EventType.ITEM_PRICE_OVERRIDDEN).exists()
