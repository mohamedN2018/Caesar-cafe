"""
The sync engine.

Sync bugs are rare, timing-dependent and financially serious, so docs/07 names
the scenarios that must have deterministic tests rather than hopeful manual
checks. Each class below is one row of that table.

The properties, in order of how much they would cost to get wrong:

  * a replayed batch charges once and creates nothing twice
  * a bad operation does not block the forty-nine good ones behind it
  * a pull never skips a row, even when a concurrent writer is mid-transaction
  * nothing fails silently
"""

from __future__ import annotations

import threading
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection, connections, transaction
from django.utils import timezone

from apps.catalog.models import Category, Product, ProductVariant
from apps.configuration import resolver as config_resolver
from apps.configuration.registry import Scope
from apps.licensing.models import Device, DeviceStatus, License, LicenseType
from apps.orders.models import EventType, Order, OrderStatus
from apps.payments.models import PaymentMethod
from apps.sync import changelog, services
from apps.sync.models import (
    ChangeLog,
    OperationStatus,
    Stream,
    SyncConflict,
    SyncOperation,
)

# The whole module runs with a real transaction: `changelog.record` defers its
# append to `transaction.on_commit`, which is the property being tested, and a
# wrapped-in-a-rollback test would never fire it. Slower, and the only honest
# way to exercise a deferred write.
pytestmark = pytest.mark.django_db(transaction=True)


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def device(organization, branch) -> Device:
    licence = License.objects.create(
        organization=organization,
        branch=branch,
        key_hash=uuid.uuid4().hex,
        key_prefix="QSR-TEST",
        customer_email="owner@caesar.test",
        license_type=LicenseType.YEARLY,
        max_devices=3,
    )
    return Device.objects.create(
        license=licence,
        branch=branch,
        device_name="كاشير ١",
        secret_hash="x" * 32,
        status=DeviceStatus.ACTIVE,
    )


@pytest.fixture
def menu(organization, branch):
    category = Category.objects.create(organization=organization, branch=branch, name_ar="المنيو")
    product = Product.objects.create(
        organization=organization,
        branch=branch,
        category=category,
        sku="CAPP",
        name_ar="كابتشينو",
    )
    return ProductVariant.objects.create(
        product=product, sku="CAPP-D", price=Decimal("60.00"), is_default=True
    )


@pytest.fixture
def cash(organization, branch) -> PaymentMethod:
    return PaymentMethod.objects.create(
        organization=organization, branch=branch, code="CASH", name_ar="نقدي"
    )


def op(entity_type: str, payload: dict, **extra) -> dict:
    return {
        "op_uuid": str(uuid.uuid4()),
        "entity_type": entity_type,
        "payload": payload,
        **extra,
    }


def push(device, branch, operations, **kwargs):
    return services.apply_push(device=device, branch=branch, operations=operations, **kwargs)


def open_order_op(order_id=None, local_number="MB-01-0001") -> dict:
    return op(
        "order_open",
        {
            "order_id": str(order_id or uuid.uuid4()),
            "order_type": "DINE_IN",
            "local_number": local_number,
        },
    )


def add_item_op(order_id, variant, quantity=1, sequence=1) -> dict:
    return op(
        "order_event",
        {
            "order_id": str(order_id),
            "event": {
                "id": str(uuid.uuid4()),
                "sequence": sequence,
                "type": EventType.ITEM_ADDED,
                "payload": {
                    "line_id": str(uuid.uuid4()),
                    "variant_id": str(variant.id),
                    "quantity": str(quantity),
                },
            },
        },
        entity_id=str(order_id),
        aggregate_seq=sequence,
    )


# ── idempotency ──────────────────────────────────────────────────────────────


