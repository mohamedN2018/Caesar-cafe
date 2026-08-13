"""
Verifying a PIN against the mirrored hash, offline.

Django hashes PINs with `make_password`, which produces `algorithm$params$salt$hash`.
The terminal mirrors that string and verifies against it locally — that is what
lets a cashier log in and a manager approve a void during an outage.

**What the terminal can and cannot do with it, precisely:**

  * It can check "does this PIN match this hash". That is the whole job.
  * It cannot mint a session. Nothing here produces a token; a POS token comes
    from the server, and an offline login authorises the local UI only. When the
    link returns, the queued operations carry the actor id and the server
    re-checks every permission on the ordinary path.
  * It cannot make a revoked user work. A DELETE on their role assignment
    removes the row from `m_permissions` on the next staff pull, and the
    permission set goes with it.

Two algorithms are supported deliberately: Argon2id, which the server prefers,
and PBKDF2-SHA256, which it keeps as a fallback for hashes written before the
change. A terminal that could not verify a legacy hash would lock out the one
member of staff who has not changed their PIN since.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging

logger = logging.getLogger(__name__)

#: The lockout that makes a 4-digit secret defensible (docs/09, S2). Mirrors the
#: server's `security.pin_lockout_*` defaults; the pulled values override them.
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_LOCKOUT_SECONDS = 900


class UnsupportedHash(ValueError):
    """
    A hash this build cannot verify.

    Deliberately NOT treated as "wrong PIN". Telling a cashier their PIN is
    wrong when the real problem is a server that upgraded its hasher would have
    them retyping it until they lock themselves out.
    """


def verify(raw_pin: str, encoded: str) -> bool:
    """
    False for a mismatch; raises for a hash this build cannot read.

    The two are deliberately different outcomes — see `UnsupportedHash`.
    """
    if not raw_pin or not encoded:
        return False

    algorithm = encoded.split("$", 1)[0]

    if algorithm == "argon2":
        return _verify_argon2(raw_pin, encoded)
    if algorithm.startswith("pbkdf2_sha256"):
        return _verify_pbkdf2(raw_pin, encoded)

    raise UnsupportedHash(
        f"This terminal cannot verify a '{algorithm}' hash. The server is using a "
        "hasher this build does not know — update the client rather than resetting PINs."
    )


def _verify_argon2(raw_pin: str, encoded: str) -> bool:
    """
    `argon2$argon2id$v=19$m=...,t=...,p=...$salt$hash` — Django's prefix on top
    of the standard PHC string.
    """
    try:
        from argon2 import PasswordHasher
        from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
    except ImportError as exc:  # pragma: no cover - argon2-cffi is a hard dependency
        raise UnsupportedHash("argon2-cffi is not installed in this build.") from exc

    phc = encoded.removeprefix("argon2")

    try:
        PasswordHasher().verify(phc, raw_pin)
    except (VerifyMismatchError, VerificationError):
        return False
    except InvalidHashError as exc:
        raise UnsupportedHash("Malformed argon2 hash in the mirror.") from exc
    return True


def _verify_pbkdf2(raw_pin: str, encoded: str) -> bool:
    """`pbkdf2_sha256$iterations$salt$base64-hash`."""
    try:
        _, iterations, salt, expected = encoded.split("$", 3)
        derived = hashlib.pbkdf2_hmac("sha256", raw_pin.encode(), salt.encode(), int(iterations))
    except (ValueError, TypeError) as exc:
        raise UnsupportedHash("Malformed pbkdf2 hash in the mirror.") from exc

    # compare_digest, not ==. A timing difference on a 4-digit secret is a
    # smaller problem than on a password, but it is free to avoid.
    return hmac.compare_digest(base64.b64encode(derived).decode(), expected)
