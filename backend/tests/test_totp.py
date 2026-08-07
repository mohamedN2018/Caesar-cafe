"""
TOTP verified against the RFC 6238 test vectors.

This is why the algorithm was implemented rather than imported: the RFC ships
official vectors, so the implementation is checked against the specification
itself instead of against its own behaviour.
"""

from __future__ import annotations

import base64
import hashlib

import pytest

from apps.accounts import totp

# RFC 6238 Appendix B uses the ASCII seed "12345678901234567890" for SHA-1.
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()

# (unix time, expected 8-digit TOTP) — SHA-1 rows from the RFC table.
RFC_VECTORS = [
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
]


@pytest.mark.parametrize(("timestamp", "expected"), RFC_VECTORS)
def test_matches_rfc6238_vectors(timestamp: int, expected: str) -> None:
    assert totp.totp(RFC_SECRET, at=timestamp, digits=8, algorithm=hashlib.sha1) == expected


class TestVerify:
    def test_accepts_the_current_code(self) -> None:
        secret = totp.generate_secret()
        code = totp.totp(secret, at=1_700_000_000)
        assert totp.verify(secret, code, at=1_700_000_000)

    @pytest.mark.parametrize("drift", [-30, 30])
    def test_accepts_one_step_of_clock_drift(self, drift: int) -> None:
        """A user typing a code as it rolls over should not be told they're wrong."""
        secret = totp.generate_secret()
        code = totp.totp(secret, at=1_700_000_000 + drift)
        assert totp.verify(secret, code, at=1_700_000_000)

    @pytest.mark.parametrize("drift", [-90, 90, 3600])
    def test_rejects_codes_outside_the_window(self, drift: int) -> None:
        secret = totp.generate_secret()
        code = totp.totp(secret, at=1_700_000_000 + drift)
        assert not totp.verify(secret, code, at=1_700_000_000)

    def test_rejects_another_secrets_code(self) -> None:
        a, b = totp.generate_secret(), totp.generate_secret()
        assert not totp.verify(a, totp.totp(b, at=1_700_000_000), at=1_700_000_000)

    @pytest.mark.parametrize("bad", ["", "12345", "1234567", "abcdef", "12 34 56", None])
    def test_rejects_malformed_input(self, bad) -> None:
        secret = totp.generate_secret()
        assert not totp.verify(secret, bad or "", at=1_700_000_000)

    def test_rejects_empty_secret(self) -> None:
        assert not totp.verify("", "123456")


class TestSecretGeneration:
    def test_secrets_are_unique(self) -> None:
        secrets_seen = {totp.generate_secret() for _ in range(50)}
        assert len(secrets_seen) == 50

    def test_secret_is_base32_and_160_bits(self) -> None:
        secret = totp.generate_secret()
        decoded = base64.b32decode(secret + "=" * (-len(secret) % 8))
        assert len(decoded) == 20  # RFC 4226 recommendation

    def test_provisioning_uri_is_well_formed(self) -> None:
        uri = totp.provisioning_uri("ABCDEF", account="ahmed@caesar.test")
        assert uri.startswith("otpauth://totp/")
        assert "secret=ABCDEF" in uri
        assert "issuer=Caesar%20Cafe" in uri

    def test_recovery_codes_are_unique_and_formatted(self) -> None:
        codes = totp.generate_recovery_codes(8)
        assert len(set(codes)) == 8
        assert all(len(c.split("-")) == 3 for c in codes)