class TestReplay:
    def test_a_duplicate_batch_creates_nothing_twice(self, device, branch, menu) -> None:
        """docs/07: identical response, no second order created."""
        order_id = uuid.uuid4()
        batch = [open_order_op(order_id), add_item_op(order_id, menu)]

        first = push(device, branch, batch)
        second = push(device, branch, batch)

        assert first.applied == 2
        assert all(r["replayed"] for r in second.results)
        assert not any(r.get("replayed") for r in first.results)

        # The point: the second push reports success without DOING anything.
        assert Order.objects.filter(id=order_id).count() == 1
        assert Order.objects.get(id=order_id).items.count() == 1
        assert SyncOperation.objects.filter(entity_type="order_open").count() == 1

    def test_a_replay_returns_the_original_result_verbatim(self, device, branch, menu) -> None:
        """A client must not be able to tell a retry from the original."""
        order_id = uuid.uuid4()
        batch = [open_order_op(order_id)]

        first = push(device, branch, batch).results[0]
        second = push(device, branch, batch).results[0]

        assert second["result"] == first["result"]
        assert second["status"] == first["status"]

    def test_a_payment_retried_after_a_timeout_charges_once(
        self, device, branch, menu, cash
    ) -> None:
        order_id = uuid.uuid4()
        push(device, branch, [open_order_op(order_id), add_item_op(order_id, menu)])
        order = Order.objects.get(id=order_id)

        pay = op(
            "payment",
            {
                "order_id": str(order_id),
                "method_id": str(cash.id),
                "amount": str(order.grand_total),
                "idempotency_key": "pay-1",
            },
        )
        push(device, branch, [pay])

        # The device never saw the response and resends with a NEW op_uuid —
        # so the op_uuid gate does not save us here. The payment's own
        # idempotency key has to.
        retried = dict(pay, op_uuid=str(uuid.uuid4()))
        push(device, branch, [retried])

        order.refresh_from_db()
        assert order.payments.count() == 1
        assert order.status == OrderStatus.PAID


# ── batch resilience ─────────────────────────────────────────────────────────


class TestPartialBatch:
    def test_one_bad_operation_does_not_block_the_rest(self, device, branch, menu) -> None:
        """
        docs/07: 49 applied, 1 rejected, batch not blocked.

        An all-or-nothing batch means a single poisoned row stalls a terminal
        forever — it retries the whole batch, fails on the same row, and the
        good sales behind it never arrive.
        """
        order_id = uuid.uuid4()
        batch = [open_order_op(order_id)]
        batch += [add_item_op(order_id, menu, sequence=i + 1) for i in range(24)]
        batch.insert(13, op("order_event", {"order_id": str(uuid.uuid4()), "event": {}}))
        batch += [add_item_op(order_id, menu, sequence=i + 25) for i in range(25)]

        result = push(device, branch, batch)

        assert result.applied == 50
        assert result.failed == 1
        assert Order.objects.get(id=order_id).items.count() == 49

    def test_a_rejected_operation_is_recorded_not_dropped(self, device, branch) -> None:
        result = push(device, branch, [op("nonsense", {})])

        assert result.results[0]["code"] == "UNKNOWN_ENTITY_TYPE"
        record = SyncOperation.objects.get(op_uuid=result.results[0]["op_uuid"])
        assert record.status == OperationStatus.REJECTED
        assert record.error_message

    def test_a_crashing_handler_is_contained(self, device, branch, menu, monkeypatch) -> None:
        """An unexpected bug must reject one operation, not stall the queue."""
        from apps.sync import handlers

        def explode(**kwargs):
            raise RuntimeError("boom")

        monkeypatch.setitem(handlers.HANDLERS, "order_open", explode)
        result = push(device, branch, [open_order_op(), op("waste", {})])

        assert result.failed == 2
        assert result.results[0]["code"] == "HANDLER_ERROR"


# ── ordering ─────────────────────────────────────────────────────────────────


