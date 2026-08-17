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


class TestResolvingTheSigningKey:
    """
    The server provisions its own signing key when the environment cannot.

    The deploy this covers: Dokploy's env panel held a stale, malformed
    `LICENSE_SIGNING_KEY`. The panel keeps its own copy of the env — no push can
    correct it — and the old refuse-on-invalid check turned that leftover string
    into a site that could not deploy at all. Refusing was the means; the end is
    that activation never fails on a key problem, and a provisioned, persisted
    key achieves the end directly.
    """

    VALID = "TXvXVjKcTBpr5PjEjPFqDpDLxWvBmFmXKDNjrmhbMQI="

    def test_a_valid_configured_key_wins(self, tmp_path) -> None:
        # Rotation and explicit control keep working; nothing is written.
        out = keys.resolve_signing_key(self.VALID, str(tmp_path))

        assert out == self.VALID
        assert not (tmp_path / "license_signing.key").exists()

    def test_nothing_configured_provisions_and_persists(self, tmp_path) -> None:
        first = keys.resolve_signing_key("", str(tmp_path))

        keys.validate_signing_key(first)  # 32 bytes, standard base64
        assert (tmp_path / "license_signing.key").read_text() == first

    def test_a_second_boot_reuses_the_same_key(self, tmp_path) -> None:
        # Stability is the point: outstanding offline tokens survive a restart.
        first = keys.resolve_signing_key("", str(tmp_path))
        second = keys.resolve_signing_key("", str(tmp_path))

        assert first == second

    def test_the_dokploy_case_an_invalid_value_boots_anyway(self, tmp_path, caplog) -> None:
        """The exact failure from the deploy log, now survivable."""
        import logging

        with caplog.at_level(logging.CRITICAL):
            out = keys.resolve_signing_key("not-valid-base64!!", str(tmp_path))

        keys.validate_signing_key(out)
        # Ignored LOUDLY — the one lost property is "I set a key and it was not
        # used", and the log states it in one line.
        assert any("IGNORED" in r.message for r in caplog.records)

    def test_an_invalid_value_and_a_persisted_key_use_the_persisted_one(self, tmp_path) -> None:
        persisted = keys.resolve_signing_key("", str(tmp_path))
        out = keys.resolve_signing_key("garbage!!", str(tmp_path))

        assert out == persisted

    def test_a_corrupt_persisted_file_refuses_with_the_remedy(self, tmp_path) -> None:
        # The only stable-key path left is a human deciding which key is real.
        (tmp_path / "license_signing.key").write_text("corrupt")

        with pytest.raises(ValueError, match="corrupt"):
            keys.resolve_signing_key("", str(tmp_path))

    def test_an_unwritable_directory_refuses_with_the_remedy(self, tmp_path) -> None:
        # A key that cannot persist would differ per process and die on restart —
        # worse than not starting, because it fails later and quietly.
        blocked = tmp_path / "file-not-dir"
        blocked.write_text("occupies the path")

        with pytest.raises(ValueError, match="cannot be written"):
            keys.resolve_signing_key("", str(blocked / "keys"))

    def test_losing_the_creation_race_adopts_the_winner_s_key(self, tmp_path, monkeypatch) -> None:
        """
        Four containers boot from the same settings against the same volume.
        Exactly one may create; the rest must ADOPT, or api and worker would sign
        with different keys until the next restart.
        """
        import os as os_module

        winner = "aX0ZrS+TFYu3ZNTVzS4PsGXq7AkvjbVnGeY8H0qmWiM="
        real_open = os_module.open

        def lose_the_race(path, flags, mode=0o777):
            # Between our existence check and our O_EXCL, the winner lands.
            if str(path).endswith("license_signing.key") and flags & os_module.O_EXCL:
                (tmp_path / "license_signing.key").write_text(winner)
            return real_open(path, flags, mode)

        monkeypatch.setattr("os.open", lose_the_race)

        out = keys.resolve_signing_key("", str(tmp_path))

        assert out == winner
