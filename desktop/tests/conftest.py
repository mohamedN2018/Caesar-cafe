from __future__ import annotations

import base64
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

# Must be set before Qt is imported: the offscreen platform is what lets the
# widget tests run headless in CI and in Docker.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from caesar_pos.vendored import offline_token as ot  # noqa: E402

#: A dedicated keypair for the suite. The public half is injected into config so
#: the gate verifies against exactly what these tests sign with.
TEST_PRIVATE, TEST_PUBLIC = ot.generate_keypair()

NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _isolated_environment(tmp_path, monkeypatch):
    """Every test gets its own data directory and signing key."""
    monkeypatch.setenv("CAESAR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CAESAR_LICENSE_PUBLIC_KEY", base64.b64encode(TEST_PUBLIC).decode())

    import caesar_pos.config as config
    import caesar_pos.security.license_gate as gate

    monkeypatch.setattr(config, "LICENSE_PUBLIC_KEY_B64", base64.b64encode(TEST_PUBLIC).decode())
    monkeypatch.setattr(gate, "LICENSE_PUBLIC_KEY_B64", base64.b64encode(TEST_PUBLIC).decode())
    yield


@pytest.fixture
def make_token():
    """Sign a licence token the way the server would."""

    def _make(*, seq: int = 1, issued_at: datetime = NOW, grace_hours: int = 72, **overrides):
        payload = {
            "v": ot.TOKEN_VERSION,
            "seq": seq,
            "license_id": "0193f4aa-0000-7000-8000-000000000001",
            "branch_id": "0193f5aa-0000-7000-8000-000000000002",
            "device_id": "0193f6aa-0000-7000-8000-000000000003",
            "device_mode": "POS",
            "status": "ACTIVE",
            "stage": "ACTIVE",
            "can_open_new_orders": True,
            "license_expires_at": (issued_at + timedelta(days=200)).isoformat(),
            "grace_hours": grace_hours,
            "expiry_policy": "BLOCK_NEW_ORDERS",
            "issued_at": issued_at.isoformat(),
            "server_time": issued_at.isoformat(),
            "token_expires_at": (issued_at + timedelta(hours=grace_hours)).isoformat(),
        }
        payload.update(overrides)
        return ot.sign(payload, TEST_PRIVATE)

    return _make


@pytest.fixture
def fake_keyring(monkeypatch):
    """In-memory credential store, so tests never touch the real OS keychain."""
    store: dict[tuple[str, str], str] = {}

    import keyring

    monkeypatch.setattr(keyring, "get_password", lambda s, k: store.get((s, k)))
    monkeypatch.setattr(keyring, "set_password", lambda s, k, v: store.__setitem__((s, k), v))
    monkeypatch.setattr(keyring, "delete_password", lambda s, k: store.pop((s, k), None))
    return store


@pytest.fixture
def state_file(tmp_path):
    from caesar_pos.config import paths

    return paths().state_file


def write_state(path, token=None, ratchet=None):
    path.write_text(json.dumps({"token": token, "ratchet": ratchet or {}}), encoding="utf-8")