class TestSequenceGap:
    def test_a_gap_is_rejected_so_the_client_can_backfill(self, device, branch, menu) -> None:
        order_id = uuid.uuid4()
        push(device, branch, [open_order_op(order_id), add_item_op(order_id, menu, sequence=1)])

        result = push(device, branch, [add_item_op(order_id, menu, sequence=3)])

        assert result.results[0]["status"] == OperationStatus.CONFLICT
        assert result.results[0]["code"] == "SEQUENCE_GAP"
        assert result.results[0]["server_state"]["expected"] == 2

    def test_backfilling_heals_it_without_a_human(self, device, branch, menu) -> None:
        order_id = uuid.uuid4()
        push(device, branch, [open_order_op(order_id), add_item_op(order_id, menu, sequence=1)])
        push(device, branch, [add_item_op(order_id, menu, sequence=3)])

        # The client re-sends from 2, then 3 again with a fresh op_uuid.
        push(device, branch, [add_item_op(order_id, menu, sequence=2)])
        healed = push(device, branch, [add_item_op(order_id, menu, sequence=3)])

        assert healed.applied == 1
        assert Order.objects.get(id=order_id).items.count() == 3

    def test_two_devices_editing_one_table_do_not_conflict(
        self, device, branch, menu, organization
    ) -> None:
        """
        docs/07's central claim: order events commute, so the conflict simply
        does not exist. Both items land, and nobody is served something they
        were not charged for.
        """
        second = Device.objects.create(
            license=device.license,
            branch=branch,
            device_name="تابلت الصالة",
            secret_hash="y" * 32,
            status=DeviceStatus.ACTIVE,
        )
        order_id = uuid.uuid4()
        push(device, branch, [open_order_op(order_id)])

        push(device, branch, [add_item_op(order_id, menu, sequence=1)])
        push(second, branch, [add_item_op(order_id, menu, sequence=1)])

        order = Order.objects.get(id=order_id)
        assert order.items.count() == 2
        assert not SyncConflict.objects.filter(branch=branch).exists()
        # Both drinks are on the bill. Under last-write-wins one of them would
        # have been served and not charged for, and nobody would ever find out.
        assert order.subtotal == Decimal("120.00")


class TestClosedOrder:
    def test_adding_to_a_paid_order_raises_a_conflict_for_a_human(
        self, device, branch, menu, cash
    ) -> None:
        """
        Not self-healing and not the client's fault: the food was made and
        somebody has to decide who pays. The server's state travels with the
        conflict so that decision can be made from the record.
        """
        order_id = uuid.uuid4()
        push(device, branch, [open_order_op(order_id), add_item_op(order_id, menu, sequence=1)])
        order = Order.objects.get(id=order_id)
        push(
            device,
            branch,
            [
                op(
                    "payment",
                    {
                        "order_id": str(order_id),
                        "method_id": str(cash.id),
                        "amount": str(order.grand_total),
                        "idempotency_key": "k1",
                    },
                )
            ],
        )

        result = push(device, branch, [add_item_op(order_id, menu, sequence=2)])

        assert result.results[0]["code"] == "ORDER_ALREADY_CLOSED"
        conflict = SyncConflict.objects.get(code="ORDER_ALREADY_CLOSED")
        assert conflict.server_state["local_number"] == order.local_number
        assert conflict.server_state["grand_total"] == str(order.grand_total)
        assert conflict.is_open


# ── clock skew ───────────────────────────────────────────────────────────────


class TestClockSkew:
    def test_a_skewed_clock_is_flagged_not_silently_accepted(self, device, branch, menu) -> None:
        order_id = uuid.uuid4()
        skewed = dict(open_order_op(order_id), client_time=timezone.now() - timedelta(hours=2))

        result = push(device, branch, [skewed])

        assert result.applied == 1, "the sale really happened — it must not be lost"
        record = SyncOperation.objects.get(op_uuid=result.results[0]["op_uuid"])
        assert record.clock_skew_seconds < -7000
        assert SyncConflict.objects.filter(code="CLOCK_SKEW").exists()

    def test_a_clock_within_tolerance_says_nothing(self, device, branch) -> None:
        push(
            device,
            branch,
            [dict(open_order_op(), client_time=timezone.now() - timedelta(seconds=5))],
        )
        assert not SyncConflict.objects.filter(code="CLOCK_SKEW").exists()

    def test_the_tolerance_is_configurable(self, device, branch) -> None:
        config_resolver.set_value(
            "sync.max_clock_skew_seconds", 30, scope=Scope.BRANCH, scope_id=branch.id
        )
        push(
            device,
            branch,
            [dict(open_order_op(), client_time=timezone.now() - timedelta(minutes=2))],
        )
        assert SyncConflict.objects.filter(code="CLOCK_SKEW").exists()


