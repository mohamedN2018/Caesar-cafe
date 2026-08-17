"""
The channels a café sells on, and the setting that decides which.

`orders.enabled_types` had been in the settings registry since it was built and
**nothing read it**. It appeared on the settings screen, took a value, saved it,
and changed nothing: the till drew a hardcoded three regardless. So a café that
does not deliver had a delivery button it could not remove, and a café that does
had a switch that did not turn one on — the worst kind of setting, because it
looks like it worked.

These tests are what make it real, plus the guard that stops the model and the
registry drifting apart again.
"""

from __future__ import annotations

import pytest

from apps.configuration import definitions, resolver
from apps.configuration.resolver import ScopeContext
from apps.core.exceptions import AppError
from apps.orders import services
from apps.orders.models import OrderType
from apps.orders.views import ORDER_TYPE_LABELS

pytestmark = pytest.mark.django_db


def set_enabled(branch, values: list[str]) -> None:
    """
    Write the override the way the settings screen does.

    Through `resolver.set_value` rather than straight into the table, so the
    registry's validators and the cache invalidation are exercised too — a helper
    that wrote the row directly would let a test pass against a value the real
    screen would have refused.

    `SubsetOf(ORDER_TYPES)` is bypassed for the deliberately-stale case below,
    which is the one value the screen genuinely cannot produce.
    """
    from django.core.cache import cache

    from apps.configuration.models import SettingValue
    from apps.configuration.registry import Scope

    try:
        resolver.set_value("orders.enabled_types", values, scope=Scope.BRANCH, scope_id=branch.id)
    except Exception:
        SettingValue.objects.update_or_create(
            scope_type="BRANCH",
            scope_id=branch.id,
            key="orders.enabled_types",
            defaults={"value": values},
        )
        cache.clear()


class TestTheRegistryAndTheModelAgree:
    """
    A channel the model knows and the settings do not is a channel nobody can
    switch on; one the settings know and the model does not is a button that
    500s. Both have happened to `orders.enabled_types` in different forms.
    """

    def test_every_model_channel_is_configurable(self) -> None:
        assert set(OrderType.values) == set(definitions.ORDER_TYPES)

    def test_every_channel_has_a_name_somebody_reads(self) -> None:
        missing = [t for t in OrderType.values if not ORDER_TYPE_LABELS.get(t)]
        assert missing == [], f"no Arabic label for {missing} — the till would print a code"


class TestTheSettingIsActuallyRead:
    def test_a_disabled_channel_is_refused_at_the_till(self, branch) -> None:
        """
        The whole point. Before this, `open_order` accepted any channel in the
        enum and the setting was decoration.
        """
        set_enabled(branch, ["DINE_IN"])

        with pytest.raises(AppError) as exc:
            services.open_order(branch=branch, order_type=OrderType.DELIVERY)

        assert exc.value.code == "ORDER_TYPE_DISABLED"

    def test_an_enabled_channel_opens(self, branch) -> None:
        set_enabled(branch, ["DINE_IN", "EXTERNAL"])

        order = services.open_order(branch=branch, order_type=OrderType.EXTERNAL)

        assert order.order_type == OrderType.EXTERNAL

    def test_an_empty_setting_does_not_close_the_till(self, branch) -> None:
        """
        A cleared value must not leave a café unable to open ANY order. Ignoring
        a value nobody meant to set beats a till that refuses every sale.
        """
        set_enabled(branch, [])

        assert services.enabled_order_types(branch) == [OrderType.DINE_IN]
        assert services.open_order(branch=branch, order_type=OrderType.DINE_IN) is not None

    def test_a_stale_value_cannot_conjure_a_channel(self, branch, monkeypatch) -> None:
        """
        A channel left in the database by a rename must not reach the till.

        Driven by patching the resolver rather than by writing the row, because
        the row cannot be written: `SubsetOf(ORDER_TYPES)` refuses it on the way
        in, and the registry falls back to the default on the way out. Two layers
        already stop this — which is exactly why the filter here has to be tested
        directly. Reached through the resolver, this test would pass without the
        filter existing at all, and would be measuring the registry.
        """
        monkeypatch.setattr(
            resolver, "get", lambda key, ctx: ["DINE_IN", "DRIVE_THROUGH", "EXTERNAL"]
        )

        assert services.enabled_order_types(branch) == [OrderType.DINE_IN, OrderType.EXTERNAL]


class TestExternalOrders:
    """
    `EXTERNAL` is an order that arrived from outside — a phone call, an app — as
    opposed to `DELIVERY`, the café's own driver taking out a bill somebody rang
    for. They are separate because they are reckoned separately.
    """

    def test_it_needs_no_table(self, branch) -> None:
        order = services.open_order(branch=branch, order_type=OrderType.EXTERNAL)

        assert order.table_session_id is None

    def test_it_prices_on_its_own_channel(self, branch, organization) -> None:
        """
        The reason the channel exists at all. An app sets its own menu price and
        takes a commission, so the same latte is a different number on each — and
        a channel with no row of its own charges the base price, unchanged.
        """
        from decimal import Decimal

        from apps.catalog.models import Category, Product, ProductVariant, VariantChannelPrice

        category = Category.objects.create(
            organization=organization, branch=branch, name_ar="مشروبات"
        )
        product = Product.objects.create(
            organization=organization,
            branch=branch,
            category=category,
            sku="LATTE",
            name_ar="لاتيه",
        )
        variant = ProductVariant.objects.create(
            product=product, sku="LATTE-R", price=Decimal("75.00"), is_default=True
        )
        VariantChannelPrice.objects.create(
            variant=variant, order_type=OrderType.EXTERNAL, price=Decimal("95.00")
        )

        assert variant.price_for(OrderType.EXTERNAL) == Decimal("95.00")
        assert variant.price_for(OrderType.DINE_IN) == Decimal("75.00")

    def test_service_charge_does_not_follow_it_by_default(self, branch) -> None:
        """
        Service is for table service. `finance.service_applies_to` defaults to
        DINE_IN, and a phone order is not somebody being waited on.
        """
        context = ScopeContext(organization_id=branch.organization_id, branch_id=branch.id)
        assert OrderType.EXTERNAL not in resolver.get("finance.service_applies_to", context)

        order = services.open_order(branch=branch, order_type=OrderType.EXTERNAL)
        assert order.service_percent == 0


