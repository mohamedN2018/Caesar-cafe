"""
Orders as an event stream, and the money that falls out of them.

The two properties these protect: replaying a pushed event never duplicates a
line, and the total a customer is charged comes from the same module the
Desktop vendors.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from apps.catalog.models import Category, Modifier, ModifierGroup, Product, ProductVariant
from apps.configuration import resolver
from apps.configuration.registry import Scope
from apps.core.exceptions import InvalidStateTransition
from apps.floor.models import Area, Table, TableSession
from apps.orders import services, state
from apps.orders.models import EventType, ItemStatus, Order, OrderStatus
from apps.payments import services as payment_services
from apps.payments.models import Invoice, Payment, PaymentMethod
from apps.shifts import services as shift_services
from apps.shifts.models import CashMovementType, ShiftStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def menu(organization, branch):
    category = Category.objects.create(organization=organization, branch=branch, name_ar="قهوة")
    cappuccino = Product.objects.create(
        organization=organization,
        branch=branch,
        category=category,
        sku="CAPP",
        name_ar="كابتشينو",
    )
    turkish = Product.objects.create(
        organization=organization,
        branch=branch,
        category=category,
        sku="TURK",
        name_ar="قهوة تركي",
    )
    group = ModifierGroup.objects.create(organization=organization, branch=branch, name_ar="إضافات")
    shot = Modifier.objects.create(
        group=group, name_ar="شوت اسبريسو زيادة", price_delta=Decimal("10.00")
    )
    return {
        "cappuccino": ProductVariant.objects.create(
            product=cappuccino, sku="CAPP-M", price=Decimal("60.00"), is_default=True
        ),
        "turkish": ProductVariant.objects.create(
            product=turkish, sku="TURK-S", price=Decimal("40.00"), is_default=True
        ),
        "shot": shot,
    }


@pytest.fixture
def cash(organization, branch):
    return PaymentMethod.objects.create(
        organization=organization,
        branch=branch,
        code="CASH",
        name_ar="نقدي",
        counts_as_cash=True,
        opens_drawer=True,
    )


@pytest.fixture
def card(organization, branch):
    return PaymentMethod.objects.create(
        organization=organization,
        branch=branch,
        code="CARD",
        name_ar="فيزا",
        counts_as_cash=False,
    )


def add_item(order, variant, quantity=1, **payload):
    return {
        "id": str(uuid.uuid4()),
        "type": EventType.ITEM_ADDED,
        "payload": {
            "line_id": str(uuid.uuid4()),
            "variant_id": str(variant.id),
            "quantity": str(quantity),
            **payload,
        },
    }


class TestEventStream:
    def test_opening_records_the_first_event(self, branch) -> None:
        order = services.open_order(branch=branch)
        assert order.status == OrderStatus.OPEN
        assert order.events.filter(event_type=EventType.ORDER_OPENED).exists()

    def test_sequences_are_gapless(self, branch, menu) -> None:
        order = services.open_order(branch=branch)
        services.apply_events(
            order,
            [add_item(order, menu["cappuccino"]), add_item(order, menu["turkish"])],
        )
        sequences = list(order.events.order_by("sequence").values_list("sequence", flat=True))
        assert sequences == list(range(1, len(sequences) + 1))

    def test_replaying_an_event_does_not_duplicate_the_line(self, branch, menu) -> None:
        """
        The property that lets a timed-out Desktop retry a whole batch: the
        event id is the idempotency key.
        """
        order = services.open_order(branch=branch)
        event = add_item(order, menu["cappuccino"], quantity=2)

        first = services.apply_events(order, [event])
        second = services.apply_events(order, [event])

        assert first.applied == [event["id"]]
        assert second.applied == []
        assert second.skipped == [event["id"]]
        assert order.items.count() == 1

    def test_a_mixed_batch_applies_only_the_new_events(self, branch, menu) -> None:
        order = services.open_order(branch=branch)
        old = add_item(order, menu["cappuccino"])
        services.apply_events(order, [old])

        new = add_item(order, menu["turkish"])
        result = services.apply_events(order, [old, new])

        assert result.applied == [new["id"]]
        assert result.skipped == [old["id"]]
        assert order.items.count() == 2

    def test_an_unknown_event_type_is_rejected(self, branch) -> None:
        order = services.open_order(branch=branch)
        with pytest.raises(services.EventRejected):
            services.apply_events(
                order, [{"id": str(uuid.uuid4()), "type": "TELEPORT_ORDER", "payload": {}}]
            )

    def test_voiding_marks_rather_than_deletes(self, branch, menu) -> None:
        """A deleted line is an unexplained gap in a financial record."""
        order = services.open_order(branch=branch)
        event = add_item(order, menu["cappuccino"])
        services.apply_events(order, [event])
        line_id = event["payload"]["line_id"]

        services.apply_events(
            order,
            [
                {
                    "id": str(uuid.uuid4()),
                    "type": EventType.ITEM_VOIDED,
                    "payload": {"line_id": line_id, "reason": "خطأ في الإدخال"},
                }
            ],
        )

        item = order.items.get(line_id=line_id)
        assert item.status == ItemStatus.VOIDED
        assert item.void_reason == "خطأ في الإدخال"
        assert order.items.count() == 1  # still there, still auditable

    def test_the_stream_explains_the_total(self, branch, menu) -> None:
        """'Why is this bill 204.29?' is answerable by replay."""
        order = services.open_order(branch=branch)
        services.apply_events(order, [add_item(order, menu["cappuccino"], 2)])

        events = list(order.events.order_by("sequence"))
        assert [e.event_type for e in events] == [
            EventType.ORDER_OPENED,
            EventType.ITEM_ADDED,
        ]


class TestTotals:
    def _rules(self, branch, **values):
        for key, value in values.items():
            resolver.set_value(f"finance.{key}", value, scope=Scope.BRANCH, scope_id=branch.id)

    def test_matches_the_documented_example(self, branch, menu) -> None:
        """
        2× cappuccino + 1× turkish, 12% service, 14% VAT → 204.29.
        The same figure as the POS mock-up in docs/04 and the golden fixture.
        """
        self._rules(branch, service_enabled=True, service_percent="12.00")

        order = services.open_order(branch=branch)
        services.apply_events(
            order,
            [add_item(order, menu["cappuccino"], 2), add_item(order, menu["turkish"], 1)],
        )
        order.refresh_from_db()

        assert order.subtotal == Decimal("160.00")
        assert order.service_total == Decimal("19.20")
        assert order.tax_total == Decimal("25.09")
        assert order.grand_total == Decimal("204.29")

    def test_a_voided_line_leaves_the_total(self, branch, menu) -> None:
        order = services.open_order(branch=branch)
        keep = add_item(order, menu["cappuccino"], 2)
        drop = add_item(order, menu["turkish"], 1)
        services.apply_events(order, [keep, drop])

        services.apply_events(
            order,
            [
                {
                    "id": str(uuid.uuid4()),
                    "type": EventType.ITEM_VOIDED,
                    "payload": {"line_id": drop["payload"]["line_id"], "reason": "x"},
                }
            ],
        )
        order.refresh_from_db()
        assert order.subtotal == Decimal("120.00")

    def test_modifiers_raise_the_line(self, branch, menu) -> None:
        order = services.open_order(branch=branch)
        services.apply_events(
            order,
            [add_item(order, menu["cappuccino"], 2, modifiers=[str(menu["shot"].id)])],
        )
        order.refresh_from_db()
        assert order.subtotal == Decimal("140.00")  # (60 + 10) × 2

    def test_an_order_discount_applies_after_line_discounts(self, branch, menu) -> None:
        order = services.open_order(branch=branch)
        services.apply_events(order, [add_item(order, menu["cappuccino"], 2)])
        services.apply_events(
            order,
            [
                {
                    "id": str(uuid.uuid4()),
                    "type": EventType.DISCOUNT_APPLIED,
                    "payload": {"percent": "10", "reason": "عميل دائم"},
                }
            ],
        )
        order.refresh_from_db()
        assert order.discount_total == Decimal("12.00")
        assert order.grand_total == Decimal("123.12")

    def test_an_invalid_discount_is_rejected(self, branch, menu) -> None:
        order = services.open_order(branch=branch)
        with pytest.raises(services.EventRejected):
            services.apply_events(
                order,
                [
                    {
                        "id": str(uuid.uuid4()),
                        "type": EventType.DISCOUNT_APPLIED,
                        "payload": {"percent": "150"},
                    }
                ],
            )

    def test_tax_rules_are_snapshotted_at_open_time(self, branch, menu) -> None:
        """A mid-service VAT change must not rewrite a bill in progress."""
        order = services.open_order(branch=branch)
        services.apply_events(order, [add_item(order, menu["cappuccino"], 2)])
        before = Order.objects.get(pk=order.pk).grand_total

        resolver.set_value("finance.vat_percent", "25.00", scope=Scope.BRANCH, scope_id=branch.id)
        services.recalculate(Order.objects.get(pk=order.pk))

        assert Order.objects.get(pk=order.pk).grand_total == before

    def test_a_client_cannot_dictate_the_total(self, branch, menu) -> None:
        """Totals are computed, never received."""
        order = services.open_order(branch=branch)
        services.apply_events(
            order, [add_item(order, menu["cappuccino"], 1, unit_price="1.00", line_total="1.00")]
        )
        order.refresh_from_db()
        assert order.subtotal == Decimal("60.00")


class TestStateMachine:
    def test_firing_moves_to_the_kitchen(self, branch, menu) -> None:
        order = services.open_order(branch=branch)
        services.apply_events(order, [add_item(order, menu["cappuccino"])])
        services.apply_events(
            order, [{"id": str(uuid.uuid4()), "type": EventType.ORDER_FIRED, "payload": {}}]
        )
        order.refresh_from_db()
        assert order.status == OrderStatus.IN_KITCHEN
        assert order.items.first().fired_at is not None

    def test_firing_nothing_is_rejected(self, branch) -> None:
        order = services.open_order(branch=branch)
        with pytest.raises(services.EventRejected):
            services.apply_events(
                order, [{"id": str(uuid.uuid4()), "type": EventType.ORDER_FIRED, "payload": {}}]
            )

    @pytest.mark.parametrize(
        ("current", "target", "allowed"),
        [
            (OrderStatus.OPEN, OrderStatus.PAID, True),
            (OrderStatus.OPEN, OrderStatus.IN_KITCHEN, True),
            (OrderStatus.PAID, OrderStatus.OPEN, False),
            (OrderStatus.PAID, OrderStatus.REFUNDED, True),
            (OrderStatus.CANCELLED, OrderStatus.OPEN, False),
            (OrderStatus.REFUNDED, OrderStatus.PAID, False),
            (OrderStatus.SERVED, OrderStatus.IN_KITCHEN, False),
        ],
    )
    def test_transition_table(self, current, target, allowed) -> None:
        assert state.can_transition(current, target) is allowed

    def test_reopening_a_paid_order_is_unreachable(self, branch, menu, cash) -> None:
        """Not an edit — a refund plus a new order, leaving two records."""
        order = services.open_order(branch=branch)
        services.apply_events(order, [add_item(order, menu["cappuccino"])])
        order.refresh_from_db()
        payment_services.take_payment(
            order=order,
            method=cash,
            amount=order.grand_total,
            idempotency_key=str(uuid.uuid4()),
        )
        order.refresh_from_db()

        with pytest.raises(InvalidStateTransition):
            state.assert_transition(order.status, OrderStatus.OPEN)

    def test_a_paid_order_cannot_take_new_items(self, branch, menu, cash) -> None:
        order = services.open_order(branch=branch)
        services.apply_events(order, [add_item(order, menu["cappuccino"])])
        order.refresh_from_db()
        payment_services.take_payment(
            order=order,
            method=cash,
            amount=order.grand_total,
            idempotency_key=str(uuid.uuid4()),
        )
        order.refresh_from_db()

        with pytest.raises(InvalidStateTransition):
            services.apply_events(order, [add_item(order, menu["turkish"])])

    def test_voiding_records_the_reason(self, branch, menu) -> None:
        order = services.open_order(branch=branch)
        services.apply_events(order, [add_item(order, menu["cappuccino"])])
        services.void_order(order, reason="العميل غادر")

        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELLED
        assert order.void_reason == "العميل غادر"
        assert order.events.filter(event_type=EventType.ORDER_VOIDED).exists()


class TestPayments:
    def _order(self, branch, menu, quantity=2):
        order = services.open_order(branch=branch)
        services.apply_events(order, [add_item(order, menu["cappuccino"], quantity)])
        order.refresh_from_db()
        return order

    def test_full_payment_settles_and_closes(self, branch, menu, cash) -> None:
        order = self._order(branch, menu)
        payment_services.take_payment(
            order=order,
            method=cash,
            amount=order.grand_total,
            tendered=Decimal("200.00"),
            idempotency_key=str(uuid.uuid4()),
        )
        order.refresh_from_db()

        assert order.status == OrderStatus.PAID
        assert order.balance_due == Decimal("0.00")
        assert order.closed_at is not None

    def test_change_is_computed(self, branch, menu, cash) -> None:
        order = self._order(branch, menu)
        payment = payment_services.take_payment(
            order=order,
            method=cash,
            amount=order.grand_total,
            tendered=Decimal("200.00"),
            idempotency_key=str(uuid.uuid4()),
        )
        assert payment.change_given == Decimal("200.00") - order.grand_total

    def test_a_replayed_payment_charges_once(self, branch, menu, cash) -> None:
        """The property §51 exists for: a retried request after a timeout."""
        order = self._order(branch, menu)
        key = str(uuid.uuid4())

        first = payment_services.take_payment(
            order=order, method=cash, amount=order.grand_total, idempotency_key=key
        )
        second = payment_services.take_payment(
            order=order, method=cash, amount=order.grand_total, idempotency_key=key
        )

        assert first.id == second.id
        assert Payment.objects.filter(order=order).count() == 1
        order.refresh_from_db()
        assert order.paid_total == order.grand_total

    def test_split_payment_settles_only_when_complete(self, branch, menu, cash, card) -> None:
        order = self._order(branch, menu)
        half = (order.grand_total / 2).quantize(Decimal("0.01"))

        payment_services.take_payment(
            order=order, method=cash, amount=half, idempotency_key=str(uuid.uuid4())
        )
        order.refresh_from_db()
        assert order.status != OrderStatus.PAID

        payment_services.take_payment(
            order=order,
            method=card,
            amount=order.balance_due,
            reference="APPROVED",
            idempotency_key=str(uuid.uuid4()),
        )
        order.refresh_from_db()
        assert order.status == OrderStatus.PAID

    def test_overpayment_is_refused(self, branch, menu, cash) -> None:
        order = self._order(branch, menu)
        with pytest.raises(payment_services.Overpayment):
            payment_services.take_payment(
                order=order,
                method=cash,
                amount=order.grand_total + Decimal("10"),
                idempotency_key=str(uuid.uuid4()),
            )

    def test_tender_below_the_amount_is_refused(self, branch, menu, cash) -> None:
        from apps.core.exceptions import AppError

        order = self._order(branch, menu)
        with pytest.raises(AppError):
            payment_services.take_payment(
                order=order,
                method=cash,
                amount=order.grand_total,
                tendered=Decimal("1.00"),
                idempotency_key=str(uuid.uuid4()),
            )

    def test_a_method_requiring_a_reference_enforces_it(self, branch, menu, card) -> None:
        from apps.core.exceptions import AppError

        card.requires_reference = True
        card.save()
        order = self._order(branch, menu)

        with pytest.raises(AppError):
            payment_services.take_payment(
                order=order,
                method=card,
                amount=order.grand_total,
                idempotency_key=str(uuid.uuid4()),
            )


class TestInvoice:
    def test_settling_issues_a_frozen_invoice(self, branch, menu, cash) -> None:
        order = services.open_order(branch=branch)
        services.apply_events(order, [add_item(order, menu["cappuccino"], 2)])
        order.refresh_from_db()
        payment_services.take_payment(
            order=order,
            method=cash,
            amount=order.grand_total,
            idempotency_key=str(uuid.uuid4()),
        )

        invoice = Invoice.objects.get(order=order)
        assert invoice.serial.startswith(branch.code)
        assert invoice.snapshot["grand_total"] == str(order.grand_total)
        assert invoice.snapshot["items"][0]["name"] == "كابتشينو"

    def test_the_snapshot_survives_a_later_price_change(self, branch, menu, cash) -> None:
        """A reprint two years later must be byte-identical."""
        order = services.open_order(branch=branch)
        services.apply_events(order, [add_item(order, menu["cappuccino"], 1)])
        order.refresh_from_db()
        payment_services.take_payment(
            order=order,
            method=cash,
            amount=order.grand_total,
            idempotency_key=str(uuid.uuid4()),
        )
        frozen = Invoice.objects.get(order=order).snapshot

        menu["cappuccino"].price = Decimal("95.00")
        menu["cappuccino"].save()

        assert Invoice.objects.get(order=order).snapshot == frozen
        assert frozen["items"][0]["unit_price"] == "60.00"

    def test_invoice_numbers_are_sequential(self, branch, menu, cash) -> None:
        numbers = []
        for _ in range(3):
            order = services.open_order(branch=branch)
            services.apply_events(order, [add_item(order, menu["turkish"], 1)])
            order.refresh_from_db()
            payment_services.take_payment(
                order=order,
                method=cash,
                amount=order.grand_total,
                idempotency_key=str(uuid.uuid4()),
            )
            numbers.append(Invoice.objects.get(order=order).invoice_number)

        assert numbers == sorted(numbers)
        assert len(set(numbers)) == 3


class TestRefund:
    def _paid(self, branch, menu, cash):
        order = services.open_order(branch=branch)
        services.apply_events(order, [add_item(order, menu["cappuccino"], 2)])
        order.refresh_from_db()
        payment_services.take_payment(
            order=order,
            method=cash,
            amount=order.grand_total,
            idempotency_key=str(uuid.uuid4()),
        )
        order.refresh_from_db()
        return order

    def test_a_full_refund_marks_the_order(self, branch, menu, cash) -> None:
        order = self._paid(branch, menu, cash)
        payment_services.refund(
            order=order,
            amount=order.paid_total,
            reason="شكوى عميل",
            idempotency_key=str(uuid.uuid4()),
        )
        order.refresh_from_db()
        assert order.status == OrderStatus.REFUNDED

    def test_a_partial_refund_leaves_the_order_paid(self, branch, menu, cash) -> None:
        order = self._paid(branch, menu, cash)
        payment_services.refund(
            order=order,
            amount=Decimal("20.00"),
            reason="صنف ناقص",
            idempotency_key=str(uuid.uuid4()),
        )
        order.refresh_from_db()
        assert order.status == OrderStatus.PAID

    def test_refunding_more_than_was_paid_is_refused(self, branch, menu, cash) -> None:
        from apps.core.exceptions import AppError

        order = self._paid(branch, menu, cash)
        with pytest.raises(AppError):
            payment_services.refund(
                order=order,
                amount=order.paid_total + Decimal("1"),
                reason="x",
                idempotency_key=str(uuid.uuid4()),
            )

    def test_a_refund_never_edits_the_original_payment(self, branch, menu, cash) -> None:
        """Two records, not one silently altered — auditable correction."""
        order = self._paid(branch, menu, cash)
        payment = Payment.objects.get(order=order)
        original = payment.amount

        payment_services.refund(
            order=order,
            amount=Decimal("20.00"),
            reason="x",
            idempotency_key=str(uuid.uuid4()),
        )
        payment.refresh_from_db()
        assert payment.amount == original

    def test_a_replayed_refund_returns_money_once(self, branch, menu, cash) -> None:
        order = self._paid(branch, menu, cash)
        key = str(uuid.uuid4())
        first = payment_services.refund(
            order=order, amount=Decimal("20.00"), reason="x", idempotency_key=key
        )
        second = payment_services.refund(
            order=order, amount=Decimal("20.00"), reason="x", idempotency_key=key
        )
        assert first.id == second.id
        assert order.refunds.count() == 1


class TestShifts:
    def test_one_open_shift_per_device(self, branch) -> None:
        device = uuid.uuid4()
        shift_services.open_shift(branch=branch, device_id=device)
        with pytest.raises(shift_services.ShiftAlreadyOpen):
            shift_services.open_shift(branch=branch, device_id=device)

    def test_expected_cash_counts_only_cash_methods(
        self, branch, menu, cash, card, make_user
    ) -> None:
        """A card payment must never inflate what the drawer should hold."""
        user = make_user(role="CASHIER")
        shift = shift_services.open_shift(
            branch=branch, user=user, device_id=uuid.uuid4(), opening_cash=Decimal("500")
        )

        for method in (cash, card):
            order = services.open_order(branch=branch, shift=shift, user=user)
            services.apply_events(order, [add_item(order, menu["cappuccino"], 1)])
            order.refresh_from_db()
            payment_services.take_payment(
                order=order,
                method=method,
                amount=order.grand_total,
                shift=shift,
                idempotency_key=str(uuid.uuid4()),
                user=user,
                reference="X",
            )

        totals = shift_services.compute_totals(shift)
        assert totals.cash_sales == Decimal("68.40")
        assert totals.non_cash_sales == Decimal("68.40")
        assert totals.expected_cash == Decimal("568.40")

    def test_cash_movements_shift_the_expectation(self, branch, make_user) -> None:
        shift = shift_services.open_shift(
            branch=branch, device_id=uuid.uuid4(), opening_cash=Decimal("500")
        )
        shift_services.record_cash_movement(
            shift=shift,
            movement_type=CashMovementType.EXPENSE,
            amount=Decimal("120"),
            reason="شراء مياه",
        )
        assert shift_services.compute_totals(shift).expected_cash == Decimal("380.00")

    def test_closing_records_the_variance(self, branch, make_user) -> None:
        shift = shift_services.open_shift(
            branch=branch, device_id=uuid.uuid4(), opening_cash=Decimal("500")
        )
        closed = shift_services.close_shift(
            shift=shift,
            counted_cash=Decimal("480"),
            reason="نقص",
        )
        assert closed.status == ShiftStatus.CLOSED
        assert closed.variance == Decimal("-20.00")
        assert closed.z_report["is_final"] is True

    def test_a_large_variance_needs_approval(self, branch, make_user) -> None:
        from apps.core.exceptions import AppError

        shift = shift_services.open_shift(
            branch=branch, device_id=uuid.uuid4(), opening_cash=Decimal("500")
        )
        with pytest.raises(AppError) as exc:
            shift_services.close_shift(shift=shift, counted_cash=Decimal("100"), reason="نقص كبير")
        assert exc.value.code == "VARIANCE_EXCEEDS_LIMIT"

        approver = make_user(role="BRANCH_MANAGER")
        closed = shift_services.close_shift(
            shift=shift, counted_cash=Decimal("100"), reason="نقص كبير", approved_by=approver
        )
        assert closed.status == ShiftStatus.CLOSED

    def test_a_shift_with_open_orders_cannot_close(self, branch, menu) -> None:
        from apps.core.exceptions import AppError

        shift = shift_services.open_shift(branch=branch, device_id=uuid.uuid4())
        order = services.open_order(branch=branch, shift=shift)
        services.apply_events(order, [add_item(order, menu["cappuccino"])])

        with pytest.raises(AppError) as exc:
            shift_services.close_shift(shift=shift, counted_cash=Decimal("0"))
        assert exc.value.code == "OPEN_ORDERS_REMAIN"

    def test_the_z_report_is_frozen(self, branch) -> None:
        shift = shift_services.open_shift(
            branch=branch, device_id=uuid.uuid4(), opening_cash=Decimal("500")
        )
        closed = shift_services.close_shift(shift=shift, counted_cash=Decimal("500"))
        assert closed.z_report["expected_cash"] == "500.00"
        assert closed.z_report["counted_cash"] == "500.00"


class TestFloor:
    def test_an_order_can_be_attached_to_a_table(self, organization, branch, menu) -> None:
        area = Area.objects.create(organization=organization, branch=branch, name_ar="الصالة")
        table = Table.objects.create(area=area, number="T-05", seats=4)
        session = TableSession.objects.create(table=table, guest_count=4)

        order = services.open_order(branch=branch, table_session=session)
        assert order.table_session == session
        assert table.open_session == session

    def test_stock_deducts_when_the_order_is_paid(
        self, organization, branch, menu, cash, units_and_recipe
    ) -> None:
        """Server-side only (C6) — the Desktop never writes authoritative stock."""
        from apps.inventory.models import StockLevel

        order = services.open_order(branch=branch)
        services.apply_events(order, [add_item(order, units_and_recipe["variant"], 2)])
        order.refresh_from_db()

        before = StockLevel.objects.get(item=units_and_recipe["beans"]).quantity_on_hand
        payment_services.take_payment(
            order=order,
            method=cash,
            amount=order.grand_total,
            idempotency_key=str(uuid.uuid4()),
        )
        after = StockLevel.objects.get(item=units_and_recipe["beans"]).quantity_on_hand

        assert before - after == Decimal("36.000")  # 18g × 2


@pytest.fixture
def units_and_recipe(organization, branch):
    from apps.inventory.models import InventoryItem, Unit
    from apps.inventory.services import set_opening_balance
    from apps.recipes.models import Recipe, RecipeLine

    gram = Unit.objects.create(organization=organization, code="G", name_ar="جرام")
    beans = InventoryItem.objects.create(
        organization=organization, branch=branch, code="BEANS", name_ar="بن", base_unit=gram
    )
    set_opening_balance(item=beans, quantity=Decimal("5000"), unit_cost=Decimal("0.35"))

    category = Category.objects.create(organization=organization, branch=branch, name_ar="قهوة")
    product = Product.objects.create(
        organization=organization,
        branch=branch,
        category=category,
        sku="ESP",
        name_ar="اسبريسو",
    )
    variant = ProductVariant.objects.create(
        product=product, sku="ESP-S", price=Decimal("45.00"), is_default=True
    )
    recipe = Recipe.objects.create(variant=variant)
    RecipeLine.objects.create(recipe=recipe, item=beans, quantity=Decimal("18"), unit=gram)

    return {"beans": beans, "variant": variant}