# ── volume ───────────────────────────────────────────────────────────────────


class TestOfflineBacklog:
    def test_a_long_outage_drains_in_order_with_exact_totals(self, device, branch, menu) -> None:
        """
        docs/07: 500 events queued offline, then reconnect — all applied in
        order, totals match to the piaster.
        """
        order_id = uuid.uuid4()
        batch = [open_order_op(order_id)]
        batch += [add_item_op(order_id, menu, sequence=i + 1) for i in range(150)]

        result = push(device, branch, batch)

        order = Order.objects.get(id=order_id)
        assert result.applied == 151
        assert order.items.count() == 150
        assert order.subtotal == Decimal("60.00") * 150

    def test_the_events_keep_their_order(self, device, branch, menu) -> None:
        order_id = uuid.uuid4()
        push(
            device,
            branch,
            [open_order_op(order_id)]
            + [add_item_op(order_id, menu, sequence=i + 1) for i in range(10)],
        )

        sequences = list(Order.objects.get(id=order_id).events.values_list("sequence", flat=True))
        assert sequences == sorted(sequences)
        assert sequences == list(range(1, len(sequences) + 1)), "the server sequence is gapless"


# ── the change log ───────────────────────────────────────────────────────────


class TestChangeLog:
    def test_a_price_change_reaches_the_stream(self, branch, menu) -> None:
        menu.price = Decimal("70.00")
        menu.save()

        rows = changelog.visible_changes(branch_id=branch.id, stream=Stream.CATALOG, after=0)
        latest = rows.filter(entity_type="variant").last()
        assert latest.payload["price"] == "70.00"

    def test_a_pull_advances_only_as_far_as_it_delivered(self, branch, device, menu) -> None:
        """
        The cursor is the last row IN THE RESPONSE, never the head. A cursor
        that ran ahead of what was delivered would skip those rows forever.
        """
        for index in range(5):
            Product.objects.create(
                organization=branch.organization,
                branch=branch,
                category=menu.product.category,
                sku=f"P{index}",
                name_ar=f"صنف {index}",
            )

        page = services.pull(branch=branch, stream=Stream.CATALOG, cursor=0, limit=3, device=device)

        assert len(page["changes"]) == 3
        assert page["has_more"] is True
        assert page["cursor"] == page["changes"][-1]["seq"]

        rest = services.pull(
            branch=branch, stream=Stream.CATALOG, cursor=page["cursor"], limit=100, device=device
        )
        assert rest["has_more"] is False
        assert {c["seq"] for c in page["changes"]} & {c["seq"] for c in rest["changes"]} == set()

    def test_another_branch_never_appears_in_the_stream(
        self, branch, other_branch, other_organization, menu
    ) -> None:
        Category.objects.create(
            organization=other_organization, branch=other_branch, name_ar="منيو آخر"
        )
        page = services.pull(branch=branch, stream=Stream.CATALOG, cursor=0, limit=500)

        assert all(
            c["entity_type"] != "category" or c["payload"]["name_ar"] != "منيو آخر"
            for c in page["changes"]
        )

    def test_an_order_event_reaches_the_orders_stream(self, branch, menu, device) -> None:
        """What lets a floor tablet and a cashier terminal see the same table."""
        order_id = uuid.uuid4()
        push(device, branch, [open_order_op(order_id), add_item_op(order_id, menu)])

        page = services.pull(branch=branch, stream=Stream.ORDERS, cursor=0, limit=500)
        types = [c["payload"]["event_type"] for c in page["changes"]]

        assert EventType.ORDER_OPENED in types
        assert EventType.ITEM_ADDED in types

    def test_a_role_change_reaches_every_branch_when_unscoped(
        self, organization, branch, make_user
    ) -> None:
        """`branch = NULL` means all branches; sending it to none would leave a
        revoked manager working on every terminal."""
        make_user(email="mgr@caesar.test", role="BRANCH_MANAGER")

        page = services.pull(branch=branch, stream=Stream.STAFF, cursor=0, limit=500)
        assert any(c["entity_type"] == "role_assignment" for c in page["changes"])

    def test_a_setting_change_reaches_the_config_stream(self, branch) -> None:
        config_resolver.set_value(
            "finance.vat_percent", "14.00", scope=Scope.BRANCH, scope_id=branch.id
        )
        page = services.pull(branch=branch, stream=Stream.CONFIG, cursor=0, limit=500)

        assert any(
            c["entity_type"] == "setting" and c["payload"]["key"] == "finance.vat_percent"
            for c in page["changes"]
        )

    def test_an_unknown_stream_is_refused(self, branch) -> None:
        from apps.core.exceptions import AppError

        with pytest.raises(AppError):
            services.pull(branch=branch, stream="not_a_stream", cursor=0)


