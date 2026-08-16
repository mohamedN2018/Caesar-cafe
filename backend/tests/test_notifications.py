"""
Alerts: what gets raised, what does not, and what is never raised twice.

The feature lives or dies on restraint. An owner who gets nine notifications a
night mutes the app, and then the one that mattered — the drawer that closed
four hundred short — is muted too. So most of what follows is about NOT sending:
under the threshold, during quiet hours, and above all not again.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from django.utils import timezone

from apps.notifications import alerts, services
from apps.notifications.models import AlertKind, PushSubscription, SentAlert
from apps.notifications.webpush import VapidKeys

pytestmark = pytest.mark.django_db

CLIENT_PUBLIC = (
    "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
)
AUTH_SECRET = "BTBZMqHH6r4Tts7J_aSIgg"


@pytest.fixture
def configured(settings):
    keys = VapidKeys.generate()
    settings.VAPID_PRIVATE_KEY = keys.private_key
    settings.VAPID_PUBLIC_KEY = keys.public_key
    settings.VAPID_SUBJECT = "mailto:owner@caesar.test"
    return keys


@pytest.fixture
def subscriber(make_user):
    """The person whose phone is subscribed. Named distinctly from `client`'s
    user, because two fixtures minting `user@caesar.test` collide on the unique
    email and the failure looks like a database problem rather than a test one."""
    return make_user(email="subscriber@caesar.test", role="BRANCH_MANAGER")


@pytest.fixture
def subscription(organization, branch, subscriber):
    return PushSubscription.objects.create(
        organization=organization,
        branch=branch,
        user=subscriber,
        endpoint="https://push.example/subscription/abc",
        p256dh=CLIENT_PUBLIC,
        auth=AUTH_SECRET,
        label="موبايل المالك",
    )


def transport(handler) -> httpx.Client:
    """A push service that answers however the test wants."""
    return httpx.Client(transport=httpx.MockTransport(handler))


# ── quiet hours ──────────────────────────────────────────────────────────────


class TestQuietHours:
    @pytest.mark.parametrize(
        ("window", "hour", "expected"),
        [
            ("02:00-09:00", 3, True),
            ("02:00-09:00", 14, False),
            ("02:00-09:00", 9, False),
            ("22:00-06:00", 23, True),
            ("22:00-06:00", 2, True),
            ("22:00-06:00", 12, False),
        ],
    )
    def test_it_handles_windows_that_wrap_past_midnight(self, window, hour, expected) -> None:
        """Which is the normal case for a cafe — quiet hours are 02:00 to 09:00."""
        now = timezone.localtime().replace(hour=hour, minute=30)
        assert alerts.in_quiet_hours(window, now=now) is expected

    def test_no_window_means_never_quiet(self) -> None:
        assert alerts.in_quiet_hours("") is False

    def test_a_malformed_window_fails_towards_delivery(self) -> None:
        """
        Silence is the dangerous direction for an alert system: a typo in a
        setting must not stop the backup failure reaching anybody.
        """
        assert alerts.in_quiet_hours("nonsense") is False
        assert alerts.in_quiet_hours("25:00-99:00") is False


# ── the rules ────────────────────────────────────────────────────────────────


class TestCashVariance:
    def _shift(self, branch, variance, user=None):
        from apps.shifts.models import Shift, ShiftStatus

        return Shift.objects.create(
            organization=branch.organization,
            branch=branch,
            status=ShiftStatus.CLOSED,
            opening_cash=Decimal("500.00"),
            counted_cash=Decimal("500.00") + variance,
            variance=variance,
            closed_at=timezone.now(),
            closed_by=user,
        )

    def test_a_shortage_over_the_threshold_is_raised(self, branch, make_user) -> None:
        self._shift(branch, Decimal("-120.00"), make_user(email="c@caesar.test"))
        raised = alerts.evaluate(branch)

        variance = [a for a in raised if a.kind == AlertKind.CASH_VARIANCE]
        assert len(variance) == 1
        assert "عجز" in variance[0].title
        assert "120.00" in variance[0].title

    def test_an_overage_is_raised_too(self, branch) -> None:
        """
        Not good news. An overage usually means a sale went unrecorded, which is
        the same problem wearing a friendlier face.
        """
        self._shift(branch, Decimal("200.00"))
        raised = [a for a in alerts.evaluate(branch) if a.kind == AlertKind.CASH_VARIANCE]

        assert len(raised) == 1
        assert "زيادة" in raised[0].title

    def test_small_differences_are_ignored(self, branch) -> None:
        """Alerting on every piastre is how an owner learns to ignore alerts."""
        self._shift(branch, Decimal("-15.00"))
        assert not [a for a in alerts.evaluate(branch) if a.kind == AlertKind.CASH_VARIANCE]

    def test_the_threshold_is_configurable(self, branch) -> None:
        from apps.configuration import resolver
        from apps.configuration.registry import Scope

        self._shift(branch, Decimal("-15.00"))
        resolver.set_value(
            "alerts.cash_variance_threshold", "10.00", scope=Scope.BRANCH, scope_id=branch.id
        )

        assert [a for a in alerts.evaluate(branch) if a.kind == AlertKind.CASH_VARIANCE]

    def test_it_links_somewhere_useful(self, branch) -> None:
        """An alert that cannot be acted on is a worse version of a text message."""
        self._shift(branch, Decimal("-120.00"))
        raised = next(a for a in alerts.evaluate(branch) if a.kind == AlertKind.CASH_VARIANCE)

        assert raised.url == "/shifts"


class TestKitchenLate:
    def _ticket(self, branch, minutes_old, target=8):
        from apps.kitchen.models import KitchenTicket, Station, TicketStatus
        from apps.orders import services as order_services

        station = Station.objects.create(
            organization=branch.organization,
            branch=branch,
            code=f"S{minutes_old}{target}",
            name_ar="بار القهوة",
            target_prep_minutes=target,
        )
        order = order_services.open_order(branch=branch)
        ticket = KitchenTicket.objects.create(
            branch=branch,
            station=station,
            order=order,
            ticket_number=minutes_old,
            status=TicketStatus.NEW,
        )
        KitchenTicket.objects.filter(pk=ticket.pk).update(
            created_at=timezone.now() - timedelta(minutes=minutes_old)
        )
        return ticket

    def test_the_threshold_is_on_top_of_the_stations_target(self, branch) -> None:
        """
        A grill targeting twelve minutes and a coffee bar targeting four cannot
        share one definition of late. An absolute number would either scream
        about every espresso or never mention a burnt steak.
        """
        self._ticket(branch, minutes_old=25, target=4)  # 21 over — late
        self._ticket(branch, minutes_old=25, target=30)  # inside target — fine

        raised = [a for a in alerts.evaluate(branch) if a.kind == AlertKind.KITCHEN_LATE]
        assert len(raised) == 1

    def test_a_ticket_inside_the_grace_is_ignored(self, branch) -> None:
        self._ticket(branch, minutes_old=10, target=8)
        assert not [a for a in alerts.evaluate(branch) if a.kind == AlertKind.KITCHEN_LATE]


class TestLowStock:
    def _item(self, branch, name, *, minimum, on_hand, reserved=Decimal("0")):
        from apps.inventory.models import InventoryItem, StockLevel, Unit

        unit, _ = Unit.objects.get_or_create(
            organization=branch.organization, code="G", defaults={"name_ar": "جرام"}
        )
        item = InventoryItem.objects.create(
            organization=branch.organization,
            branch=branch,
            code=name.upper(),
            name_ar=name,
            base_unit=unit,
            minimum_stock=minimum,
        )
        StockLevel.objects.create(item=item, quantity_on_hand=on_hand, quantity_reserved=reserved)
        return item

    def _raised(self, branch):
        return [a for a in alerts.evaluate(branch) if a.kind == AlertKind.LOW_STOCK]

    def test_an_item_at_its_minimum_is_raised(self, branch) -> None:
        self._item(branch, "لبن", minimum=Decimal("5000"), on_hand=Decimal("4000"))
        raised = self._raised(branch)

        assert len(raised) == 1
        assert "لبن" in raised[0].body
        assert raised[0].url == "/inventory"

    def test_plenty_in_stock_says_nothing(self, branch) -> None:
        self._item(branch, "لبن", minimum=Decimal("5000"), on_hand=Decimal("90000"))
        assert not self._raised(branch)

    def test_items_with_no_minimum_set_are_ignored(self, branch) -> None:
        """
        A minimum of zero is an unconfigured item, not a threshold. Counting it
        would put every such item into the alert the day it emptied and bury
        the ones somebody deliberately set a level for.
        """
        self._item(branch, "منديل", minimum=Decimal("0"), on_hand=Decimal("0"))
        assert not self._raised(branch)

    def test_stock_committed_to_open_orders_does_not_count_as_available(self, branch) -> None:
        """
        Milk in the fridge that four unpaid tickets are already spoken for is
        not milk you can sell, and an alert that says otherwise sends somebody
        to check a shelf that looks fine.
        """
        self._item(
            branch,
            "لبن",
            minimum=Decimal("5000"),
            on_hand=Decimal("6000"),
            reserved=Decimal("2000"),
        )
        assert self._raised(branch)

    def test_many_low_items_are_one_alert_not_many(self, branch) -> None:
        """
        Low stock arrives in clusters — a delivery is missed and everything goes
        under together. Five buzzes is how an owner learns to swipe the app away.
        """
        for name in ["لبن", "بن", "سكر", "شاي", "كاكاو"]:
            self._item(branch, name, minimum=Decimal("5000"), on_hand=Decimal("10"))

        raised = self._raised(branch)
        assert len(raised) == 1
        assert "5" in raised[0].title
        assert "وغيرها" in raised[0].body

    def test_a_newly_low_item_is_a_new_subject(self, branch) -> None:
        """
        The dedupe key is the SET of low items. A key on the count alone would
        stay silent when one item is restocked and another falls the same hour.
        """
        self._item(branch, "لبن", minimum=Decimal("5000"), on_hand=Decimal("10"))
        first = self._raised(branch)[0].dedupe_key

        self._item(branch, "بن", minimum=Decimal("5000"), on_hand=Decimal("10"))
        assert self._raised(branch)[0].dedupe_key != first

    def test_the_key_fits_the_column(self, branch) -> None:
        """
        Six UUIDs do not fit in 200 characters. A truncated key would collapse
        two different sets of low items onto one alert, and the second would
        never send — the failure being silence, which nobody reports.
        """
        from apps.notifications.models import SentAlert

        for index in range(12):
            self._item(branch, f"صنف {index}", minimum=Decimal("5000"), on_hand=Decimal("10"))

        limit = SentAlert._meta.get_field("dedupe_key").max_length
        assert len(self._raised(branch)[0].dedupe_key) <= limit


class TestBackupFailed:
    def test_no_backup_in_26_hours_is_raised(self, branch) -> None:
        raised = [a for a in alerts.evaluate(branch) if a.kind == AlertKind.BACKUP_FAILED]
        assert len(raised) == 1
        assert "لم تُنفَّذ" in raised[0].title

    def test_a_failed_run_is_raised_with_its_reason(self, branch) -> None:
        from apps.ops.models import BackupRecord, BackupStatus

        BackupRecord.objects.create(
            filename="x.sql.gz.enc", status=BackupStatus.FAILED, error="disk full"
        )
        raised = next(a for a in alerts.evaluate(branch) if a.kind == AlertKind.BACKUP_FAILED)

        assert "disk full" in raised.body

    def test_a_good_backup_raises_nothing(self, branch) -> None:
        from apps.ops.models import BackupRecord, BackupStatus

        BackupRecord.objects.create(filename="x.sql.gz.enc", status=BackupStatus.COMPLETE)
        assert not [a for a in alerts.evaluate(branch) if a.kind == AlertKind.BACKUP_FAILED]

    def test_it_is_delivered_even_during_quiet_hours(self, branch) -> None:
        """
        The one exemption. Knowing at 03:00 that there is no backup leaves a
        morning to fix it; knowing at 09:00 does not.
        """
        from apps.configuration import resolver
        from apps.configuration.registry import Scope

        resolver.set_value(
            "alerts.quiet_hours", "00:00-23:59", scope=Scope.BRANCH, scope_id=branch.id
        )
        raised = alerts.evaluate(branch)

        assert [a for a in raised if a.kind == AlertKind.BACKUP_FAILED]
        assert AlertKind.BACKUP_FAILED in alerts.ALWAYS_DELIVER

    def test_everything_else_is_silenced_during_quiet_hours(self, branch) -> None:
        from apps.configuration import resolver
        from apps.configuration.registry import Scope
        from apps.shifts.models import Shift, ShiftStatus

        Shift.objects.create(
            organization=branch.organization,
            branch=branch,
            status=ShiftStatus.CLOSED,
            variance=Decimal("-500.00"),
            closed_at=timezone.now(),
        )
        resolver.set_value(
            "alerts.quiet_hours", "00:00-23:59", scope=Scope.BRANCH, scope_id=branch.id
        )

        kinds = {a.kind for a in alerts.evaluate(branch)}
        assert AlertKind.CASH_VARIANCE not in kinds


class TestSwitches:
    def test_disabling_alerts_silences_everything(self, branch) -> None:
        from apps.configuration import resolver
        from apps.configuration.registry import Scope

        resolver.set_value("alerts.enabled", False, scope=Scope.BRANCH, scope_id=branch.id)
        assert alerts.evaluate(branch) == []

    def test_a_kind_can_be_turned_off_on_its_own(self, branch) -> None:
        from apps.configuration import resolver
        from apps.configuration.registry import Scope

        resolver.set_value(
            "alerts.kinds", ["CASH_VARIANCE"], scope=Scope.BRANCH, scope_id=branch.id
        )
        kinds = {a.kind for a in alerts.evaluate(branch)}

        assert AlertKind.BACKUP_FAILED not in kinds

    def test_sync_conflicts_are_off_by_default(self) -> None:
        """
        The Desktop already shows these in its header with a button, and the
        cashier at the terminal is better placed than the owner at home.
        """
        from apps.configuration.registry import registry

        assert "SYNC_CONFLICT" not in registry.get("alerts.kinds").default

    def test_one_broken_rule_does_not_silence_the_others(self, branch, monkeypatch) -> None:
        def explode(*args, **kwargs):
            raise RuntimeError("the kitchen table is on fire")

        monkeypatch.setitem(alerts.RULES, AlertKind.KITCHEN_LATE, explode)
        kinds = {a.kind for a in alerts.evaluate(branch)}

        assert AlertKind.BACKUP_FAILED in kinds


# ── not twice ────────────────────────────────────────────────────────────────


class TestDeduplication:
    def test_the_same_subject_is_only_claimed_once(self, branch) -> None:
        """
        The sweep re-reads the same conditions every five minutes. Without this,
        one late order becomes twelve notifications before anybody cooks it.
        """
        alert = alerts.Alert(
            kind=AlertKind.KITCHEN_LATE, dedupe_key="ticket:1", title="t", body="b"
        )

        assert services.claim(branch, alert) is not None
        assert services.claim(branch, alert) is None
        assert SentAlert.objects.filter(dedupe_key="ticket:1").count() == 1

    def test_a_different_subject_is_a_different_alert(self, branch) -> None:
        for number in ("1", "2"):
            services.claim(
                branch,
                alerts.Alert(
                    kind=AlertKind.KITCHEN_LATE,
                    dedupe_key=f"ticket:{number}",
                    title="t",
                    body="b",
                ),
            )
        assert SentAlert.objects.count() == 2

    def test_two_branches_do_not_collide(self, branch, other_branch) -> None:
        alert = alerts.Alert(kind=AlertKind.BACKUP_FAILED, dedupe_key="x", title="t", body="b")

        assert services.claim(branch, alert) is not None
        assert services.claim(other_branch, alert) is not None


# ── delivery ─────────────────────────────────────────────────────────────────


class TestDelivery:
    def _record(self, branch) -> SentAlert:
        return SentAlert.objects.create(
            branch=branch,
            kind=AlertKind.CASH_VARIANCE,
            dedupe_key=f"x:{uuid.uuid4()}",
            title="عجز في الدرج",
            body="١٢٠ ج.م",
            url="/shifts",
        )

    def test_a_successful_push_is_counted(self, branch, subscription, configured) -> None:
        client = transport(lambda request: httpx.Response(201))
        result = services.deliver(self._record(branch), client=client)

        assert result.sent == 1
        subscription.refresh_from_db()
        assert subscription.last_sent_at is not None

    def test_the_body_is_encrypted(self, branch, subscription, configured) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = request.content
            seen["headers"] = dict(request.headers)
            return httpx.Response(201)

        services.deliver(self._record(branch), client=transport(handler))

        assert "عجز".encode() not in seen["body"]
        assert seen["headers"]["content-encoding"] == "aes128gcm"
        assert seen["headers"]["authorization"].startswith("vapid t=")

    def test_a_410_deletes_the_subscription(self, branch, subscription, configured) -> None:
        """
        The browser is gone. Keeping the row means failing forever against an
        endpoint that will never answer.
        """
        client = transport(lambda request: httpx.Response(410))
        result = services.deliver(self._record(branch), client=client)

        assert result.dropped == 1
        assert not PushSubscription.objects.filter(pk=subscription.pk).exists()

    def test_a_500_is_a_retryable_failure(self, branch, subscription, configured) -> None:
        client = transport(lambda request: httpx.Response(500))
        result = services.deliver(self._record(branch), client=client)

        assert result.failed == 1
        subscription.refresh_from_db()
        assert subscription.failures == 1

    def test_repeated_failures_eventually_drop_it(self, branch, subscription, configured) -> None:
        subscription.failures = services.MAX_FAILURES - 1
        subscription.save(update_fields=["failures"])

        services.deliver(self._record(branch), client=transport(lambda r: httpx.Response(500)))

        assert not PushSubscription.objects.filter(pk=subscription.pk).exists()

    def test_a_network_error_does_not_raise(self, branch, subscription, configured) -> None:
        """Nothing in the cafe depends on a notification arriving."""

        def handler(request):
            raise httpx.ConnectError("no route to host")

        result = services.deliver(self._record(branch), client=transport(handler))
        assert result.failed == 1

    def test_one_bad_subscription_does_not_stop_the_others(
        self, branch, subscription, configured, make_user, organization
    ) -> None:
        PushSubscription.objects.create(
            organization=organization,
            branch=branch,
            user=make_user(email="second@caesar.test"),
            endpoint="https://push.example/second",
            p256dh="not-a-valid-key",
            auth=AUTH_SECRET,
        )

        result = services.deliver(
            self._record(branch), client=transport(lambda r: httpx.Response(201))
        )

        assert result.sent == 1
        assert result.failed == 1

    def test_it_refuses_to_pretend_when_unconfigured(self, branch, settings) -> None:
        """
        A deployment that believes it is sending alerts and is not is worse than
        one that says it cannot.
        """
        settings.VAPID_PRIVATE_KEY = ""
        settings.VAPID_PUBLIC_KEY = ""

        with pytest.raises(services.NotConfigured):
            services.keys()

    def test_only_the_branchs_own_subscribers_are_told(
        self, branch, other_branch, subscription, configured, make_user, other_organization
    ) -> None:
        """An owner with two cafes is not woken by the other one's kitchen."""
        PushSubscription.objects.create(
            organization=other_organization,
            branch=other_branch,
            user=make_user(email="other@caesar.test", org=other_organization),
            endpoint="https://push.example/other",
            p256dh=CLIENT_PUBLIC,
            auth=AUTH_SECRET,
        )

        result = services.deliver(
            self._record(branch), client=transport(lambda r: httpx.Response(201))
        )
        assert result.sent == 1


