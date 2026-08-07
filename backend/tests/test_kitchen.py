"""
Kitchen routing, ticket lifecycle, and the real-time layer.

The properties under test: firing is idempotent per item, an order's status
follows its tickets, and an unrouted item is reported rather than lost.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.catalog.models import Category, Product, ProductVariant
from apps.core.exceptions import InvalidStateTransition
from apps.kitchen import services
from apps.kitchen.models import KitchenTicket, Station, TicketStatus
from apps.orders import services as order_services
from apps.orders.models import EventType, OrderStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def stations(organization, branch):
    return {
        "coffee": Station.objects.create(
            organization=organization,
            branch=branch,
            code="COFFEE",
            name_ar="بار القهوة",
            target_prep_minutes=5,
        ),
        "dessert": Station.objects.create(
            organization=organization,
            branch=branch,
            code="DESSERT",
            name_ar="الحلويات",
            target_prep_minutes=10,
        ),
        "bar": Station.objects.create(
            organization=organization,
            branch=branch,
            code="BAR",
            name_ar="البار",
            auto_accept=True,
        ),
    }


@pytest.fixture
def menu(organization, branch, stations):
    category = Category.objects.create(organization=organization, branch=branch, name_ar="المنيو")

    def make(sku, name, station, price="60.00"):
        product = Product.objects.create(
            organization=organization,
            branch=branch,
            category=category,
            sku=sku,
            name_ar=name,
            station=station,
        )
        return ProductVariant.objects.create(
            product=product, sku=f"{sku}-D", price=Decimal(price), is_default=True
        )

    return {
        "cappuccino": make("CAPP", "كابتشينو", stations["coffee"]),
        "cake": make("CAKE", "تشيز كيك", stations["dessert"], "75.00"),
        "juice": make("JUICE", "عصير", stations["bar"], "35.00"),
        "unrouted": make("WATER", "مياه", None, "15.00"),
    }


def add(order, variant, quantity=1):
    return {
        "id": str(uuid.uuid4()),
        "type": EventType.ITEM_ADDED,
        "payload": {
            "line_id": str(uuid.uuid4()),
            "variant_id": str(variant.id),
            "quantity": str(quantity),
        },
    }


def fire(order):
    return order_services.apply_events(
        order, [{"id": str(uuid.uuid4()), "type": EventType.ORDER_FIRED, "payload": {}}]
    )


class TestRouting:
    def test_one_ticket_per_station(self, branch, menu) -> None:
        """
        A coffee and a cake become two tickets — they are made by different
        people in different places.
        """
        order = order_services.open_order(branch=branch)
        order_services.apply_events(
            order, [add(order, menu["cappuccino"], 2), add(order, menu["cake"])]
        )
        fire(order)

        tickets = list(order.kitchen_tickets.select_related("station"))
        assert len(tickets) == 2
        assert {t.station.code for t in tickets} == {"COFFEE", "DESSERT"}

    def test_items_for_one_station_share_a_ticket(self, branch, menu) -> None:
        order = order_services.open_order(branch=branch)
        order_services.apply_events(
            order, [add(order, menu["cappuccino"]), add(order, menu["cappuccino"], 3)]
        )
        fire(order)

        ticket = order.kitchen_tickets.get()
        assert ticket.lines.count() == 2

    def test_firing_twice_only_sends_what_is_new(self, branch, menu) -> None:
        """A second press must not re-send what the kitchen already has."""
        order = order_services.open_order(branch=branch)
        order_services.apply_events(order, [add(order, menu["cappuccino"])])
        fire(order)

        order_services.apply_events(order, [add(order, menu["cake"])])
        fire(order)

        assert order.kitchen_tickets.count() == 2
        coffee = order.kitchen_tickets.filter(station__code="COFFEE").get()
        assert coffee.lines.count() == 1

    def test_firing_nothing_new_is_rejected(self, branch, menu) -> None:
        order = order_services.open_order(branch=branch)
        order_services.apply_events(order, [add(order, menu["cappuccino"])])
        fire(order)

        with pytest.raises(order_services.EventRejected):
            fire(order)

    def test_an_item_with_no_station_is_reported_not_dropped(self, branch, menu) -> None:
        """An item nobody makes is a problem the cashier must learn about now."""
        order = order_services.open_order(branch=branch)
        order_services.apply_events(
            order, [add(order, menu["cappuccino"]), add(order, menu["unrouted"])]
        )
        result = services.route_order(order)

        assert result.unrouted == ["مياه"]
        assert len(result.tickets) == 1

    def test_auto_accept_stations_skip_triage(self, branch, menu) -> None:
        order = order_services.open_order(branch=branch)
        order_services.apply_events(order, [add(order, menu["juice"])])
        fire(order)

        ticket = order.kitchen_tickets.get()
        assert ticket.status == TicketStatus.ACCEPTED
        assert ticket.accepted_at is not None

    def test_ticket_numbers_are_small_and_sequential(self, branch, menu) -> None:
        """Staff read these aloud across a noisy kitchen."""
        numbers = []
        for _ in range(3):
            order = order_services.open_order(branch=branch)
            order_services.apply_events(order, [add(order, menu["cappuccino"])])
            fire(order)
            numbers.append(order.kitchen_tickets.get().ticket_number)

        assert numbers == [1, 2, 3]

    def test_lines_are_snapshotted(self, branch, menu) -> None:
        order = order_services.open_order(branch=branch)
        order_services.apply_events(order, [add(order, menu["cappuccino"], 2)])
        fire(order)

        line = order.kitchen_tickets.get().lines.get()
        assert line.name_snapshot == "كابتشينو"
        assert line.quantity == Decimal("2.000")

        menu["cappuccino"].product.name_ar = "كابتشينو دبل"
        menu["cappuccino"].product.save()
        line.refresh_from_db()
        assert line.name_snapshot == "كابتشينو"


class TestLifecycle:
    def _ticket(self, branch, menu) -> KitchenTicket:
        order = order_services.open_order(branch=branch)
        order_services.apply_events(order, [add(order, menu["cappuccino"])])
        fire(order)
        return order.kitchen_tickets.get()

    def test_full_happy_path(self, branch, menu) -> None:
        ticket = self._ticket(branch, menu)

        ticket = services.transition(ticket, TicketStatus.ACCEPTED)
        assert ticket.accepted_at is not None

        ticket = services.transition(ticket, TicketStatus.PREPARING)
        assert ticket.started_at is not None

        ticket = services.transition(ticket, TicketStatus.READY)
        assert ticket.ready_at is not None
        assert ticket.prep_seconds is not None

        ticket = services.transition(ticket, TicketStatus.SERVED)
        assert ticket.served_at is not None

    @pytest.mark.parametrize(
        ("current", "target"),
        [
            (TicketStatus.NEW, TicketStatus.READY),
            (TicketStatus.NEW, TicketStatus.SERVED),
            (TicketStatus.CANCELLED, TicketStatus.PREPARING),
        ],
    )
    def test_invalid_transitions_are_refused(self, branch, menu, current, target) -> None:
        ticket = self._ticket(branch, menu)
        KitchenTicket.objects.filter(pk=ticket.pk).update(status=current)
        ticket.refresh_from_db()

        with pytest.raises(InvalidStateTransition):
            services.transition(ticket, target)

    def test_prep_seconds_measures_fired_to_ready(self, branch, menu) -> None:
        ticket = self._ticket(branch, menu)
        KitchenTicket.objects.filter(pk=ticket.pk).update(
            created_at=timezone.now() - timedelta(minutes=4)
        )
        ticket.refresh_from_db()

        services.transition(ticket, TicketStatus.PREPARING)
        ticket = services.transition(ticket, TicketStatus.READY)

        assert 230 <= ticket.prep_seconds <= 250

    def test_late_is_measured_against_the_station_target(self, branch, menu) -> None:
        ticket = self._ticket(branch, menu)  # coffee bar, 5-minute target
        assert not ticket.is_late()

        KitchenTicket.objects.filter(pk=ticket.pk).update(
            created_at=timezone.now() - timedelta(minutes=7)
        )
        ticket.refresh_from_db()
        assert ticket.is_late()

    def test_recall_clears_readiness(self, branch, menu) -> None:
        ticket = self._ticket(branch, menu)
        services.transition(ticket, TicketStatus.PREPARING)
        ticket = services.transition(ticket, TicketStatus.READY)
        ticket = services.transition(ticket, TicketStatus.SERVED)

        ticket = services.recall(ticket)
        assert ticket.status == TicketStatus.PREPARING
        assert ticket.ready_at is None
        assert ticket.prep_seconds is None

    def test_recall_is_bounded_by_a_time_window(self, branch, menu) -> None:
        """A recall must not be usable to quietly rewrite yesterday."""
        from apps.core.exceptions import AppError

        ticket = self._ticket(branch, menu)
        services.transition(ticket, TicketStatus.PREPARING)
        services.transition(ticket, TicketStatus.READY)
        ticket = services.transition(ticket, TicketStatus.SERVED)

        KitchenTicket.objects.filter(pk=ticket.pk).update(
            served_at=timezone.now() - timedelta(hours=3)
        )
        ticket.refresh_from_db()

        with pytest.raises(AppError) as exc:
            services.recall(ticket)
        assert exc.value.code == "RECALL_WINDOW_ELAPSED"


class TestOrderStatusFollowsTickets:
    def test_order_is_ready_only_when_every_ticket_is(self, branch, menu) -> None:
        """
        A customer whose coffee is done but whose cake is not has not had their
        order completed.
        """
        order = order_services.open_order(branch=branch)
        order_services.apply_events(
            order, [add(order, menu["cappuccino"]), add(order, menu["cake"])]
        )
        fire(order)
        order.refresh_from_db()
        assert order.status == OrderStatus.IN_KITCHEN

        coffee, dessert = list(order.kitchen_tickets.order_by("station__code"))

        for ticket in (coffee,):
            services.transition(ticket, TicketStatus.PREPARING)
            services.transition(ticket, TicketStatus.READY)
        order.refresh_from_db()
        assert order.status == OrderStatus.IN_KITCHEN, "one station done is not the order done"

        services.transition(dessert, TicketStatus.PREPARING)
        services.transition(dessert, TicketStatus.READY)
        order.refresh_from_db()
        assert order.status == OrderStatus.READY

    def test_serving_every_ticket_serves_the_order(self, branch, menu) -> None:
        order = order_services.open_order(branch=branch)
        order_services.apply_events(order, [add(order, menu["cappuccino"])])
        fire(order)

        ticket = order.kitchen_tickets.get()
        services.transition(ticket, TicketStatus.PREPARING)
        services.transition(ticket, TicketStatus.READY)
        services.transition(ticket, TicketStatus.SERVED)

        order.refresh_from_db()
        assert order.status == OrderStatus.SERVED

    def test_a_paid_order_is_not_dragged_backwards(self, branch, menu) -> None:
        """Ticket activity must never reopen a settled order."""
        from apps.payments.models import PaymentMethod
        from apps.payments.services import take_payment

        order = order_services.open_order(branch=branch)
        order_services.apply_events(order, [add(order, menu["cappuccino"])])
        fire(order)
        order.refresh_from_db()

        method = PaymentMethod.objects.create(
            organization=order.organization,
            branch=branch,
            code="CASH",
            name_ar="نقدي",
            counts_as_cash=True,
        )
        take_payment(
            order=order,
            method=method,
            amount=order.grand_total,
            idempotency_key=str(uuid.uuid4()),
        )

        ticket = order.kitchen_tickets.get()
        services.transition(ticket, TicketStatus.PREPARING)
        services.transition(ticket, TicketStatus.READY)

        order.refresh_from_db()
        assert order.status == OrderStatus.PAID


class TestPerformance:
    def test_prep_times_are_reported_per_station(self, branch, menu) -> None:
        for variant in (menu["cappuccino"], menu["cake"]):
            order = order_services.open_order(branch=branch)
            order_services.apply_events(order, [add(order, variant)])
            fire(order)
            ticket = order.kitchen_tickets.get()
            services.transition(ticket, TicketStatus.PREPARING)
            services.transition(ticket, TicketStatus.READY)

        report = services.performance(branch)
        assert set(report) == {"بار القهوة", "الحلويات"}
        assert report["بار القهوة"]["count"] == 1
        assert "average_seconds" in report["بار القهوة"]
        assert "late_percent" in report["بار القهوة"]


class TestKitchenAPI:
    @pytest.fixture
    def kds(self, make_user, authed, branch):
        return authed(make_user(role="KITCHEN", branch=branch), branch=branch)

    def _fired(self, branch, menu):
        order = order_services.open_order(branch=branch)
        order_services.apply_events(order, [add(order, menu["cappuccino"])])
        fire(order)
        return order

    def test_the_board_lists_open_tickets(self, branch, menu, kds) -> None:
        self._fired(branch, menu)
        rows = kds.get("/api/v1/kitchen/tickets/").json()["data"]

        assert len(rows) == 1
        assert rows[0]["station_name"] == "بار القهوة"
        assert rows[0]["lines"][0]["name"] == "كابتشينو"
        assert rows[0]["is_late"] is False

    def test_advancing_a_ticket(self, branch, menu, kds) -> None:
        order = self._fired(branch, menu)
        ticket = order.kitchen_tickets.get()

        assert (
            kds.post(f"/api/v1/kitchen/tickets/{ticket.id}/start/", {}, format="json").json()[
                "data"
            ]["status"]
            == "PREPARING"
        )
        assert (
            kds.post(f"/api/v1/kitchen/tickets/{ticket.id}/ready/", {}, format="json").json()[
                "data"
            ]["status"]
            == "READY"
        )

    def test_an_illegal_transition_returns_409(self, branch, menu, kds) -> None:
        order = self._fired(branch, menu)
        ticket = order.kitchen_tickets.get()

        response = kds.post(f"/api/v1/kitchen/tickets/{ticket.id}/served/", {}, format="json")
        assert response.status_code == 409
        assert response.json()["code"] == "INVALID_STATE_TRANSITION"

    def test_per_line_readiness_completes_the_ticket(self, branch, menu, kds) -> None:
        order = order_services.open_order(branch=branch)
        order_services.apply_events(order, [add(order, menu["cappuccino"])])
        fire(order)
        ticket = order.kitchen_tickets.get()
        line = ticket.lines.get()

        services.transition(ticket, TicketStatus.PREPARING)
        response = kds.post(
            f"/api/v1/kitchen/tickets/{ticket.id}/lines/{line.id}/ready/", {}, format="json"
        )
        assert response.json()["data"]["status"] == "READY"

    def test_kitchen_staff_cannot_see_money(self, branch, menu, kds) -> None:
        """docs/05 exclusion #3, enforced end to end."""
        assert kds.get("/api/v1/payments/").status_code == 403
        assert kds.get("/api/v1/shifts/").status_code == 403

    def test_tickets_are_scoped_to_the_branch(
        self, branch, menu, make_user, authed, other_organization, other_branch
    ) -> None:
        self._fired(branch, menu)
        outsider = make_user(
            email="out@other.test", role="KITCHEN", org=other_organization, branch=other_branch
        )
        client = authed(outsider, branch=other_branch)
        assert client.get("/api/v1/kitchen/tickets/").json()["data"] == []