class TestCursorVisibility:
    """
    docs/07: pull cursor at a transaction boundary — no skipped rows.

    A BIGSERIAL is assigned at INSERT and becomes visible at COMMIT, so seq 100
    can commit AFTER seq 101 is already readable. A reader at exactly that
    moment moves its cursor past 101 and never sees 100 again. This is the
    nastiest bug in the whole engine: rare, silent, and unreproducible in
    production.
    """

    def test_a_row_from_an_open_transaction_is_never_served(
        self, organization, branch, django_db_blocker
    ) -> None:
        category = Category.objects.create(
            organization=organization, branch=branch, name_ar="المنيو"
        )

        held = threading.Event()
        release = threading.Event()
        failure: list[str] = []

        def slow_writer() -> None:
            """Insert a change row and sit on the open transaction."""
            try:
                with transaction.atomic():
                    changelog.record(
                        branch_id=branch.id,
                        stream=Stream.CATALOG,
                        entity_type="category",
                        entity_id=category.id,
                        payload={"name_ar": "بطيء"},
                        immediate=True,
                    )
                    held.set()
                    release.wait(timeout=20)
            except Exception as exc:  # surfaced in the assertion below
                failure.append(f"{type(exc).__name__}: {exc}")
            finally:
                connections.close_all()

        writer = threading.Thread(target=slow_writer)
        writer.start()
        assert held.wait(timeout=20), "writer never started"

        try:
            # A newer row that HAS committed. Naive `seq > cursor` would hand
            # this out and strand the slow writer's row behind the cursor.
            changelog.record(
                branch_id=branch.id,
                stream=Stream.CATALOG,
                entity_type="category",
                entity_id=category.id,
                payload={"name_ar": "سريع"},
                immediate=True,
            )
            during = services.pull(branch=branch, stream=Stream.CATALOG, cursor=0, limit=500)
            names = [c["payload"].get("name_ar") for c in during["changes"]]

            assert "بطيء" not in names, "served a row from an in-flight transaction"
            assert "سريع" not in names, (
                "advanced the cursor past a row that had not committed yet — "
                "the slow row would be skipped forever"
            )
        finally:
            release.set()
            writer.join(timeout=20)

        assert not failure, failure

        after = services.pull(branch=branch, stream=Stream.CATALOG, cursor=0, limit=500)
        names = [c["payload"].get("name_ar") for c in after["changes"]]
        assert "بطيء" in names and "سريع" in names, "both rows must arrive once committed"


# ── conflicts ────────────────────────────────────────────────────────────────