# ── the API ──────────────────────────────────────────────────────────────────


class TestApi:
    @pytest.fixture
    def client(self, authed, make_user, branch):
        return authed(make_user(email="manager@caesar.test", role="BRANCH_MANAGER"), branch=branch)

    def test_the_public_key_is_served(self, client, configured) -> None:
        response = client.get("/api/v1/notifications/vapid-key/")

        assert response.status_code == 200
        assert response.json()["data"]["configured"] is True
        assert response.json()["data"]["public_key"] == configured.public_key

    def test_an_unconfigured_server_says_so(self, client, settings) -> None:
        settings.VAPID_PUBLIC_KEY = ""
        data = client.get("/api/v1/notifications/vapid-key/").json()["data"]

        assert data["configured"] is False
        assert data["public_key"] is None

    def test_a_browser_can_subscribe(self, client) -> None:
        response = client.post(
            "/api/v1/notifications/subscriptions/",
            {
                "endpoint": "https://push.example/abc",
                "p256dh": CLIENT_PUBLIC,
                "auth": AUTH_SECRET,
                "label": "موبايل المالك",
            },
            format="json",
        )

        assert response.status_code == 201
        assert PushSubscription.objects.count() == 1

    def test_resubscribing_updates_rather_than_duplicates(self, client) -> None:
        """
        Browsers re-issue a subscription when their push service rotates.
        Without this an owner accumulates dead endpoints and one notification
        per each.
        """
        body = {
            "endpoint": "https://push.example/abc",
            "p256dh": CLIENT_PUBLIC,
            "auth": AUTH_SECRET,
        }
        client.post("/api/v1/notifications/subscriptions/", body, format="json")
        client.post("/api/v1/notifications/subscriptions/", body, format="json")

        assert PushSubscription.objects.count() == 1

    def test_the_keys_are_never_returned(self, client, subscription) -> None:
        """
        These are the capability to push to somebody's phone. A list endpoint
        that handed them out would let its reader impersonate us to that device.
        """
        body = client.get("/api/v1/notifications/subscriptions/").content.decode()

        assert CLIENT_PUBLIC not in body
        assert AUTH_SECRET not in body
        assert "push.example" not in body

    def test_a_bare_device_token_cannot_subscribe(self, branch) -> None:
        """
        Somebody must be accountable for a subscription — it is a standing
        instruction to send a person's phone the day's problems, and a token
        with no person attached names nobody to revoke it.

        A cashier signed in AT a terminal is a person and passes; only a bare
        device token, the kind that drains the outbox at 3am, is refused.
        """
        from rest_framework.test import APIClient

        from apps.accounts import tokens
        from apps.licensing.models import Device, DeviceStatus, License, LicenseType

        # A REAL activated device, so the token resolves and the request is
        # refused by `RequiresHuman` rather than bouncing off authentication —
        # which would prove nothing about this endpoint.
        licence = License.objects.create(
            organization=branch.organization,
            branch=branch,
            key_hash=uuid.uuid4().hex,
            key_prefix="QSR-TEST",
            license_type=LicenseType.YEARLY,
            max_devices=3,
        )
        device = Device.objects.create(
            license=licence,
            branch=branch,
            device_name="كاشير ١",
            secret_hash="x" * 32,
            status=DeviceStatus.ACTIVE,
        )
        pair = tokens.issue_pair(
            user=None,
            kind="DEVICE",
            organization_id=branch.organization_id,
            branch_id=branch.id,
            device_id=device.id,
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {pair['access']}")

        response = client.post(
            "/api/v1/notifications/subscriptions/",
            {"endpoint": "https://push.example/x", "p256dh": CLIENT_PUBLIC, "auth": AUTH_SECRET},
            format="json",
        )
        assert response.status_code == 403
        assert not PushSubscription.objects.exists()

    def test_a_subscription_can_be_removed(self, authed, subscriber, subscription, branch) -> None:
        """Its owner, and only its owner, may silence it."""
        owner = authed(subscriber, branch=branch)

        response = owner.delete(f"/api/v1/notifications/subscriptions/{subscription.id}/")

        assert response.status_code == 204
        assert not PushSubscription.objects.filter(pk=subscription.pk).exists()

    def test_one_manager_cannot_silence_anothers_phone(
        self, client, subscription, authed, make_user, branch
    ) -> None:
        other = authed(make_user(email="other@caesar.test", role="BRANCH_MANAGER"), branch=branch)
        response = other.delete(f"/api/v1/notifications/subscriptions/{subscription.id}/")

        assert response.status_code == 404
        assert PushSubscription.objects.filter(pk=subscription.pk).exists()

    def test_the_history_needs_a_reporting_permission(self, authed, make_user, branch) -> None:
        """
        Every cash variance and late ticket is management information, unlike
        subscribing — which anybody signed in may do for their own branch.

        A WAITER, not a cashier: the catalogue gives CASHIER `reports.sales`
        deliberately, so they can see their own day. The line this asserts is
        between somebody who reads figures and somebody who does not.
        """
        from apps.authz import catalog

        assert "reports.sales" not in catalog.SYSTEM_ROLES["WAITER"]["permissions"]

        waiter = authed(make_user(email="w@caesar.test", role="WAITER"), branch=branch)
        assert waiter.get("/api/v1/notifications/alerts/").status_code == 403

    def test_a_manager_can_read_the_history(self, client, branch) -> None:
        SentAlert.objects.create(
            branch=branch, kind=AlertKind.CASH_VARIANCE, dedupe_key="x", title="t", body="b"
        )
        rows = client.get("/api/v1/notifications/alerts/").json()["data"]

        assert rows[0]["kind_label"] == "فرق نقدي"
