"""
License key generation and normalization.

Format: QSR-XXXX-XXXX-XXXX-XXXX — 16 Crockford Base32 characters, 80 bits.

Design choices, each for a specific reason (docs/06):

  * `secrets`, never `random`. Python's `random` is a Mersenne Twister:
    observing a few outputs lets you predict all the rest. That is the single
    most common way a homegrown key generator is broken.
  * 80 bits ≈ 1.2e24 keyspace. Brute force against a rate-limited endpoint is
    not a threat worth further thought.
  * Crockford Base32 omits I, L, O and U — the characters people misread from a
    phone photo or a WhatsApp message, which is how these keys actually travel.
    It also treats 0/O and 1/I/L as equivalent on input, so a mistyped key still
    activates.
  * Grouped in fours, because that is how humans transcribe long strings
    without losing their place.
  * No sequence, timestamp or customer id is encoded. Any structure is a
    foothold for a keygen.

This module is deliberately Django-free so it can be exercised in isolation.
"""

from __future__ import annotations

import re
import secrets

PREFIX = "QSR"
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford: no I, L, O, U
GROUP_SIZE = 4
GROUP_COUNT = 4
KEY_LENGTH = GROUP_SIZE * GROUP_COUNT  # 16 chars = 80 bits
ENTROPY_BITS = KEY_LENGTH * 5

#: Crockford's documented input equivalences.
_CONFUSABLES = str.maketrans({"O": "0", "o": "0", "I": "1", "i": "1", "L": "1", "l": "1"})

_VALID = re.compile(rf"^{PREFIX}(-[{ALPHABET}]{{{GROUP_SIZE}}}){{{GROUP_COUNT}}}$")


def generate() -> str:
    """A fresh license key. Cryptographically random, never sequential."""
    raw = secrets.randbits(ENTROPY_BITS)
    chars = "".join(ALPHABET[(raw >> (5 * i)) & 0x1F] for i in range(KEY_LENGTH))
    groups = [chars[i : i + GROUP_SIZE] for i in range(0, KEY_LENGTH, GROUP_SIZE)]
    return f"{PREFIX}-" + "-".join(groups)


def fold_input(raw: str) -> str:
    """
    Reduce typed input to bare alphabet characters.

    Upper-cases, drops separators and the prefix, and folds the Crockford
    confusables (O→0, I/L→1). Public because the Desktop's key field calls it on
    every keystroke: the client and the server must agree on what a typed key
    *means*, not just on the final comparison.

    Does NOT validate length — that is `normalize`'s job.
    """
    cleaned = raw.strip().upper().translate(_CONFUSABLES)
    cleaned = re.sub(r"[\s\-_]", "", cleaned)
    if cleaned.startswith(PREFIX):
        cleaned = cleaned[len(PREFIX) :]
    return cleaned


def normalize(raw: str) -> str:
    """
    Canonicalize user input so a reasonable typo still activates.

    Accepts lower case, missing or extra dashes, surrounding whitespace, a
    missing prefix, and the Crockford confusables. Returns the canonical form;
    raises ValueError if it cannot be one.
    """
    if not raw:
        raise ValueError("empty key")

    cleaned = fold_input(raw)

    if len(cleaned) != KEY_LENGTH:
        raise ValueError(f"expected {KEY_LENGTH} characters, got {len(cleaned)}")
    if any(c not in ALPHABET for c in cleaned):
        raise ValueError("contains characters outside the Crockford alphabet")

    groups = [cleaned[i : i + GROUP_SIZE] for i in range(0, KEY_LENGTH, GROUP_SIZE)]
    return f"{PREFIX}-" + "-".join(groups)


def is_well_formed(candidate: str) -> bool:
    return bool(_VALID.match(candidate))


def mask(key: str) -> str:
    """`QSR-7X29-••••-••••-3F1A` — what an admin sees after creation."""
    canonical = normalize(key)
    groups = canonical.split("-")
    return "-".join([groups[0], groups[1], "••••", "••••", groups[4]])


def prefix_of(key: str) -> str:
    """`QSR-7X29` — enough to identify a key in a list without revealing it."""
    canonical = normalize(key)
    return "-".join(canonical.split("-")[:2])


def last4_of(key: str) -> str:
    return normalize(key).split("-")[-1]