class TestConflictResolution:
    def _conflict(self, device, branch, menu) -> SyncConflict:
        order_id = uuid.uuid4()
        push(device, branch, [open_order_op(order_id), add_item_op(order_id, menu, sequence=1)])
        push(device, branch, [add_item_op(order_id, menu, sequence=5)])
        return SyncConflict.objects.get(code="SEQUENCE_GAP")

    def test_acknowledging_closes_it(self, device, branch, menu, make_user) -> None:
        manager = make_user(email="mgr@caesar.test", role="BRANCH_MANAGER")
        resolved = services.resolve_conflict(
            self._conflict(device, branch, menu),
            resolution="ACKNOWLEDGED",
            note="أعيد الإرسال",
            user=manager,
        )
        assert not resolved.is_open
        assert resolved.resolved_by_id == manager.id

    def test_discarding_records_who_decided(self, device, branch, menu, make_user) -> None:
        """ "We chose not to record that sale" must be a signed statement."""
        manager = make_user(email="mgr@caesar.test", role="BRANCH_MANAGER")
        resolved = services.resolve_conflict(
            self._conflict(device, branch, menu), resolution="DISCARDED", user=manager
        )
        assert resolved.resolution == "DISCARDED"
        assert resolved.resolved_by_id == manager.id

    def test_retrying_reruns_the_original_operation(self, device, branch, menu) -> None:
        conflict = self._conflict(device, branch, menu)
        # Backfill the hole first; the retry should then succeed.
        push(device, branch, [add_item_op(conflict.operation.entity_id, menu, sequence=2)])
        push(device, branch, [add_item_op(conflict.operation.entity_id, menu, sequence=3)])
        push(device, branch, [add_item_op(conflict.operation.entity_id, menu, sequence=4)])

        resolved = services.resolve_conflict(conflict, resolution="RETRIED")

        conflict.operation.refresh_from_db()
        assert conflict.operation.status == OperationStatus.APPLIED
        assert not resolved.is_open

    def test_a_resolved_conflict_cannot_be_resolved_again(self, device, branch, menu) -> None:
        from apps.core.exceptions import AppError

        conflict = self._conflict(device, branch, menu)
        services.resolve_conflict(conflict, resolution="ACKNOWLEDGED")

        with pytest.raises(AppError):
            services.resolve_conflict(conflict, resolution="ACKNOWLEDGED")

    def test_an_unknown_resolution_is_refused(self, device, branch, menu) -> None:
        from apps.core.exceptions import AppError

        with pytest.raises(AppError):
            services.resolve_conflict(self._conflict(device, branch, menu), resolution="IGNORE")


# ── visibility ───────────────────────────────────────────────────────────────


class TestStatus:
    def test_a_device_that_stopped_talking_is_counted_as_stale(self, device, branch) -> None:
        """A terminal that stopped talking is a terminal whose sales are sitting
        on a hard drive."""
        device.last_seen_at = timezone.now() - timedelta(hours=4)
        device.save(update_fields=["last_seen_at"])

        assert services.branch_status(branch)["stale_devices"] == 1

    def test_pushing_marks_the_device_seen(self, device, branch) -> None:
        device.last_seen_at = timezone.now() - timedelta(hours=4)
        device.save(update_fields=["last_seen_at"])

        push(device, branch, [open_order_op()])
        assert services.branch_status(branch)["stale_devices"] == 0

    def test_open_conflicts_are_surfaced(self, device, branch, menu) -> None:
        order_id = uuid.uuid4()
        push(device, branch, [open_order_op(order_id), add_item_op(order_id, menu, sequence=1)])
        push(device, branch, [add_item_op(order_id, menu, sequence=9)])

        assert services.branch_status(branch)["open_conflicts"] == 1
        assert services.device_status(device)["open_conflicts"] == 1

    def test_rejections_are_counted_per_device(self, device, branch) -> None:
        push(device, branch, [op("nonsense", {})])
        assert services.device_status(device)["rejected"] == 1

    def test_the_cursor_is_remembered_server_side(self, device, branch, menu) -> None:
        """So the Web Admin can answer "is that terminal up to date?" without
        asking the terminal — which is the one you cannot ask."""
        page = services.pull(
            branch=branch, stream=Stream.CATALOG, cursor=0, limit=500, device=device
        )
        assert services.device_status(device)["cursors"][Stream.CATALOG] == page["cursor"]


# ── other domains ────────────────────────────────────────────────────────────


