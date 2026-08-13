"""
Ed25519-signed offline license tokens (commitment C5).

The Desktop must start and sell during an outage. But if the local check is
"read a JSON file and see if it says valid: true", the whole system is defeated
with Notepad — the explicit failure mode docs/09 warns about.

So the server issues a signed, self-contained token:

  * the PRIVATE key lives only on the server and never ships
  * the PUBLIC key is embedded in the Desktop binary; verification is local and
    needs no network
  * every successful heartbeat returns a fresh token, sliding the window
    forward. A terminal that is online daily never notices this exists
  * forging one requires the private key; editing the JSON breaks the
    signature; deleting the file means "not activated" — it cannot mean
    "valid forever"

This module is deliberately Django-free: `verify()` and `TokenRatchet` are
vendored verbatim into the PySide6 client, so both sides run identical logic.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

TOKEN_VERSION = 1
SEPARATOR = "."


class TokenError(Exception):
    """Base for every offline-token failure."""


class TokenMalformed(TokenError):
    pass


class TokenSignatureInvalid(TokenError):
    """The payload was edited, or signed by someone else."""


class TokenExpired(TokenError):
    pass


class TokenReplayed(TokenError):
    """An older token presented after a newer one — a rollback attempt."""


class ClockRolledBack(TokenError):
    """System clock is earlier than a time we have already observed."""


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def sign(payload: dict[str, Any], private_key_bytes: bytes) -> str:
    """Produce `<base64url(json)>.<base64url(signature)>`."""
    key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"{_b64encode(body)}{SEPARATOR}{_b64encode(key.sign(body))}"


def verify(token: str, public_key_bytes: bytes) -> dict[str, Any]:
    """
    Check the signature and return the payload.

    Signature is verified BEFORE anything in the payload is read, so a forged
    token cannot influence any decision made here.
    """
    if not token or SEPARATOR not in token:
        raise TokenMalformed("token is not <payload>.<signature>")

    body_b64, _, signature_b64 = token.partition(SEPARATOR)
    try:
        body = _b64decode(body_b64)
        signature = _b64decode(signature_b64)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise TokenMalformed("token is not valid base64url") from exc

    try:
        Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(signature, body)
    except InvalidSignature as exc:
        raise TokenSignatureInvalid("signature does not match") from exc

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise TokenMalformed("payload is not JSON") from exc

    if payload.get("v") != TOKEN_VERSION:
        raise TokenMalformed(f"unsupported token version {payload.get('v')!r}")
    return payload


def _parse(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp)


@dataclass
class RatchetState:
    """
    Persisted on the Desktop between runs.

    `highest_server_time` is the monotonic high-water mark; `highest_seq` is the
    token sequence. Both only ever move forward.
    """

    highest_server_time: str | None = None
    highest_seq: int = -1

    def as_dict(self) -> dict[str, Any]:
        return {
            "highest_server_time": self.highest_server_time,
            "highest_seq": self.highest_seq,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RatchetState:
        data = data or {}
        return cls(
            highest_server_time=data.get("highest_server_time"),
            highest_seq=int(data.get("highest_seq", -1)),
        )


def accept(
    token: str,
    public_key_bytes: bytes,
    state: RatchetState,
    *,
    now: datetime | None = None,
) -> tuple[dict[str, Any], RatchetState]:
    """
    Verify a token and advance the ratchet.

    Three layered defences against the obvious attack on any offline expiry —
    setting the system clock backwards:

      1. Monotonic high-water mark: if the system clock reads earlier than the
         newest server time we have ever seen, refuse. Time only moves forward
         from the app's perspective.
      2. Sequence ratchet: a token whose sequence is below the stored one is
         rejected, so an old token cannot be replayed after a newer one.
      3. (Server-side, elsewhere) reconciliation flags implausible client
         timestamps on the next sync, so tampering leaves a trail even if it
         briefly works.

    Returns the payload and the NEW state, which the caller must persist.
    """
    payload = verify(token, public_key_bytes)
    current = now or datetime.now(UTC)

    sequence = int(payload.get("seq", 0))
    if sequence < state.highest_seq:
        raise TokenReplayed(f"token sequence {sequence} is older than {state.highest_seq}")

    server_time = _parse(payload["server_time"])
    if state.highest_server_time is not None:
        watermark = _parse(state.highest_server_time)
        if current < watermark:
            raise ClockRolledBack(
                f"system clock {current.isoformat()} is behind the last known "
                f"server time {watermark.isoformat()}"
            )

    if current > _parse(payload["token_expires_at"]):
        raise TokenExpired("offline grace window has elapsed")

    newest = (
        max(server_time, _parse(state.highest_server_time))
        if state.highest_server_time
        else server_time
    )
    return payload, RatchetState(
        highest_server_time=newest.isoformat(),
        highest_seq=max(sequence, state.highest_seq),
    )


def generate_keypair() -> tuple[bytes, bytes]:
    """(private_bytes, public_bytes) — used by the key-generation command."""
    from cryptography.hazmat.primitives import serialization

    private = Ed25519PrivateKey.generate()
    return (
        private.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ),
    )
