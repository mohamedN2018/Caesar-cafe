"""
The startup gate — the client half of commitment C5.

These are the tests that matter most in the Desktop client: they are what stop
the offline licence check from being defeated by editing a local file.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from caesar_pos.security import license_gate as gate
from caesar_pos.security.license_gate import GateDecision, LicenseState
from caesar_pos.vendored import offline_token as ot

from .conftest import NOW, TEST_PRIVATE


class TestNotActivated:
    def test_no_credential_means_the_activation_screen(self) -> None:
        result, _ = gate.evaluate(LicenseState(), is_activated=False, now=NOW)
        assert result.decision is GateDecision.NOT_ACTIVATED
        assert not result.may_start

    def test_activated_but_tokenless_is_blocked_not_allowed(self) -> None:
        """Fail closed: a missing token can never mean 'permitted'."""
        result, _ = gate.evaluate(LicenseState(), is_activated=True, now=NOW)
        assert result.decision is GateDecision.BLOCKED
        assert result.reason_code == "NO_TOKEN"


class TestValidToken:
    def test_a_healthy_token_allows_startup(self, make_token) -> None:
        state = LicenseState(token=make_token())
        result, advanced = gate.evaluate(state, is_activated=True, now=NOW)

        assert result.decision is GateDecision.ALLOWED
        assert result.can_open_new_orders
        assert result.may_start
        assert advanced.ratchet.highest_seq == 1

    def test_restricted_still_settles_open_tables(self, make_token) -> None:
        """The core of the expiry policy: never strand a customer mid-meal."""
        state = LicenseState(token=make_token(stage="RESTRICTED", can_open_new_orders=False))
        result, _ = gate.evaluate(state, is_activated=True, now=NOW)

        assert result.decision is GateDecision.RESTRICTED
        assert result.can_open_new_orders is False
        assert result.can_close_open_orders is True
        assert result.may_start is True

    def test_grace_stage_sells_normally(self, make_token) -> None:
        state = LicenseState(token=make_token(stage="GRACE"))
        result, _ = gate.evaluate(state, is_activated=True, now=NOW)
        assert result.can_open_new_orders is True
        assert "برجاء التجديد" in result.message_ar

    def test_revoked_status_blocks(self, make_token) -> None:
        state = LicenseState(token=make_token(status="REVOKED"))
        result, _ = gate.evaluate(state, is_activated=True, now=NOW)
        assert result.decision is GateDecision.BLOCKED
        assert result.can_close_open_orders is False

    def test_suspended_still_lets_open_orders_close(self, make_token) -> None:
        state = LicenseState(token=make_token(status="SUSPENDED"))
        result, _ = gate.evaluate(state, is_activated=True, now=NOW)
        assert result.decision is GateDecision.BLOCKED
        assert result.can_close_open_orders is True


class TestTampering:
    def test_editing_the_payload_is_detected(self, make_token) -> None:
        """The whole point: you cannot grant yourself a longer licence."""
        import base64

        token = make_token()
        body_b64, _, signature = token.partition(".")
        payload = json.loads(base64.urlsafe_b64decode(body_b64 + "=="))
        payload["token_expires_at"] = "2099-01-01T00:00:00+00:00"
        payload["stage"] = "ACTIVE"

        forged = (
            base64.urlsafe_b64encode(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            )
            .decode()
            .rstrip("=")
        )
        state = LicenseState(token=f"{forged}.{signature}")

        result, _ = gate.evaluate(state, is_activated=True, now=NOW)
        assert result.decision is GateDecision.BLOCKED
        assert result.reason_code == "TOKEN_TAMPERED"

    def test_a_token_signed_by_someone_else_is_rejected(self) -> None:
        other_private, _ = ot.generate_keypair()
        payload = {
            "v": ot.TOKEN_VERSION,
            "seq": 99,
            "status": "ACTIVE",
            "stage": "ACTIVE",
            "can_open_new_orders": True,
            "server_time": NOW.isoformat(),
            "token_expires_at": (NOW + timedelta(days=3650)).isoformat(),
        }
        state = LicenseState(token=ot.sign(payload, other_private))

        result, _ = gate.evaluate(state, is_activated=True, now=NOW)
        assert result.decision is GateDecision.BLOCKED
        assert result.reason_code == "TOKEN_TAMPERED"

    @pytest.mark.parametrize("garbage", ["", "not-a-token", "a.b.c", "!!!.???"])
    def test_garbage_tokens_are_rejected(self, garbage: str) -> None:
        state = LicenseState(token=garbage)
        result, _ = gate.evaluate(state, is_activated=True, now=NOW)
        assert result.decision is GateDecision.BLOCKED

    def test_a_plain_valid_true_file_grants_nothing(self, state_file) -> None:
        """
        The failure mode docs/09 warns about: if the check were a flag in a
        JSON file, Notepad would defeat the entire licensing system.
        """
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps({"token": "valid", "valid": True, "licensed": True}),
            encoding="utf-8",
        )
        loaded = gate.load_state(state_file)
        result, _ = gate.evaluate(loaded, is_activated=True, now=NOW)

        assert result.decision is GateDecision.BLOCKED


class TestClockAndReplay:
    def test_rolling_the_clock_back_is_refused(self, make_token) -> None:
        """The obvious attack on any offline expiry."""
        state = LicenseState(
            token=make_token(seq=5),
            ratchet=ot.RatchetState(highest_server_time=NOW.isoformat(), highest_seq=4),
        )
        result, _ = gate.evaluate(state, is_activated=True, now=NOW - timedelta(days=30))
        assert result.decision is GateDecision.BLOCKED
        assert result.reason_code == "CLOCK_ROLLED_BACK"

    def test_replaying_an_older_token_is_refused(self, make_token) -> None:
        state = LicenseState(
            token=make_token(seq=2),
            ratchet=ot.RatchetState(highest_server_time=NOW.isoformat(), highest_seq=9),
        )
        result, _ = gate.evaluate(state, is_activated=True, now=NOW)
        assert result.decision is GateDecision.BLOCKED
        assert result.reason_code == "TOKEN_REPLAYED"

    def test_the_offline_window_eventually_closes(self, make_token) -> None:
        state = LicenseState(token=make_token(grace_hours=72))
        result, _ = gate.evaluate(state, is_activated=True, now=NOW + timedelta(hours=73))
        assert result.decision is GateDecision.BLOCKED
        assert result.reason_code == "OFFLINE_GRACE_ELAPSED"

    def test_the_cafe_keeps_working_within_the_window(self, make_token) -> None:
        state = LicenseState(token=make_token(grace_hours=72))
        result, _ = gate.evaluate(state, is_activated=True, now=NOW + timedelta(hours=71))
        assert result.may_start

    def test_the_ratchet_advances_and_persists(self, make_token, state_file) -> None:
        state = LicenseState(token=make_token(seq=7))
        _, advanced = gate.evaluate(state, is_activated=True, now=NOW)
        advanced.token = state.token
        gate.save_state(state_file, advanced)

        reloaded = gate.load_state(state_file)
        assert reloaded.ratchet.highest_seq == 7
        assert reloaded.ratchet.highest_server_time == NOW.isoformat()


class TestStatePersistence:
    def test_a_corrupt_state_file_fails_closed(self, state_file) -> None:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text("{ this is not json", encoding="utf-8")

        state = gate.load_state(state_file)
        assert state.token is None

        result, _ = gate.evaluate(state, is_activated=True, now=NOW)
        assert not result.may_start

    def test_a_missing_state_file_is_not_an_error(self, state_file) -> None:
        assert gate.load_state(state_file).token is None

    def test_round_trip(self, state_file, make_token) -> None:
        original = LicenseState(
            token=make_token(),
            ratchet=ot.RatchetState(highest_server_time=NOW.isoformat(), highest_seq=3),
        )
        gate.save_state(state_file, original)
        reloaded = gate.load_state(state_file)

        assert reloaded.token == original.token
        assert reloaded.ratchet == original.ratchet


class TestServerTokenCompatibility:
    """
    The two halves of C5 meeting.

    This signs with the *same module* the server uses (vendored verbatim), so a
    change to the token format on either side breaks this test rather than
    breaking a cafe at 7am.
    """

    def test_the_gate_accepts_a_server_shaped_token(self) -> None:
        payload = {
            "v": ot.TOKEN_VERSION,
            "seq": 1,
            "license_id": "0193f4aa-0000-7000-8000-000000000001",
            "branch_id": "0193f5aa-0000-7000-8000-000000000002",
            "device_id": "0193f6aa-0000-7000-8000-000000000003",
            "device_mode": "POS",
            "status": "ACTIVE",
            "stage": "ACTIVE",
            "can_open_new_orders": True,
            "license_expires_at": "2027-08-07T00:00:00+00:00",
            "grace_hours": 72,
            "expiry_policy": "BLOCK_NEW_ORDERS",
            "issued_at": NOW.isoformat(),
            "server_time": NOW.isoformat(),
            "token_expires_at": (NOW + timedelta(hours=72)).isoformat(),
        }
        state = LicenseState(token=ot.sign(payload, TEST_PRIVATE))

        result, _ = gate.evaluate(state, is_activated=True, now=NOW)
        assert result.decision is GateDecision.ALLOWED
        assert result.payload["device_mode"] == "POS"