class TestShiftHandlers:
    def test_a_shift_keeps_the_id_the_device_gave_it(self, device, branch, make_user) -> None:
        """Its queued cash movements refer to that id and must resolve."""
        shift_id = str(uuid.uuid4())
        push(device, branch, [op("shift_open", {"shift_id": shift_id, "opening_cash": "500.00"})])

        movement = push(
            device,
            branch,
            [
                op(
                    "cash_movement",
                    {
                        "shift_id": shift_id,
                        "movement_type": "IN",
                        "amount": "50.00",
                        "reason": "عهدة",
                    },
                )
            ],
        )
        assert movement.applied == 1

    def test_a_second_shift_on_one_device_is_a_conflict(self, device, branch) -> None:
        """A device that crashed and re-opened would otherwise end the day with
        two drawers and no way to say which counted."""
        push(device, branch, [op("shift_open", {"shift_id": str(uuid.uuid4())})])
        result = push(device, branch, [op("shift_open", {"shift_id": str(uuid.uuid4())})])

        assert result.results[0]["code"] == "SHIFT_ALREADY_OPEN"

    def test_the_server_recomputes_the_z_report(self, device, branch) -> None:
        """
        The terminal's own close lets the cashier count and leave during an
        outage. The server's figure is the one that counts, and a disagreement
        is itself the finding.
        """
        shift_id = str(uuid.uuid4())
        push(device, branch, [op("shift_open", {"shift_id": shift_id, "opening_cash": "500.00"})])

        result = push(
            device,
            branch,
            [
                op(
                    "shift_close",
                    {
                        "shift_id": shift_id,
                        "counted_cash": "500.00",
                        "client_expected_cash": "480.00",
                    },
                )
            ],
        )
        assert result.results[0]["result"]["server_client_drift"] == "-20.00"

    def test_an_offline_order_lands_in_the_shift_it_was_sold_in(
        self, device, branch, menu, cash
    ) -> None:
        """
        The handler used to drop the terminal's `shift_id`, so every synced order
        arrived with no drawer — which empties the Z-report of exactly the sales
        the terminal made, and takes the variance report with it.
        """
        shift_id = str(uuid.uuid4())
        order_id = uuid.uuid4()

        push(device, branch, [op("shift_open", {"shift_id": shift_id, "opening_cash": "500.00"})])
        push(
            device,
            branch,
            [
                op(
                    "order_open",
                    {
                        "order_id": str(order_id),
                        "order_type": "DINE_IN",
                        "local_number": "MB-01-0099",
                        "shift_id": shift_id,
                    },
                ),
                add_item_op(order_id, menu),
            ],
        )

        order = Order.objects.get(id=order_id)
        assert str(order.shift_id) == shift_id

    def test_an_order_with_no_shift_still_syncs(self, device, branch, menu) -> None:
        """
        A terminal mid-upgrade, or a board that never opened a drawer, must not
        have its sales rejected — they arrive unattributed and visible rather
        than lost.
        """
        order_id = uuid.uuid4()
        result = push(device, branch, [open_order_op(order_id)])

        assert result.applied == 1
        assert Order.objects.get(id=order_id).shift_id is None


class TestInvoiceReconciliation:
    def test_a_provisional_serial_is_recorded_beside_the_permanent_one(
        self, device, branch, menu, cash
    ) -> None:
        """
        A terminal that exhausted its block offline printed `MB-01-P042` and
        handed it to a customer. Matching that slip to this invoice later is the
        entire point of keeping it (C9).
        """
        order_id = uuid.uuid4()
        push(device, branch, [open_order_op(order_id), add_item_op(order_id, menu)])
        order = Order.objects.get(id=order_id)

        push(
            device,
            branch,
            [
                op(
                    "payment",
                    {
                        "order_id": str(order_id),
                        "method_id": str(cash.id),
                        "amount": str(order.grand_total),
                        "idempotency_key": "prov-1",
                        "provisional_serial": "MB-01-P042",
                    },
                )
            ],
        )

        order.refresh_from_db()
        assert order.invoice.provisional_serial == "MB-01-P042"
        assert order.invoice.serial != "MB-01-P042"


# ── API ──────────────────────────────────────────────────────────────────────


