"""
Activation, seat limits, and the concurrency race that a naive implementation
gets wrong.
"""

from __future__ import annotations

import threading
from datetime import timedelta
from itertools import pairwise

import pytest
from django.db import connections
from django.utils import timezone

from apps.licensing import services
from apps.licensing.models import Device, DeviceStatus, License, LicenseEvent, LicenseStatus

pytestmark = pytest.mark.django_db


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


class TestIssuance:
    def test_plaintext_key_is_never_stored(self, issued) -> None:
        key = issued.plaintext_key
        body = "".join(key.split("-")[1:])

        for value in License.objects.values_list("key_hash", "key_prefix", "key_last4"):
            joined = " ".join(value)
            assert body not in joined
            assert key not in joined

    def test_only_prefix_and_last4_are_recoverable(self, issued) -> None:
        license_obj = issued.license
        assert license_obj.key_prefix == issued.plaintext_key[:8]
        assert license_obj.key_last4 == issued.plaintext_key[-4:]
        assert "••••" in license_obj.masked_key

    def test_starts_pending_until_first_activation(self, issued) -> None:
        assert issued.license.status == LicenseStatus.PENDING

    def test_creation_is_recorded(self, issued) -> None:
        assert issued.license.events.filter(event=LicenseEvent.Event.CREATED).exists()


class TestActivation:
    def _activate(self, issued, name="Cashier-01", **kwargs):
        return services.activate(
            license_key=kwargs.pop("license_key", issued.plaintext_key),
            email=kwargs.pop("email", "owner@caesar.test"),
            device_name=name,
            **kwargs,
        )

    def test_happy_path(self, issued) -> None:
        activation = self._activate(issued)

        assert activation.device.status == DeviceStatus.ACTIVE
        assert activation.device_secret
        assert activation.offline_token

        issued.license.refresh_from_db()
        assert issued.license.status == LicenseStatus.ACTIVE
        assert issued.license.activation_count == 1

    def test_device_secret_is_stored_hashed_only(self, issued) -> None:
        activation = self._activate(issued)
        activation.device.refresh_from_db()
        assert activation.device_secret not in activation.device.secret_hash
        assert activation.device.secret_hash.startswith(("argon2", "md5", "pbkdf2"))

    def test_secret_is_256_bits(self, issued) -> None:
        assert len(self._activate(issued).device_secret) >= 40  # 32 bytes b64url

    def test_two_activations_get_different_secrets(self, issued) -> None:
        a = self._activate(issued, "Cashier-01")
        b = self._activate(issued, "Cashier-02")
        assert a.device_secret != b.device_secret

    def test_accepts_a_mistyped_but_normalizable_key(self, issued) -> None:
        sloppy = issued.plaintext_key.lower().replace("-", " ")
        assert self._activate(issued, license_key=sloppy).device is not None

    def test_unknown_key_is_refused(self, issued) -> None:
        with pytest.raises(services.LicenseNotFound):
            self._activate(issued, license_key="QSR-0000-0000-0000-0000")

    def test_malformed_key_is_refused(self, issued) -> None:
        with pytest.raises(services.LicenseNotFound):
            self._activate(issued, license_key="not-a-key")

    def test_email_must_match(self, issued) -> None:
        with pytest.raises(services.LicenseEmailMismatch):
            self._activate(issued, email="someone-else@caesar.test")

    def test_email_match_is_case_insensitive(self, issued) -> None:
        assert self._activate(issued, email="OWNER@CAESAR.TEST").device is not None

    def test_suspended_licence_is_refused(self, issued) -> None:
        License.objects.filter(pk=issued.license.pk).update(status=LicenseStatus.SUSPENDED)
        with pytest.raises(services.LicenseSuspended):
            self._activate(issued)

    def test_revoked_licence_is_refused(self, issued) -> None:
        License.objects.filter(pk=issued.license.pk).update(status=LicenseStatus.REVOKED)
        with pytest.raises(services.LicenseRevoked):
            self._activate(issued)

    def test_expired_licence_is_refused_with_the_date(self, issued) -> None:
        License.objects.filter(pk=issued.license.pk).update(
            expires_at=timezone.now() - timedelta(days=1)
        )
        with pytest.raises(services.LicenseExpired) as exc:
            self._activate(issued)
        assert "expires_at" in exc.value.extra

    def test_not_yet_started_licence_is_refused(self, issued) -> None:
        License.objects.filter(pk=issued.license.pk).update(
            starts_at=timezone.now() + timedelta(days=7)
        )
        with pytest.raises(services.ActivationError):
            self._activate(issued)

    def test_failures_are_recorded(self, issued) -> None:
        with pytest.raises(services.LicenseEmailMismatch):
            self._activate(issued, email="wrong@caesar.test")

        event = issued.license.events.filter(event=LicenseEvent.Event.ACTIVATION_FAILED).first()
        assert event is not None
        assert event.detail["reason"] == "LICENSE_EMAIL_MISMATCH"

    def test_reactivating_the_same_device_reuses_its_seat(self, issued) -> None:
        """Reinstalling Windows must not burn a seat."""
        first = self._activate(issued, "Cashier-01")
        second = self._activate(issued, "Cashier-01")

        assert first.device.id == second.device.id
        assert first.device_secret != second.device_secret, "secret must be rotated"
        assert Device.objects.count() == 1

    def test_a_revoked_device_cannot_reactivate(self, issued) -> None:
        activation = self._activate(issued, "Cashier-01")
        Device.objects.filter(pk=activation.device.pk).update(status=DeviceStatus.REVOKED)

        with pytest.raises(services.DeviceRevoked):
            self._activate(issued, "Cashier-01")

    def test_fingerprint_change_is_counted_not_enforced(self, issued) -> None:
        """C4: the fingerprint is diagnostic. It must never block activation."""
        self._activate(issued, "Cashier-01", fingerprint="AAAA")
        activation = self._activate(issued, "Cashier-01", fingerprint="BBBB")

        activation.device.refresh_from_db()
        assert activation.device.status == DeviceStatus.ACTIVE
        assert activation.device.fingerprint_changed_count == 1


