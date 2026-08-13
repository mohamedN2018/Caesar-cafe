"""Expiry policy, heartbeat, device management and the licensing API."""

from __future__ import annotations

from datetime import timedelta
from itertools import pairwise

import pytest
from django.utils import timezone

from apps.licensing import offline_token as ot
from apps.licensing import services
from apps.licensing.models import Device, DeviceStatus, License, LicenseEvent, LicenseStatus
from apps.licensing.services import ExpiryStage

pytestmark = pytest.mark.django_db

# The suite ships a fixed Ed25519 key (config/settings/test.py), so server and
# tests agree without per-test wiring.
PUBLIC = services.public_key_bytes()


@pytest.fixture
def issued(organization, branch):
    return services.issue_license(
        organization=organization,
        branch=branch,
        customer_email="owner@caesar.test",
        license_type="YEARLY",
        max_devices=3,
        expires_at=timezone.now() + timedelta(days=365),
    )


@pytest.fixture
def activated(issued):
    return services.activate(
        license_key=issued.plaintext_key,
        email="owner@caesar.test",
        device_name="Cashier-01",
    )


class TestExpiryPolicy:
    """
    docs/06 §60 — never hard-kill a running cafe. A POS going black mid-service
    because a renewal was forgotten is worse than a few days of unpaid use, and
    is the fastest way to lose a customer permanently.
    """

    def _at(self, issued, days_from_expiry: int):
        License.objects.filter(pk=issued.license.pk).update(
            expires_at=timezone.now() + timedelta(days=days_from_expiry)
        )
        issued.license.refresh_from_db()
        return services.evaluate_state(issued.license)

    def test_healthy_licence_is_unrestricted(self, issued) -> None:
        state = self._at(issued, 200)
        assert state.stage is ExpiryStage.ACTIVE
        assert state.can_open_new_orders

    def test_notice_stage_does_not_disturb_staff(self, issued) -> None:
        state = self._at(issued, 10)
        assert state.stage is ExpiryStage.NOTICE
        assert state.can_open_new_orders

    def test_warning_stage_still_sells(self, issued) -> None:
        state = self._at(issued, 2)
        assert state.stage is ExpiryStage.WARNING
        assert state.can_open_new_orders

    def test_grace_period_still_sells(self, issued) -> None:
        """Commercial pressure, not sabotage."""
        state = self._at(issued, -3)
        assert state.stage is ExpiryStage.GRACE
        assert state.can_open_new_orders
        assert state.message_ar

    def test_restricted_blocks_new_orders_but_settles_open_ones(self, issued) -> None:
        """Eight open tables must still be servable and payable."""
        state = self._at(issued, -30)
        assert state.stage is ExpiryStage.RESTRICTED
        assert state.can_open_new_orders is False
        assert state.can_close_open_orders is True
        assert state.can_read_history is True

    def test_read_only_policy_stops_everything_but_reading(self, issued, organization) -> None:
        from apps.configuration import resolver
        from apps.configuration.registry import Scope

        resolver.set_value(
            "license.expiry_policy",
            "READ_ONLY",
            scope=Scope.ORGANIZATION,
            scope_id=organization.id,
        )
        state = self._at(issued, -30)
        assert state.can_open_new_orders is False
        assert state.can_close_open_orders is False
        assert state.can_read_history is True

    def test_history_is_always_readable(self, issued) -> None:
        """The owner's financial records are their data, not ours to withhold."""
        for days in (200, -3, -30, -365):
            assert self._at(issued, days).can_read_history is True

    def test_lifetime_never_expires(self, issued) -> None:
        License.objects.filter(pk=issued.license.pk).update(
            expires_at=None, license_type="LIFETIME"
        )
        issued.license.refresh_from_db()
        state = services.evaluate_state(issued.license)
        assert state.stage is ExpiryStage.ACTIVE
        assert state.days_until_expiry is None

    def test_revoked_blocks_everything_but_reading(self, issued) -> None:
        License.objects.filter(pk=issued.license.pk).update(status=LicenseStatus.REVOKED)
        issued.license.refresh_from_db()
        state = services.evaluate_state(issued.license)
        assert state.stage is ExpiryStage.BLOCKED
        assert state.can_open_new_orders is False

    def test_suspended_still_lets_open_orders_close(self, issued) -> None:
        License.objects.filter(pk=issued.license.pk).update(status=LicenseStatus.SUSPENDED)
        issued.license.refresh_from_db()
        state = services.evaluate_state(issued.license)
        assert state.can_open_new_orders is False
        assert state.can_close_open_orders is True


