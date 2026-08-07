"""
TOTP (RFC 6238) for admin MFA.

Implemented directly rather than pulling a dependency: the algorithm is ~30
lines, fully specified, and — decisively — has official test vectors, so this
implementation is verified against the RFC itself in tests/test_totp.py rather
than against its own behaviour.

MFA is mandatory for the roles in `security.require_mfa_for_roles` (C11): an
account that can change prices, void sales and manage licences is reachable from
the public internet, and a password alone is not adequate for that.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

DIGITS = 6
PERIOD = 30
#: Accept one step either side — clocks drift, and a user typing a code as it
#: rolls over should not be told they are wrong.
DEFAULT_WINDOW = 1


def generate_secret(length: int = 20) -> str:
    """Base32 secret, 160 bits — the RFC 4226 recommendation."""
    return base64.b32encode(secrets.token_bytes(length)).decode("ascii").rstrip("=")


def _hotp(secret_bytes: bytes, counter: int, digits: int, algorithm) -> str:
    mac = hmac.new(secret_bytes, struct.pack(">Q", counter), algorithm).digest()
    offset = mac[-1] & 0x0F
    truncated = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(truncated % (10**digits)).zfill(digits)


def _decode_secret(secret: str) -> bytes:
    padded = secret.upper() + "=" * (-len(secret) % 8)
    return base64.b32decode(padded, casefold=True)


def totp(
    secret: str,
    *,
    at: int | None = None,
    digits: int = DIGITS,
    period: int = PERIOD,
    algorithm=hashlib.sha1,
) -> str:
    timestamp = int(at if at is not None else time.time())
    return _hotp(_decode_secret(secret), timestamp // period, digits, algorithm)


def verify(
    secret: str,
    code: str,
    *,
    at: int | None = None,
    window: int = DEFAULT_WINDOW,
    digits: int = DIGITS,
    period: int = PERIOD,
) -> bool:
    if not secret or not code:
        return False

    code = code.strip().replace(" ", "")
    if not code.isdigit() or len(code) != digits:
        return False

    timestamp = int(at if at is not None else time.time())
    counter = timestamp // period
    secret_bytes = _decode_secret(secret)

    # Constant-time compare against every candidate, and do not short-circuit —
    # an early return leaks which step matched via timing.
    matched = False
    for drift in range(-window, window + 1):
        candidate = _hotp(secret_bytes, counter + drift, digits, hashlib.sha1)
        if hmac.compare_digest(candidate, code):
            matched = True
    return matched


def provisioning_uri(secret: str, *, account: str, issuer: str = "Caesar Cafe") -> str:
    """otpauth:// URI for Google Authenticator / Authy, rendered as a QR code."""
    label = quote(f"{issuer}:{account}")
    return (
        f"otpauth://totp/{label}"
        f"?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1"
        f"&digits={DIGITS}&period={PERIOD}"
    )


def generate_recovery_codes(count: int = 8) -> list[str]:
    """
    Single-use recovery codes.

    A cafe owner who loses their phone at 7am must be able to get in without
    waiting for a developer. Stored hashed, exactly like passwords.
    """
    return [
        f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}"
        for _ in range(count)
    ]
