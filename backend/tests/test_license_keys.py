"""Key generation, normalization and hashing."""

from __future__ import annotations

import re

import pytest
from django.test import override_settings

from apps.licensing import keys, services

pytestmark = pytest.mark.django_db


class TestGeneration:
    def test_format(self) -> None:
        key = keys.generate()
        assert re.match(r"^QSR(-[0-9A-HJKMNP-TV-Z]{4}){4}$", key), key
        assert keys.is_well_formed(key)

    def test_uses_the_crockford_alphabet(self) -> None:
        """I, L, O and U are excluded — they are what people misread."""
        body = "".join(keys.generate().split("-")[1:])
        assert not set(body) & set("ILOU")

    def test_keys_are_unique(self) -> None:
        assert len({keys.generate() for _ in range(2000)}) == 2000

    def test_entropy_is_80_bits(self) -> None:
        assert keys.ENTROPY_BITS == 80

    def test_every_alphabet_character_can_appear(self) -> None:
        """A biased generator would shrink the real keyspace."""
        seen: set[str] = set()
        for _ in range(3000):
            seen |= set("".join(keys.generate().split("-")[1:]))
        assert seen == set(keys.ALPHABET)

    def test_no_sequential_structure(self) -> None:
        """Two consecutive keys must share no positional characters by pattern."""
        a = "".join(keys.generate().split("-")[1:])
        b = "".join(keys.generate().split("-")[1:])
        matching = sum(1 for x, y in zip(a, b, strict=True) if x == y)
        assert matching < 8, "suspiciously similar consecutive keys"


class TestNormalization:
    @pytest.mark.parametrize(
        "variant",
        [
            "QSR-7X29-K8P4-3F1A-9WYZ",
            "qsr-7x29-k8p4-3f1a-9wyz",
            "QSR7X29K8P43F1A9WYZ",
            "  QSR-7X29-K8P4-3F1A-9WYZ  ",
            "7X29-K8P4-3F1A-9WYZ",
            "QSR 7X29 K8P4 3F1A 9WYZ",
            "QSR_7X29_K8P4_3F1A_9WYZ",
        ],
    )
    def test_accepts_reasonable_variants(self, variant: str) -> None:
        assert keys.normalize(variant) == "QSR-7X29-K8P4-3F1A-9WYZ"

    @pytest.mark.parametrize(
        ("typed", "expected"),
        [
            ("QSR-7X29-K8P4-3FIA-9WYZ", "QSR-7X29-K8P4-3F1A-9WYZ"),  # I -> 1
            ("QSR-7X29-K8P4-3FLA-9WYZ", "QSR-7X29-K8P4-3F1A-9WYZ"),  # L -> 1
            ("QSR-7X29-K8P4-3F1A-9WYZ", "QSR-7X29-K8P4-3F1A-9WYZ"),
        ],
    )
    def test_crockford_confusables_still_activate(self, typed: str, expected: str) -> None:
        """A key read off a phone photo must still work."""
        assert keys.normalize(typed) == expected

    def test_letter_o_becomes_zero(self) -> None:
        assert keys.normalize("QSR-OX29-K8P4-3F1A-9WYZ") == "QSR-0X29-K8P4-3F1A-9WYZ"

    @pytest.mark.parametrize("bad", ["", "QSR-1234", "QSR-7X29-K8P4-3F1A-9WYZ-EXTRA", "!!!!"])
    def test_rejects_malformed(self, bad: str) -> None:
        with pytest.raises(ValueError):
            keys.normalize(bad)

    def test_round_trips_generated_keys(self) -> None:
        for _ in range(200):
            key = keys.generate()
            assert keys.normalize(key) == key


class TestDisplayHelpers:
    def test_mask_hides_the_middle(self) -> None:
        masked = keys.mask("QSR-7X29-K8P4-3F1A-9WYZ")
        assert masked == "QSR-7X29-••••-••••-9WYZ"
        assert "K8P4" not in masked
        assert "3F1A" not in masked

    def test_prefix_and_last4(self) -> None:
        key = "QSR-7X29-K8P4-3F1A-9WYZ"
        assert keys.prefix_of(key) == "QSR-7X29"
        assert keys.last4_of(key) == "9WYZ"


class TestHashing:
    def test_hash_is_deterministic(self) -> None:
        key = keys.generate()
        assert services.hash_key(key) == services.hash_key(key)

    def test_different_keys_hash_differently(self) -> None:
        assert services.hash_key(keys.generate()) != services.hash_key(keys.generate())

    def test_hash_is_sha256_hex(self) -> None:
        assert re.match(r"^[0-9a-f]{64}$", services.hash_key(keys.generate()))

    def test_the_pepper_changes_the_hash(self) -> None:
        """A stolen database alone must not yield working keys."""
        key = keys.generate()
        with override_settings(LICENSE_PEPPER="pepper-one"):
            first = services.hash_key(key)
        with override_settings(LICENSE_PEPPER="pepper-two"):
            second = services.hash_key(key)
        assert first != second

    def test_refuses_to_hash_without_a_pepper(self) -> None:
        with override_settings(LICENSE_PEPPER=""), pytest.raises(RuntimeError, match="PEPPER"):
            services.hash_key("QSR-7X29-K8P4-3F1A-9WYZ")
