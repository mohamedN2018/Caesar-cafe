"""
Offline licence tokens: signature, tampering, expiry, and the clock ratchet.

These are the controls that stop the Desktop's offline mode from being defeated
with Notepad.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest

from apps.licensing import offline_token as ot

PRIVATE, PUBLIC = ot.generate_keypair()
OTHER_PRIVATE, OTHER_PUBLIC = ot.generate_keypair()

NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)


def make_payload(**overrides):
    payload = {
        "v": ot.TOKEN_VERSION,
        "seq": 5,
        "license_id": "0193f4aa-0000-7000-8000-000000000001",
        "branch_id": "0193f5aa-0000-7000-8000-000000000002",
        "device_id": "0193f6aa-0000-7000-8000-000000000003",
        "status": "ACTIVE",
        "stage": "ACTIVE",
        "can_open_new_orders": True,
        "grace_hours": 72,
        "issued_at": NOW.isoformat(),
        "server_time": NOW.isoformat(),
        "token_expires_at": (NOW + timedelta(hours=72)).isoformat(),
    }
    payload.update(overrides)
    return payload


class TestSignAndVerify:
    def test_round_trip(self) -> None:
        token = ot.sign(make_payload(), PRIVATE)
        assert ot.verify(token, PUBLIC)["seq"] == 5

    def test_token_is_two_base64url_parts(self) -> None:
        token = ot.sign(make_payload(), PRIVATE)
        assert token.count(".") == 1
        assert "+" not in token and "/" not in token  # url-safe alphabet

    def test_a_different_key_cannot_verify(self) -> None:
        token = ot.sign(make_payload(), OTHER_PRIVATE)
        with pytest.raises(ot.TokenSignatureInvalid):
            ot.verify(token, PUBLIC)

    def test_editing_the_payload_breaks_the_signature(self) -> None:
        """The whole point: you cannot grant yourself a longer licence."""
        token = ot.sign(make_payload(), PRIVATE)
        body_b64, _, signature = token.partition(".")

        payload = json.loads(base64.urlsafe_b64decode(body_b64 + "=="))
        payload["token_expires_at"] = "2099-01-01T00:00:00+00:00"
        forged_body = (
            base64.urlsafe_b64encode(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            )
            .decode()
            .rstrip("=")
        )

        with pytest.raises(ot.TokenSignatureInvalid):
            ot.verify(f"{forged_body}.{signature}", PUBLIC)

    def test_flipping_one_signature_bit_is_rejected(self) -> None:
        token = ot.sign(make_payload(), PRIVATE)
        body, _, signature = token.partition(".")
        mutated = ("A" if signature[0] != "A" else "B") + signature[1:]
        with pytest.raises(ot.TokenSignatureInvalid):
            ot.verify(f"{body}.{mutated}", PUBLIC)

    @pytest.mark.parametrize("bad", ["", "no-separator", "a.b.c.d", "!!!.???"])
    def test_malformed_tokens_are_rejected(self, bad: str) -> None:
        with pytest.raises(ot.TokenError):
            ot.verify(bad, PUBLIC)

    def test_unknown_version_is_rejected(self) -> None:
        token = ot.sign(make_payload(v=999), PRIVATE)
        with pytest.raises(ot.TokenMalformed):
            ot.verify(token, PUBLIC)


class TestRatchet:
    def test_first_token_is_accepted_and_advances_state(self) -> None:
        token = ot.sign(make_payload(seq=5), PRIVATE)
        payload, state = ot.accept(token, PUBLIC, ot.RatchetState(), now=NOW)

        assert payload["seq"] == 5
        assert state.highest_seq == 5
        assert state.highest_server_time == NOW.isoformat()

    def test_newer_token_advances(self) -> None:
        state = ot.RatchetState(highest_server_time=NOW.isoformat(), highest_seq=5)
        later = NOW + timedelta(days=1)
        token = ot.sign(
            make_payload(
                seq=6,
                server_time=later.isoformat(),
                token_expires_at=(later + timedelta(hours=72)).isoformat(),
            ),
            PRIVATE,
        )
        _, new_state = ot.accept(token, PUBLIC, state, now=later)
        assert new_state.highest_seq == 6

    def test_replaying_an_older_token_is_rejected(self) -> None:
        """Stops swapping back to a token issued before the licence lapsed."""
        state = ot.RatchetState(highest_server_time=NOW.isoformat(), highest_seq=10)
        old = ot.sign(make_payload(seq=4), PRIVATE)

        with pytest.raises(ot.TokenReplayed):
            ot.accept(old, PUBLIC, state, now=NOW)

    def test_clock_rollback_is_refused(self) -> None:
        """The obvious attack on any offline expiry."""
        state = ot.RatchetState(highest_server_time=NOW.isoformat(), highest_seq=5)
        token = ot.sign(make_payload(seq=6), PRIVATE)

        with pytest.raises(ot.ClockRolledBack):
            ot.accept(token, PUBLIC, state, now=NOW - timedelta(days=30))

    def test_expired_token_is_refused(self) -> None:
        token = ot.sign(make_payload(), PRIVATE)
        with pytest.raises(ot.TokenExpired):
            ot.accept(token, PUBLIC, ot.RatchetState(), now=NOW + timedelta(hours=73))

    def test_token_is_valid_right_up_to_the_grace_boundary(self) -> None:
        token = ot.sign(make_payload(), PRIVATE)
        payload, _ = ot.accept(
            token, PUBLIC, ot.RatchetState(), now=NOW + timedelta(hours=71, minutes=59)
        )
        assert payload["status"] == "ACTIVE"

    def test_a_forged_token_never_reaches_the_ratchet(self) -> None:
        """Signature is checked before any payload value is read."""
        token = ot.sign(make_payload(seq=9999), OTHER_PRIVATE)
        with pytest.raises(ot.TokenSignatureInvalid):
            ot.accept(token, PUBLIC, ot.RatchetState(highest_seq=1), now=NOW)

    def test_state_survives_a_json_round_trip(self) -> None:
        """The Desktop persists this between runs."""
        state = ot.RatchetState(highest_server_time=NOW.isoformat(), highest_seq=7)
        restored = ot.RatchetState.from_dict(json.loads(json.dumps(state.as_dict())))
        assert restored == state

    def test_missing_state_starts_clean(self) -> None:
        state = ot.RatchetState.from_dict(None)
        assert state.highest_seq == -1
        assert state.highest_server_time is None


class TestKeypair:
    def test_raw_ed25519_sizes(self) -> None:
        private, public = ot.generate_keypair()
        assert len(private) == 32
        assert len(public) == 32

    def test_keypairs_are_unique(self) -> None:
        assert ot.generate_keypair()[0] != ot.generate_keypair()[0]
