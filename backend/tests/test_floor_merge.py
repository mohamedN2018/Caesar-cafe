"""
Merging two tables into one bill.

The case is ordinary and was unserveable: a group of eight arrives, two
four-tops are pushed together, and at the end they want one bill. Without merge
a waiter either splits the party across two payments or re-rings every item onto
one table — and re-ringing is how a round of drinks goes missing.

Merging is a **money** operation. It moves orders between records, and afterwards
there is one payment where there would have been two. So it has its own
permission, its own audit entry, and a row lock.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from apps.catalog.models import Category, Product, ProductVariant
from apps.floor.models import Area, Table, TableSession, TableStatus
from apps.orders.models import Order

pytestmark = pytest.mark.django_db


@pytest.fixture
def area(organization, branch):
    return Area.objects.create(organization=organization, branch=branch, name_ar="الصالة")


@pytest.fixture
def terrace(organization, branch):
    return Area.objects.create(organization=organization, branch=branch, name_ar="التراس")


@pytest.fixture
def tables(area):
    return [
        Table.objects.create(area=area, number=str(index), seats=4, status=TableStatus.OCCUPIED)
        for index in range(1, 4)
    ]


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
def manager(make_user):
    return make_user(role="BRANCH_MANAGER")


@pytest.fixture
def client(authed, manager, branch):
    return authed(manager, branch=branch)


def seat(table, guests: int = 2) -> TableSession:
    return TableSession.objects.create(table=table, guest_count=guests)


def order_on(session, branch, variant, quantity: int = 1) -> Order:
    from apps.orders import services as order_services
    from apps.orders.models import EventType

    order = order_services.open_order(branch=branch, table_session=session)
    order_services.apply_events(
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
    order.refresh_from_db()
    return order


def merge(client, source, target):
    return client.post(
        f"/api/v1/floor/sessions/{source.id}/merge/", {"into": str(target.id)}, format="json"
    )


class TestMerging:
    def test_orders_move_to_the_surviving_session(self, client, branch, tables, variant) -> None:
        first, second = seat(tables[0]), seat(tables[1])
        moved = order_on(first, branch, variant)
        stayed = order_on(second, branch, variant)

        assert merge(client, first, second).status_code == 200

        moved.refresh_from_db()
        stayed.refresh_from_db()
        assert moved.table_session_id == second.id
        assert stayed.table_session_id == second.id

    def test_the_source_session_closes(self, client, tables) -> None:
        first, second = seat(tables[0]), seat(tables[1])
        merge(client, first, second)

        first.refresh_from_db()
        assert first.closed_at is not None
        second.refresh_from_db()
        assert second.closed_at is None

    def test_the_freed_table_is_available_not_dirty(self, client, tables) -> None:
        """
        The party is still in the room and the crockery went with them. Marking
        it CLEANING would send somebody to wipe a table nobody left.
        """
        first, second = seat(tables[0]), seat(tables[1])
        merge(client, first, second)

        tables[0].refresh_from_db()
        assert tables[0].status == TableStatus.AVAILABLE

    def test_the_party_is_combined(self, client, tables) -> None:
        """The floor view draws chairs from this, so it has to be the real number."""
        first, second = seat(tables[0], guests=3), seat(tables[1], guests=4)
        merge(client, first, second)

        second.refresh_from_db()
        assert second.guest_count == 7

    def test_one_bill_where_there_were_two(self, client, branch, tables, variant) -> None:
        first, second = seat(tables[0]), seat(tables[1])
        order_on(first, branch, variant, quantity=2)
        order_on(second, branch, variant, quantity=1)

        merge(client, first, second)

        second.refresh_from_db()
        total = sum(order.grand_total for order in second.orders.all())
        assert total == Decimal("205.20"), "3 × 60.00 plus 14% VAT, on one session"

    def test_it_is_audited(self, client, tables) -> None:
        from apps.audit.models import AuditLog

        first, second = seat(tables[0]), seat(tables[1])
        merge(client, first, second)

        entry = AuditLog.objects.filter(action="floor.sessions_merged").latest("occurred_at")
        assert entry.detail["from_table"] == tables[0].number
        assert entry.detail["to_table"] == tables[1].number


class TestWhatItRefuses:
    def test_a_session_cannot_be_merged_into_itself(self, client, tables) -> None:
        session = seat(tables[0])
        response = merge(client, session, session)

        assert response.status_code == 400
        assert response.json()["code"] == "SAME_SESSION"

    def test_a_closed_session_cannot_be_merged(self, client, tables) -> None:
        from django.utils import timezone

        first, second = seat(tables[0]), seat(tables[1])
        second.closed_at = timezone.now()
        second.save(update_fields=["closed_at"])

        response = merge(client, first, second)

        assert response.status_code == 404, "a closed session is not an open one"
        first.refresh_from_db()
        assert first.closed_at is None, "and the live one is untouched"

    def test_tables_in_different_areas_are_refused(
        self, client, organization, area, terrace
    ) -> None:
        """
        Two tables in different rooms were not pushed together, so this is
        almost certainly the wrong pair picked from a list.
        """
        inside = Table.objects.create(area=area, number="A1", seats=4)
        outside = Table.objects.create(area=terrace, number="B1", seats=4)

        response = merge(client, seat(inside), seat(outside))

        assert response.status_code == 400
        assert response.json()["code"] == "DIFFERENT_AREAS"

    def test_it_needs_its_own_permission(self, authed, make_user, branch, tables) -> None:
        """
        Transferring moves a party; merging combines two bills. A role that may
        do the first is not thereby allowed the second.
        """
        from apps.authz import catalog
        from apps.authz.models import Role

        user = make_user(email="w@caesar.test", role="WAITER")
        role = Role.objects.get(organization=user.organization, code="WAITER")
        held = set(catalog.SYSTEM_ROLES["WAITER"]["permissions"])
        role.set_permissions(sorted(held | {"floor.transfer"} - {"floor.merge"}))

        waiter = authed(user, branch=branch)
        first, second = seat(tables[0]), seat(tables[1])

        assert merge(waiter, first, second).status_code == 403
        first.refresh_from_db()
        assert first.closed_at is None

    def test_another_tenants_session_is_not_mergeable(
        self, client, tables, other_organization, other_branch
    ) -> None:
        foreign_area = Area.objects.create(
            organization=other_organization, branch=other_branch, name_ar="صالة أخرى"
        )
        foreign = seat(Table.objects.create(area=foreign_area, number="X1", seats=4))

        response = merge(client, seat(tables[0]), foreign)

        assert response.status_code == 404
        foreign.refresh_from_db()
        assert foreign.closed_at is None


def test_merging_moves_no_money(client, branch, tables, variant) -> None:
    """
    The bills combine; the totals do not change. A merge that quietly re-priced
    anything would be the worst possible way to find out about a rounding bug.
    """
    first, second = seat(tables[0]), seat(tables[1])
    moved = order_on(first, branch, variant)
    before = moved.grand_total

    merge(client, first, second)

    moved.refresh_from_db()
    assert moved.grand_total == before
