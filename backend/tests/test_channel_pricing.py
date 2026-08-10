"""
A different price when the order leaves the room.

"المياه جوه الصالة بـ15، ولما تطلع توصيل بتتحسب بـ20."

Modelled as a price rather than a markup, because that is what it is: a delivery
price covers a driver and a takeaway cup costs more than a glass that gets
washed. Neither is a percentage of the room price, and a cafe that raises
dine-in by five pounds does not thereby want delivery to move.

The property that matters most is the last group: **the channel is resolved by
the server, from the order's own type, at the moment the line is rung.** A
client that sent a price would be a client that could send any price.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from apps.catalog.models import Category, Product, ProductVariant, VariantChannelPrice
from apps.orders import services
from apps.orders.models import EventType, Order, OrderType

pytestmark = pytest.mark.django_db


@pytest.fixture
def water(organization, branch):
    category = Category.objects.create(organization=organization, branch=branch, name_ar="مشروبات")
    product = Product.objects.create(
        organization=organization,
        branch=branch,
        category=category,
        sku="WATER",
        name_ar="مياه معدنية",
        is_tax_exempt=True,
    )
    variant = ProductVariant.objects.create(
        product=product, sku="WATER-S", price=Decimal("15.00"), is_default=True
    )
    VariantChannelPrice.objects.create(
        variant=variant, order_type=OrderType.DELIVERY, price=Decimal("20.00")
    )
    return variant


def ring(order, variant, quantity=1):
    services.apply_events(
        order,
        [
            {
                "id": str(uuid.uuid4()),
                "type": EventType.ITEM_ADDED,
                "payload": {
                    "line_id": str(uuid.uuid4()),
                    "variant_id": str(variant.id),
                    "quantity": str(quantity),
                },
            }
        ],
    )
    return Order.objects.get(pk=order.pk)


class TestTheChannelDecidesThePrice:
    def test_in_the_room_it_is_the_base_price(self, branch, water) -> None:
        order = ring(services.open_order(branch=branch, order_type=OrderType.DINE_IN), water)

        assert order.subtotal == Decimal("15.00")

    def test_on_a_delivery_it_is_the_delivery_price(self, branch, water) -> None:
        order = ring(services.open_order(branch=branch, order_type=OrderType.DELIVERY), water)

        assert order.subtotal == Decimal("20.00")

    def test_a_channel_with_no_row_falls_back_to_the_base(self, branch, water) -> None:
        """
        Most of the menu costs the same everywhere. Requiring a row per channel
        would be three places to forget to update, so silence means "same".
        """
        order = ring(services.open_order(branch=branch, order_type=OrderType.TAKE_AWAY), water)

        assert order.subtotal == Decimal("15.00")

    def test_the_channel_price_multiplies_like_any_price(self, branch, water) -> None:
        order = ring(services.open_order(branch=branch, order_type=OrderType.DELIVERY), water, 3)

        assert order.subtotal == Decimal("60.00")


class TestTheLineRemembersWhatItWasCharged:
    def test_the_snapshot_is_the_channel_price_not_the_menu_price(self, branch, water) -> None:
        """
        Frozen onto the line, like every other snapshot. A receipt reprinted
        next month has to show what the customer actually paid, not what the
        menu says today.
        """
        order = ring(services.open_order(branch=branch, order_type=OrderType.DELIVERY), water)

        assert order.items.first().unit_price_snapshot == Decimal("20.00")

    def test_changing_the_channel_price_later_does_not_move_an_old_bill(
        self, branch, water
    ) -> None:
        order = ring(services.open_order(branch=branch, order_type=OrderType.DELIVERY), water)

        VariantChannelPrice.objects.filter(variant=water).update(price=Decimal("35.00"))
        order.refresh_from_db()

        assert order.subtotal == Decimal("20.00")

    def test_a_manual_override_still_wins(self, branch, water) -> None:
        """
        The two features stack in the order a cashier expects: the channel sets
        the price, and a human overruling it overrules whatever it was.
        """
        order = ring(services.open_order(branch=branch, order_type=OrderType.DELIVERY), water)
        line = order.items.first()

        services.apply_events(
            order,
            [
                {
                    "id": str(uuid.uuid4()),
                    "type": EventType.ITEM_PRICE_OVERRIDDEN,
                    "payload": {"line_id": str(line.line_id), "price": "5.00", "reason": "ضيافة"},
                }
            ],
        )
        order.refresh_from_db()

        assert order.subtotal == Decimal("5.00")
        # The delivery price stays beside it, so "what should this have been"
        # is still answerable — the same rule as the menu price.
        assert order.items.first().unit_price_snapshot == Decimal("20.00")


class TestTheClientCannotChooseThePrice:
    def test_a_price_in_the_event_payload_is_ignored(self, branch, water) -> None:
        """
        The single most important property here. `ITEM_ADDED` carries a variant
        id and a quantity; the price is resolved server-side from the order's
        own channel. A client that could send a price could send any price, and
        the till is a browser on a shared machine.
        """
        order = services.open_order(branch=branch, order_type=OrderType.DELIVERY)
        services.apply_events(
            order,
            [
                {
                    "id": str(uuid.uuid4()),
                    "type": EventType.ITEM_ADDED,
                    "payload": {
                        "line_id": str(uuid.uuid4()),
                        "variant_id": str(water.id),
                        "quantity": "1",
                        "unit_price_snapshot": "1.00",
                        "price": "1.00",
                    },
                }
            ],
        )
        order.refresh_from_db()

        assert order.subtotal == Decimal("20.00")


class TestTheTillIsToldTheChannelPrices:
    @pytest.fixture
    def client(self, authed, make_user, branch):
        return authed(make_user(email="till@caesar.test", role="CASHIER"), branch=branch)

    def test_the_product_carries_its_channel_prices(self, client, water) -> None:
        """
        So a tile can show 20 before a delivery order is rung, rather than 15
        and a surprise on the bill. That gap is an argument on the phone.
        """
        row = client.get("/api/v1/catalog/products/").json()["data"][0]
        prices = row["variants"][0]["channel_prices"]

        assert [(p["order_type"], p["price"]) for p in prices] == [("DELIVERY", "20.00")]

    def test_only_the_channels_that_differ_are_sent(self, client, water) -> None:
        """Three near-identical rows per variant is payload nobody reads."""
        row = client.get("/api/v1/catalog/products/").json()["data"][0]

        assert len(row["variants"][0]["channel_prices"]) == 1