class TestOfflineTokenIssuance:
    def test_token_verifies_with_the_server_public_key(self, activated) -> None:
        payload = ot.verify(activated.offline_token, PUBLIC)
        assert payload["device_id"] == str(activated.device.id)
        assert payload["grace_hours"] == 72

    def test_each_issue_advances_the_sequence(self, activated) -> None:
        first = ot.verify(activated.offline_token, PUBLIC)["seq"]
        second_token = services.issue_offline_token(activated.device.license, activated.device)
        second = ot.verify(second_token, PUBLIC)["seq"]
        assert second > first, "sequence must be monotonic for the client ratchet"

    def test_token_carries_the_expiry_stage(self, issued, activated) -> None:
        License.objects.filter(pk=issued.license.pk).update(
            expires_at=timezone.now() - timedelta(days=30)
        )
        issued.license.refresh_from_db()
        payload = ot.verify(services.issue_offline_token(issued.license, activated.device), PUBLIC)
        assert payload["stage"] == ExpiryStage.RESTRICTED.value
        assert payload["can_open_new_orders"] is False

    def test_the_desktop_ratchet_accepts_a_real_server_token(self, activated) -> None:
        """The two halves of C5 meeting: server signs, client verifies."""
        payload, state = ot.accept(activated.offline_token, PUBLIC, ot.RatchetState())
        assert payload["status"] == LicenseStatus.ACTIVE
        assert state.highest_seq >= 0


class TestDeviceAuthentication:
    def test_correct_secret_authenticates(self, activated) -> None:
        device = services.authenticate_device(
            device_id=activated.device.id, device_secret=activated.device_secret
        )
        assert device.id == activated.device.id
        assert device.last_seen_at is not None

    def test_wrong_secret_is_refused(self, activated) -> None:
        from apps.core.exceptions import AppError

        with pytest.raises(AppError):
            services.authenticate_device(device_id=activated.device.id, device_secret="wrong")

    def test_revoked_device_is_refused(self, activated) -> None:
        Device.objects.filter(pk=activated.device.pk).update(status=DeviceStatus.REVOKED)
        with pytest.raises(services.DeviceRevoked):
            services.authenticate_device(
                device_id=activated.device.id, device_secret=activated.device_secret
            )

    def test_a_copied_secret_still_needs_the_right_device_id(self, activated) -> None:
        import uuid

        from apps.core.exceptions import AppError

        with pytest.raises(AppError):
            services.authenticate_device(
                device_id=uuid.uuid4(), device_secret=activated.device_secret
            )


class TestInvoiceBlocks:
    def test_blocks_are_disjoint_and_sequential(self, issued) -> None:
        devices = [
            services.activate(
                license_key=issued.plaintext_key,
                email="owner@caesar.test",
                device_name=f"T{i}",
            ).device
            for i in range(3)
        ]
        blocks = [services.allocate_invoice_block(d, size=500) for d in devices]

        assert (blocks[0].range_start, blocks[0].range_end) == (1, 500)
        assert blocks[1].range_start == 501
        assert blocks[2].range_start == 1001

        for earlier, later in pairwise(blocks):
            assert later.range_start > earlier.range_end

    def test_block_accounting(self, activated) -> None:
        block = services.allocate_invoice_block(activated.device, size=100)
        assert (block.size, block.remaining, block.used) == (100, 100, 0)
        assert not block.is_exhausted

        block.next_unused = block.range_end + 1
        block.save()
        assert block.is_exhausted
        assert block.remaining == 0

    def test_gaps_are_reported_not_hidden(self, activated) -> None:
        """An accountant asking about missing numbers gets an answer, not a suspicion."""
        block = services.allocate_invoice_block(activated.device, size=500)
        block.next_unused = block.range_start + 187
        block.exhausted_at = timezone.now()
        block.save()

        assert services.block_gaps(activated.device.branch) == [
            {"from": 188, "to": 500, "count": 313}
        ]


