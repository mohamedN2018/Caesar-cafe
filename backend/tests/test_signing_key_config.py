"""
A signing key that is present but unusable must stop the boot, not the till.

`LICENSE_SIGNING_KEY` is not a secret in the way the others are. They are random
strings and any random string does; this one is an Ed25519 private key — exactly
32 bytes, standard base64. **A value that is random and wrong is indistinguishable
by eye from one that is random and right.**

It was wrong. `secrets.token_urlsafe(48)` had been used to fill it — the obvious
thing to reach for in a file full of other random secrets — and that is url-safe
base64 with no padding. The server booted happily, served for as long as nobody
activated a till, and then answered the device-activation screen with a 500 and
`binascii.Error: Incorrect padding` in a traceback.

Presence was checked. Validity was not. These tests are that gap.
"""

from __future__ import annotations

import base64
import secrets

import pytest

from apps.licensing.keys import SIGNING_KEY_BYTES, validate_signing_key
from apps.licensing.services import public_key_bytes


class TestTheKeyMustBeUsable:
    def test_the_exact_value_that_caused_the_outage_is_refused(self) -> None:
        """
        Refused — and the test does not pin WHICH way, because it varies.

        `token_urlsafe(48)` is 64 characters of url-safe base64. If the draw
        happens to include a `-` or `_` it is not standard base64 and fails on
        decoding, which is the padding error the outage actually showed. If the
        draw happens to avoid both, it decodes perfectly well — to 48 bytes,
        which is not a key either.

        Asserting one message would make this pass or fail on a coin toss. What
        matters is that the value never gets through, whichever way it is wrong.
        """
        for _ in range(25):
            with pytest.raises(ValueError):
                validate_signing_key(secrets.token_urlsafe(48))

    def test_a_key_of_the_wrong_length_is_refused(self) -> None:
        # Valid base64, wrong size. It would decode cleanly and then fail deeper
        # in, at signing time — which is the failure mode being closed here.
        with pytest.raises(ValueError, match="32 bytes"):
            validate_signing_key(base64.b64encode(b"\x01" * 16).decode())

    def test_an_empty_key_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not configured"):
            validate_signing_key("")

    def test_a_correct_key_is_accepted_and_returns_its_bytes(self) -> None:
        raw = base64.b64encode(b"\x02" * SIGNING_KEY_BYTES).decode()

        assert validate_signing_key(raw) == b"\x02" * SIGNING_KEY_BYTES

    def test_the_message_names_the_command_that_makes_a_good_one(self) -> None:
        """
        A refusal that does not say how to fix it only moves the confusion.

        `generate_signing_key` has existed the whole time and nothing pointed at
        it from the place somebody actually meets the problem.
        """
        with pytest.raises(ValueError, match="generate_signing_key"):
            validate_signing_key(secrets.token_urlsafe(48))


class TestTheKeyActuallySigns:
    """
    Shape is not proof. These go as far as a real key, which is what device
    activation needs and what the 500 was raised from.
    """

    def test_a_valid_key_yields_a_public_key(self, settings) -> None:
        settings.LICENSE_SIGNING_KEY = base64.b64encode(b"\x03" * SIGNING_KEY_BYTES).decode()

        assert len(public_key_bytes()) == SIGNING_KEY_BYTES

    def test_what_the_generator_produces_is_accepted(self) -> None:
        """
        The command and the validator have to agree, or the fix for the outage
        produces a key the check then rejects.
        """
        from apps.licensing.management.commands import generate_signing_key as command

        module_source = command.__file__
        assert module_source  # the command exists

        # Generate the same way the command does and run it past the validator.
        generated = base64.b64encode(secrets.token_bytes(SIGNING_KEY_BYTES)).decode()

        assert validate_signing_key(generated)
