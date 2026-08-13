"""
Startup sequencing: online refresh, offline fallback, and server rejection.

The distinction that matters here is between "the server said no" and "the
server did not answer". The first is authoritative and must not be softened by
falling back to a cached token; the second is exactly what the cached token is
for.
"""

from __future__ import annotations

import pytest

from caesar_pos import bootstrap
from caesar_pos.api.client import ApiClient, ApiError, NetworkUnavailable
from caesar_pos.bootstrap import Screen
from caesar_pos.security import credentials
from caesar_pos.security.license_gate import load_state, save_state

from .conftest import NOW


@pytest.fixture
def client():
    return ApiClient(base_url="http://server.test")


@pytest.fixture
def activated(fake_keyring):
    credentials.store(credentials.DeviceCredential(device_id="dev-1", device_secret="s3cret"))
    return credentials.load()


def _seed_token(token: str) -> None:
    from caesar_pos.config import paths

    state = load_state(paths().state_file)
    state.token = token
    save_state(paths().state_file, state)


class TestNotActivated:
    def test_a_fresh_machine_goes_to_activation(self, client, fake_keyring) -> None:
        startup = bootstrap.start(client, now=NOW)
        assert startup.screen is Screen.ACTIVATION
        assert startup.online is False


class TestOffline:
    def test_a_cached_token_starts_the_app_without_a_server(
        self, client, activated, make_token, monkeypatch
    ) -> None:
        """The whole point of the offline design."""
        _seed_token(make_token())
        monkeypatch.setattr(
            bootstrap,
            "obtain_device_token",
            lambda *a, **k: (_ for _ in ()).throw(NetworkUnavailable()),
        )

        startup = bootstrap.start(client, now=NOW)

        assert startup.screen is Screen.LOGIN
        assert startup.online is False
        assert startup.gate.can_open_new_orders

    def test_an_expired_offline_window_blocks(
        self, client, activated, make_token, monkeypatch
    ) -> None:
        from datetime import timedelta

        _seed_token(make_token(grace_hours=72))
        monkeypatch.setattr(
            bootstrap,
            "obtain_device_token",
            lambda *a, **k: (_ for _ in ()).throw(NetworkUnavailable()),
        )

        startup = bootstrap.start(client, now=NOW + timedelta(hours=80))

        assert startup.screen is Screen.BLOCKED
        assert startup.gate.reason_code == "OFFLINE_GRACE_ELAPSED"


class TestOnline:
    def test_a_successful_heartbeat_refreshes_the_token(
        self, client, activated, make_token, monkeypatch
    ) -> None:
        fresh = make_token(seq=42)
        monkeypatch.setattr(bootstrap, "obtain_device_token", lambda *a, **k: {"access": "tok"})
        monkeypatch.setattr(
            bootstrap,
            "heartbeat",
            lambda *a, **k: {"offline_token": fresh, "stage": "ACTIVE"},
        )

        startup = bootstrap.start(client, now=NOW)

        assert startup.online is True
        assert startup.screen is Screen.LOGIN
        assert startup.access_token == "tok"

        from caesar_pos.config import paths

        assert load_state(paths().state_file).token == fresh

    def test_a_revoked_device_is_wiped_and_sent_to_activation(
        self, client, activated, make_token, monkeypatch, fake_keyring
    ) -> None:
        """
        The server said no. That is authoritative — the cached token must not
        be used to keep a revoked terminal running.
        """
        _seed_token(make_token())
        monkeypatch.setattr(
            bootstrap,
            "obtain_device_token",
            lambda *a, **k: (_ for _ in ()).throw(
                ApiError("DEVICE_REVOKED", "تم إلغاء تفعيل هذا الجهاز.")
            ),
        )

        startup = bootstrap.start(client, now=NOW)

        assert startup.screen is Screen.ACTIVATION
        assert startup.gate.reason_code == "DEVICE_REVOKED"
        assert credentials.load() is None, "credential must be cleared"

        from caesar_pos.config import paths

        assert load_state(paths().state_file).token is None

    def test_an_unrelated_server_error_falls_back_to_the_cached_token(
        self, client, activated, make_token, monkeypatch
    ) -> None:
        """A 500 must not take the cafe offline when a valid token is cached."""
        _seed_token(make_token())
        monkeypatch.setattr(
            bootstrap,
            "obtain_device_token",
            lambda *a, **k: (_ for _ in ()).throw(ApiError("INTERNAL_ERROR", "boom")),
        )

        startup = bootstrap.start(client, now=NOW)
        assert startup.screen is Screen.LOGIN
        assert startup.online is False


class TestActivationPersistence:
    def test_completing_activation_stores_both_halves(self, fake_keyring, make_token) -> None:
        token = make_token()
        bootstrap.complete_activation(
            device_id="dev-9", device_secret="secret-9", offline_token=token
        )

        credential = credentials.load()
        assert credential.device_id == "dev-9"
        assert credential.device_secret == "secret-9"

        from caesar_pos.config import paths

        assert load_state(paths().state_file).token == token

    def test_the_secret_never_lands_in_the_state_file(
        self, fake_keyring, make_token, state_file
    ) -> None:
        """Credentials belong in the OS keychain, not beside the executable."""
        bootstrap.complete_activation(
            device_id="dev-9",
            device_secret="super-secret-value",
            offline_token=make_token(),
        )
        assert "super-secret-value" not in state_file.read_text(encoding="utf-8")


class TestCredentials:
    def test_store_load_clear(self, fake_keyring) -> None:
        assert credentials.load() is None
        assert credentials.is_activated() is False

        credentials.store(credentials.DeviceCredential(device_id="d1", device_secret="s1"))
        assert credentials.is_activated() is True
        assert credentials.load().device_secret == "s1"

        credentials.clear()
        assert credentials.load() is None

    def test_clearing_twice_is_not_an_error(self, fake_keyring) -> None:
        credentials.clear()
        credentials.clear()

    def test_a_partial_credential_counts_as_not_activated(self, fake_keyring) -> None:
        import keyring

        from caesar_pos.config import KEYRING_DEVICE_ID, KEYRING_SERVICE

        keyring.set_password(KEYRING_SERVICE, KEYRING_DEVICE_ID, "only-the-id")
        assert credentials.load() is None