class TestLicensingAPI:
    def test_activation_endpoint(self, issued, api) -> None:
        response = api.post(
            "/api/v1/licensing/activate/",
            {
                "license_key": issued.plaintext_key,
                "email": "owner@caesar.test",
                "device_name": "Cashier-01",
                "mode": "POS",
                "app_version": "0.1.0",
            },
            format="json",
        )
        assert response.status_code == 201
        data = response.json()["data"]
        assert data["device_secret"]
        assert ot.verify(data["offline_token"], PUBLIC)["device_id"] == data["device_id"]

    def test_activation_endpoint_is_public(self, issued, api) -> None:
        """A device has no credentials yet — it is throttled instead."""
        response = api.post(
            "/api/v1/licensing/activate/",
            {
                "license_key": issued.plaintext_key,
                "email": "owner@caesar.test",
                "device_name": "Cashier-01",
            },
            format="json",
        )
        assert response.status_code != 401

    def test_bad_key_returns_the_stable_code(self, api) -> None:
        response = api.post(
            "/api/v1/licensing/activate/",
            {
                "license_key": "QSR-0000-0000-0000-0000",
                "email": "owner@caesar.test",
                "device_name": "X",
            },
            format="json",
        )
        assert response.status_code == 404
        assert response.json()["code"] == "LICENSE_NOT_FOUND"

    def test_device_token_exchange(self, activated, api) -> None:
        response = api.post(
            "/api/v1/licensing/device-token/",
            {
                "device_id": str(activated.device.id),
                "device_secret": activated.device_secret,
            },
            format="json",
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["access"]
        assert data["can_open_new_orders"] is True

    def test_issuing_a_licence_shows_the_key_exactly_once(self, make_user, authed) -> None:
        client = authed(make_user(role="SUPER_ADMIN"))

        created = client.post(
            "/api/v1/licensing/licenses/",
            {
                "customer_email": "cafe@example.test",
                "license_type": "YEARLY",
                "max_devices": 2,
                "expires_at": (timezone.now() + timedelta(days=365)).isoformat(),
            },
            format="json",
        )
        assert created.status_code == 201
        assert created.json()["data"]["license_key"].startswith("QSR-")

        listed = client.get("/api/v1/licensing/licenses/").json()["data"]
        assert all("license_key" not in row for row in listed)
        assert all("••••" in row["masked_key"] for row in listed)

    def test_lifetime_licence_needs_no_expiry(self, make_user, authed) -> None:
        client = authed(make_user(role="SUPER_ADMIN"))
        response = client.post(
            "/api/v1/licensing/licenses/",
            {"customer_email": "cafe@example.test", "license_type": "LIFETIME"},
            format="json",
        )
        assert response.status_code == 201

    def test_non_lifetime_licence_requires_an_expiry(self, make_user, authed) -> None:
        client = authed(make_user(role="SUPER_ADMIN"))
        response = client.post(
            "/api/v1/licensing/licenses/",
            {"customer_email": "cafe@example.test", "license_type": "YEARLY"},
            format="json",
        )
        assert response.status_code == 400

    def test_cashier_cannot_manage_licences(self, make_user, authed) -> None:
        """docs/05 exclusion #1, enforced end to end."""
        client = authed(make_user(role="CASHIER"))
        assert client.get("/api/v1/licensing/licenses/").status_code == 403

    def test_branch_manager_can_view_but_not_issue(self, make_user, authed, branch) -> None:
        client = authed(make_user(role="BRANCH_MANAGER"), branch=branch)
        assert client.get("/api/v1/licensing/licenses/").status_code == 200
        assert (
            client.post(
                "/api/v1/licensing/licenses/",
                {"customer_email": "x@y.test", "license_type": "LIFETIME"},
                format="json",
            ).status_code
            == 403
        )

    def test_revoking_a_licence_kills_every_device(
        self, issued, activated, make_user, authed
    ) -> None:
        client = authed(make_user(role="SUPER_ADMIN"))
        response = client.post(
            f"/api/v1/licensing/licenses/{issued.license.id}/revoke/", {}, format="json"
        )
        assert response.status_code == 200

        activated.device.refresh_from_db()
        assert activated.device.status == DeviceStatus.REVOKED

        event = issued.license.events.filter(event=LicenseEvent.Event.REVOKED).first()
        assert event is not None
        assert event.detail["devices_revoked"] == 1

    def test_regenerating_a_key_kills_the_old_one(self, issued, make_user, authed) -> None:
        old_key = issued.plaintext_key
        client = authed(make_user(role="SUPER_ADMIN"))

        response = client.post(
            f"/api/v1/licensing/licenses/{issued.license.id}/regenerate-key/",
            {},
            format="json",
        )
        new_key = response.json()["data"]["license_key"]
        assert new_key != old_key

        with pytest.raises(services.LicenseNotFound):
            services.activate(license_key=old_key, email="owner@caesar.test", device_name="X")
        assert services.activate(license_key=new_key, email="owner@caesar.test", device_name="X")

    def test_device_reset_frees_a_seat(self, issued, activated, make_user, authed) -> None:
        client = authed(make_user(role="SUPER_ADMIN"))
        response = client.post(
            f"/api/v1/licensing/devices/{activated.device.id}/reset/", {}, format="json"
        )
        assert response.status_code == 200
        assert not Device.objects.filter(id=activated.device.id).exists()

        issued.license.refresh_from_db()
        assert issued.license.seats_available == 3

    def test_every_admin_action_is_recorded_with_the_actor(self, issued, make_user, authed) -> None:
        admin = make_user(role="SUPER_ADMIN", email="admin@caesar.test")
        client = authed(admin)
        client.post(f"/api/v1/licensing/licenses/{issued.license.id}/suspend/", {}, format="json")

        event = issued.license.events.filter(event=LicenseEvent.Event.SUSPENDED).first()
        assert event is not None
        assert event.actor_id == admin.id

    def test_licences_are_scoped_to_the_organization(
        self, issued, make_user, authed, other_organization
    ) -> None:
        outsider = make_user(
            email="outsider@other.test", role="SUPER_ADMIN", org=other_organization
        )
        client = authed(outsider)

        assert client.get("/api/v1/licensing/licenses/").json()["data"] == []
        assert client.get(f"/api/v1/licensing/licenses/{issued.license.id}/").status_code == 404

    def test_heartbeat_denies_a_revoked_device(self, activated, authed, make_user) -> None:
        client = authed(make_user(role="CASHIER"), kind="POS", device_id=activated.device.id)
        Device.objects.filter(pk=activated.device.pk).update(status=DeviceStatus.REVOKED)

        response = client.post("/api/v1/licensing/heartbeat/", {}, format="json")

        assert response.status_code == 403
        assert response.json()["code"] == "DEVICE_REVOKED"
        assert activated.device.license.events.filter(
            event=LicenseEvent.Event.HEARTBEAT_DENIED
        ).exists()

    def test_heartbeat_returns_a_fresh_token_and_state(self, activated, authed, make_user) -> None:
        client = authed(make_user(role="CASHIER"), kind="POS", device_id=activated.device.id)
        response = client.post(
            "/api/v1/licensing/heartbeat/", {"app_version": "0.2.0"}, format="json"
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["stage"] == "ACTIVE"
        assert data["can_open_new_orders"] is True
        assert ot.verify(data["offline_token"], PUBLIC)["device_id"] == str(activated.device.id)

        activated.device.refresh_from_db()
        assert activated.device.app_version == "0.2.0"
        assert activated.device.last_seen_at is not None

    def test_heartbeat_requires_a_device(self, make_user, authed) -> None:
        client = authed(make_user(role="CASHIER"))  # WEB session, no device
        response = client.post("/api/v1/licensing/heartbeat/", {}, format="json")
        assert response.status_code == 400
        assert response.json()["code"] == "DEVICE_REQUIRED"