class TestSeatLimit:
    def _fill(self, issued, count, *, prefix="Terminal"):
        return [
            services.activate(
                license_key=issued.plaintext_key,
                email="owner@caesar.test",
                device_name=f"{prefix}-{i}",
            )
            for i in range(count)
        ]

    def test_seats_are_enforced(self, issued) -> None:
        self._fill(issued, 3)
        # A DIFFERENT name — reusing one would legitimately reuse its seat.
        with pytest.raises(services.DeviceLimitReached) as exc:
            self._fill(issued, 1, prefix="Extra")
        assert exc.value.extra == {"used": 3, "max": 3}

    def test_the_message_names_the_remedy(self, issued) -> None:
        """ "Activation failed" sends a cashier to the phone; this does not."""
        self._fill(issued, 3)
        with pytest.raises(services.DeviceLimitReached) as exc:
            services.activate(
                license_key=issued.plaintext_key,
                email="owner@caesar.test",
                device_name="Terminal-X",
            )
        message = str(exc.value.detail)
        assert "3/3" in message
        assert "إلغاء تفعيل" in message

    def test_revoking_a_device_frees_a_seat(self, issued) -> None:
        devices = self._fill(issued, 3)
        Device.objects.filter(pk=devices[0].device.pk).update(status=DeviceStatus.REVOKED)

        issued.license.refresh_from_db()
        assert issued.license.seats_available == 1
        assert services.activate(
            license_key=issued.plaintext_key,
            email="owner@caesar.test",
            device_name="Replacement",
        )


@pytest.mark.django_db(transaction=True)
class TestConcurrentActivation:
    """
    Phase 3 exit criterion.

    Without `SELECT ... FOR UPDATE`, three simultaneous activations against a
    3-seat licence each read activation_count = 2, each conclude there is room,
    and each insert — leaving 5 devices on a 3-seat plan. Same class of bug as
    the inventory race in docs/02: a check-then-write over a shared counter.
    """

    def test_ten_parallel_activations_against_three_seats(self, organization, branch) -> None:
        issued = services.issue_license(
            organization=organization,
            branch=branch,
            customer_email="owner@caesar.test",
            license_type="YEARLY",
            max_devices=3,
            expires_at=timezone.now() + timedelta(days=365),
        )

        results: list[str] = []
        lock = threading.Lock()
        start = threading.Barrier(10)

        def worker(index: int) -> None:
            try:
                start.wait(timeout=10)
                services.activate(
                    license_key=issued.plaintext_key,
                    email="owner@caesar.test",
                    device_name=f"Terminal-{index}",
                )
                outcome = "granted"
            except services.DeviceLimitReached:
                outcome = "refused"
            except Exception as exc:  # surfaced in the assertion below
                outcome = f"error:{type(exc).__name__}"
            finally:
                connections.close_all()
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        granted = results.count("granted")
        errors = [r for r in results if r.startswith("error:")]

        assert not errors, f"unexpected failures: {errors}"
        assert granted == 3, f"expected exactly 3 seats granted, got {granted}: {results}"
        assert Device.objects.filter(license=issued.license).count() == 3

    def test_parallel_invoice_block_allocation_never_overlaps(self, organization, branch) -> None:
        """Two devices requesting simultaneously must get disjoint ranges (C9)."""
        issued = services.issue_license(
            organization=organization,
            branch=branch,
            customer_email="owner@caesar.test",
            license_type="YEARLY",
            max_devices=5,
            expires_at=timezone.now() + timedelta(days=365),
        )
        devices = [
            services.activate(
                license_key=issued.plaintext_key,
                email="owner@caesar.test",
                device_name=f"Terminal-{i}",
            ).device
            for i in range(4)
        ]

        blocks: list[tuple[int, int]] = []
        lock = threading.Lock()
        start = threading.Barrier(len(devices))

        def worker(device) -> None:
            try:
                start.wait(timeout=10)
                block = services.allocate_invoice_block(device, size=500)
                with lock:
                    blocks.append((block.range_start, block.range_end))
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker, args=(d,)) for d in devices]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert len(blocks) == len(devices)
        blocks.sort()
        for (_, end), (next_start, _) in pairwise(blocks):
            assert next_start > end, f"overlapping invoice blocks: {blocks}"