class TestTheTillIsToldWhichChannels:
    def test_the_endpoint_lists_only_what_is_enabled(self, authed, make_user, branch) -> None:
        set_enabled(branch, ["DINE_IN", "EXTERNAL"])
        client = authed(make_user(role="CASHIER"), branch=branch)

        rows = client.get("/api/v1/orders/types/").json()["data"]

        assert [r["value"] for r in rows] == ["DINE_IN", "EXTERNAL"]
        assert [r["label"] for r in rows] == ["صالة", "طلب خارجي"]

    def test_a_cashier_can_read_it_without_being_an_administrator(
        self, authed, make_user, branch
    ) -> None:
        """
        Served from `/orders/types/` rather than `/settings/` for exactly this: a
        till that has to be an administrator to know which buttons to draw is a
        till that draws the wrong ones.
        """
        client = authed(make_user(role="CASHIER"), branch=branch)

        assert client.get("/api/v1/orders/types/").status_code == 200
        assert client.get("/api/v1/settings/").status_code == 403

    def test_the_default_never_points_at_a_disabled_channel(
        self, authed, make_user, branch
    ) -> None:
        """
        `orders.default_type` defaults to DINE_IN. Switch dine-in off — a café
        that only delivers — and a till preselecting it would open every order
        against a channel the server refuses.
        """
        set_enabled(branch, ["DELIVERY", "EXTERNAL"])
        client = authed(make_user(role="CASHIER"), branch=branch)

        rows = client.get("/api/v1/orders/types/").json()["data"]
        default = [r for r in rows if r["is_default"]]

        assert len(default) == 1
        assert default[0]["value"] == "DELIVERY"

    def test_only_the_room_asks_for_a_table(self, authed, make_user, branch) -> None:
        client = authed(make_user(role="CASHIER"), branch=branch)

        payload = client.get("/api/v1/orders/types/").json()["data"]
        rows = {r["value"]: r["needs_table"] for r in payload}

        assert rows["DINE_IN"] is True
        assert rows["EXTERNAL"] is False
        assert rows["TAKE_AWAY"] is False


class TestFindingTheBillOpenOnATable:
    """
    A table has ONE bill, and this filter is what lets the till find it.

    Without it, every arrival at the order screen opened a fresh order — so a
    party ordering three rounds finished with three separate bills on one table.
    Six, on table 2 of the running demo. Each had its own number and its own
    receipt; the floor board summed them into one figure, so nothing admitted it
    until somebody tried to settle and was handed three.
    """

    def test_it_finds_the_open_bill_for_a_session(self, authed, make_user, branch, organization):
        from apps.floor.models import Area, Table, TableSession

        area = Area.objects.create(organization=organization, branch=branch, name_ar="الصالة")
        table = Table.objects.create(area=area, number="7", seats=4)
        session = TableSession.objects.create(table=table, guest_count=2)

        first = services.open_order(
            branch=branch, order_type=OrderType.DINE_IN, table_session=session
        )
        client = authed(make_user(role="CASHIER"), branch=branch)

        rows = client.get(f"/api/v1/orders/?open=true&session={session.id}").json()["data"]

        assert [r["id"] for r in rows] == [str(first.id)]

    def test_it_does_not_reach_the_previous_party(self, authed, make_user, branch, organization):
        """
        By SESSION, never by table.

        A table filter would find the bill of the people who left. Adding a round
        to THAT is the one mistake this lookup exists to prevent, and it is
        unrecoverable at closing — the items are on a receipt nobody at the table
        ordered.
        """
        from django.utils import timezone

        from apps.floor.models import Area, Table, TableSession

        area = Area.objects.create(organization=organization, branch=branch, name_ar="الصالة")
        table = Table.objects.create(area=area, number="7", seats=4)

        gone = TableSession.objects.create(table=table, guest_count=2)
        services.open_order(branch=branch, order_type=OrderType.DINE_IN, table_session=gone)
        gone.closed_at = timezone.now()
        gone.save(update_fields=["closed_at"])

        seated = TableSession.objects.create(table=table, guest_count=3)
        client = authed(make_user(role="CASHIER"), branch=branch)

        by_session = client.get(f"/api/v1/orders/?open=true&session={seated.id}").json()["data"]
        by_table = client.get(f"/api/v1/orders/?open=true&table={table.id}").json()["data"]

        assert by_session == [], "the new party's session has no bill yet"
        assert len(by_table) == 1, "the table filter DOES reach the old one — hence the session one"