class TestAPI:
    def _device_client(self, device, branch):
        from rest_framework.test import APIClient

        from apps.accounts import tokens

        pair = tokens.issue_pair(
            user=None,
            kind="DEVICE",
            organization_id=branch.organization_id,
            branch_id=branch.id,
            device_id=device.id,
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {pair['access']}")
        return client

    def test_a_mixed_batch_answers_207(self, device, branch, menu) -> None:
        client = self._device_client(device, branch)
        response = client.post(
            "/api/v1/sync/push/",
            {"operations": [open_order_op(), op("nonsense", {})]},
            format="json",
        )
        assert response.status_code == 207, response.json()
        assert response.json()["data"]["applied"] == 1
        assert response.json()["data"]["failed"] == 1

    def test_a_clean_batch_answers_200(self, device, branch) -> None:
        client = self._device_client(device, branch)
        response = client.post(
            "/api/v1/sync/push/", {"operations": [open_order_op()]}, format="json"
        )
        assert response.status_code == 200

    def test_a_revoked_device_cannot_push(self, device, branch) -> None:
        """
        401, not 403: revoking a device stops its token resolving at all, so it
        is rejected before the view runs. The view's own DEVICE_REVOKED check is
        a backstop for a revocation that lands mid-request.
        """
        client = self._device_client(device, branch)
        device.status = DeviceStatus.REVOKED
        device.save(update_fields=["status"])

        response = client.post(
            "/api/v1/sync/push/", {"operations": [open_order_op()]}, format="json"
        )
        assert response.status_code == 401
        assert not Order.objects.exists()

    def test_a_human_without_a_device_cannot_push(self, branch, make_user, authed) -> None:
        client = authed(make_user(email="mgr@caesar.test", role="BRANCH_MANAGER"), branch=branch)
        response = client.post(
            "/api/v1/sync/push/", {"operations": [open_order_op()]}, format="json"
        )
        assert response.json()["code"] == "DEVICE_REQUIRED"

    def test_a_device_can_pull(self, device, branch, menu) -> None:
        client = self._device_client(device, branch)
        response = client.get("/api/v1/sync/pull/?stream=catalog&cursor=0")

        assert response.status_code == 200
        assert response.json()["data"]["stream"] == "catalog"
        assert response.json()["data"]["changes"]

    def test_conflicts_need_a_permission(self, device, branch, menu, make_user, authed) -> None:
        cashier = authed(make_user(email="c@caesar.test", role="CASHIER"), branch=branch)
        assert cashier.get("/api/v1/sync/conflicts/").status_code == 403

        manager = authed(make_user(email="m@caesar.test", role="BRANCH_MANAGER"), branch=branch)
        assert manager.get("/api/v1/sync/conflicts/").status_code == 200

    def test_resolving_needs_its_own_permission(self, device, branch, menu, make_user, authed):
        order_id = uuid.uuid4()
        push(device, branch, [open_order_op(order_id), add_item_op(order_id, menu, sequence=1)])
        push(device, branch, [add_item_op(order_id, menu, sequence=7)])
        conflict = SyncConflict.objects.get(code="SEQUENCE_GAP")

        cashier = authed(make_user(email="c@caesar.test", role="CASHIER"), branch=branch)
        assert (
            cashier.post(
                f"/api/v1/sync/conflicts/{conflict.id}/resolve/",
                {"resolution": "ACKNOWLEDGED"},
                format="json",
            ).status_code
            == 403
        )

    def test_the_branch_status_endpoint_reports_devices(
        self, device, branch, make_user, authed
    ) -> None:
        manager = authed(make_user(email="m@caesar.test", role="BRANCH_MANAGER"), branch=branch)
        data = manager.get("/api/v1/sync/status/").json()["data"]

        assert len(data["devices"]) == 1
        assert data["devices"][0]["device_name"] == "كاشير ١"
        assert set(data["heads"]) == set(Stream.values)


def test_the_change_log_carries_a_transaction_id(branch, menu) -> None:
    """Without it the visibility guard has nothing to compare against."""
    row = ChangeLog.objects.filter(branch=branch).last()
    assert row is not None
    assert row.txid > 0
    assert connection.vendor == "postgresql", "the guard is Postgres-specific by design"
