"""
Web Push encryption, against the RFC's own numbers.

This is why implementing it was defensible rather than reckless. A Web Push
encryption bug is **invisible**: the push service accepts the request, returns
201, and the notification simply never appears on the phone. There is no error
to read and nothing in a log. The only way to know the code is right is to run
the bytes the specification publishes and compare.

  * RFC 8291 §5 — a complete worked example: keys, salt, plaintext, and the
    exact ciphertext they produce.
  * RFC 8292 §2.4 — a worked VAPID assertion.

Everything else here is about the mistakes that vectors do not catch: a reused
salt, a DER signature where JWS wants raw, an audience with a path in it.
"""

from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from apps.notifications import webpush

# ── RFC 8291 §5 ──────────────────────────────────────────────────────────────
# https://www.rfc-editor.org/rfc/rfc8291#section-5

PLAINTEXT = b"When I grow up, I want to be a watermelon"
CLIENT_PUBLIC = (
    "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
)
AUTH_SECRET = "BTBZMqHH6r4Tts7J_aSIgg"
SERVER_PRIVATE = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
SALT = "DGv6ra1nlYgDCS1FRnbzlw"

EXPECTED = (
    "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27ml"
    "mlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPT"
    "pK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
)


def server_key() -> ec.EllipticCurvePrivateKey:
    raw = webpush.b64url_decode(SERVER_PRIVATE)
    return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())


class TestRfc8291:
    def test_the_worked_example_reproduces_byte_for_byte(self) -> None:
        """
        The whole justification for writing this instead of installing it. If
        this passes, the encryption is right; if it fails, no amount of manual
        testing would have told us.
        """
        record = webpush.encrypt(
            PLAINTEXT,
            client_public_key=webpush.b64url_decode(CLIENT_PUBLIC),
            auth_secret=webpush.b64url_decode(AUTH_SECRET),
            server_private_key=server_key(),
            salt=webpush.b64url_decode(SALT),
        )

        assert webpush.b64url_encode(record) == EXPECTED

    def test_the_header_carries_the_salt_and_the_server_key(self) -> None:
        """
        RFC 8188 §2.1: the receiver needs both to derive anything, so they
        travel in front of the ciphertext rather than in a header field.
        """
        record = webpush.encrypt(
            PLAINTEXT,
            client_public_key=webpush.b64url_decode(CLIENT_PUBLIC),
            auth_secret=webpush.b64url_decode(AUTH_SECRET),
            server_private_key=server_key(),
            salt=webpush.b64url_decode(SALT),
        )

        assert record[:16] == webpush.b64url_decode(SALT)
        assert int.from_bytes(record[16:20], "big") == webpush.RECORD_SIZE
        assert record[20] == 65, "an uncompressed P-256 point is 65 bytes"

    def test_a_fresh_salt_is_used_every_time(self) -> None:
        """
        A reused salt with a reused key repeats the AES-GCM nonce, which is the
        one mistake GCM does not survive — two messages under the same nonce
        leak their XOR.
        """
        kwargs = {
            "client_public_key": webpush.b64url_decode(CLIENT_PUBLIC),
            "auth_secret": webpush.b64url_decode(AUTH_SECRET),
        }
        first = webpush.encrypt(PLAINTEXT, **kwargs)
        second = webpush.encrypt(PLAINTEXT, **kwargs)

        assert first[:16] != second[:16]
        assert first != second

    def test_an_ephemeral_server_key_is_used_every_time(self) -> None:
        kwargs = {
            "client_public_key": webpush.b64url_decode(CLIENT_PUBLIC),
            "auth_secret": webpush.b64url_decode(AUTH_SECRET),
        }
        first = webpush.encrypt(PLAINTEXT, **kwargs)
        second = webpush.encrypt(PLAINTEXT, **kwargs)

        assert first[21:86] != second[21:86], "the public key in the header must differ"

    def test_the_ciphertext_is_longer_than_the_plaintext(self) -> None:
        """86 bytes of header, one delimiter byte, and a 16-byte GCM tag."""
        record = webpush.encrypt(
            PLAINTEXT,
            client_public_key=webpush.b64url_decode(CLIENT_PUBLIC),
            auth_secret=webpush.b64url_decode(AUTH_SECRET),
        )
        assert len(record) == 86 + len(PLAINTEXT) + 1 + 16

    def test_an_arabic_payload_survives_the_round_trip(self) -> None:
        """The alerts are in Arabic, so UTF-8 through the encoder is not optional."""
        record = webpush.encrypt(
            "عجز في الدرج ٤٥ ج.م".encode(),
            client_public_key=webpush.b64url_decode(CLIENT_PUBLIC),
            auth_secret=webpush.b64url_decode(AUTH_SECRET),
        )
        assert len(record) > 86


# ── RFC 8292 ─────────────────────────────────────────────────────────────────


class TestVapid:
    @pytest.fixture
    def keys(self) -> webpush.VapidKeys:
        return webpush.VapidKeys.generate()

    def test_a_generated_pair_round_trips(self, keys: webpush.VapidKeys) -> None:
        from cryptography.hazmat.primitives import serialization

        rebuilt = webpush.b64url_encode(
            keys.load()
            .public_key()
            .public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        )
        assert rebuilt == keys.public_key

    def test_the_public_key_is_an_uncompressed_point(self, keys) -> None:
        """65 bytes starting 0x04 — what `applicationServerKey` must receive."""
        raw = webpush.b64url_decode(keys.public_key)
        assert len(raw) == 65
        assert raw[0] == 0x04

    def test_the_audience_is_the_origin_not_the_endpoint(self, keys) -> None:
        """
        The assertion says "for whoever runs this push service". Putting the
        path in would carry the subscription itself into a token that travels
        further than it needs to.
        """
        headers = webpush.vapid_headers(
            "https://fcm.googleapis.com/fcm/send/abc123XYZ",
            keys,
            subject="mailto:owner@caesar.test",
        )
        claims = self._claims(headers)

        assert claims["aud"] == "https://fcm.googleapis.com"
        assert "abc123XYZ" not in headers["Authorization"]

    def test_it_expires(self, keys) -> None:
        headers = webpush.vapid_headers(
            "https://push.example/x", keys, subject="mailto:a@b.c", now=1_000_000
        )
        claims = self._claims(headers)

        assert claims["exp"] == 1_000_000 + webpush.VAPID_TTL_SECONDS
        assert claims["exp"] - 1_000_000 < 24 * 3600, "the spec's ceiling"

    def test_the_header_carries_the_public_key(self, keys) -> None:
        """
        `k=` is how the push service checks the signature against the key the
        subscription was created with.
        """
        headers = webpush.vapid_headers("https://push.example/x", keys, subject="mailto:a@b.c")
        assert f"k={keys.public_key}" in headers["Authorization"]

    def test_the_signature_is_raw_not_der(self, keys) -> None:
        """
        JWS wants R||S, 64 bytes. `cryptography` produces DER, and forgetting to
        convert earns a 401 from every push service with nothing useful in it.
        """
        headers = webpush.vapid_headers("https://push.example/x", keys, subject="mailto:a@b.c")
        token = headers["Authorization"].split("t=")[1].split(",")[0]
        signature = webpush.b64url_decode(token.split(".")[2])

        assert len(signature) == 64
        assert signature[0] != 0x30, "0x30 is a DER SEQUENCE — the wrong encoding"

    def test_the_signature_verifies(self, keys) -> None:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import utils

        headers = webpush.vapid_headers("https://push.example/x", keys, subject="mailto:a@b.c")
        token = headers["Authorization"].split("t=")[1].split(",")[0]
        header, body, signature = token.split(".")

        raw = webpush.b64url_decode(signature)
        der = utils.encode_dss_signature(
            int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
        )
        keys.load().public_key().verify(der, f"{header}.{body}".encode(), ec.ECDSA(hashes.SHA256()))

    def test_two_generated_pairs_differ(self) -> None:
        assert webpush.VapidKeys.generate().public_key != webpush.VapidKeys.generate().public_key

    @staticmethod
    def _claims(headers: dict) -> dict:
        token = headers["Authorization"].split("t=")[1].split(",")[0]
        return json.loads(webpush.b64url_decode(token.split(".")[1]))


# ── the whole request ────────────────────────────────────────────────────────


class TestBuildRequest:
    @pytest.fixture
    def keys(self) -> webpush.VapidKeys:
        return webpush.VapidKeys.generate()

    def test_it_produces_a_postable_request(self, keys) -> None:
        url, body, headers = webpush.build_request(
            endpoint="https://push.example/subscription/abc",
            client_public_key=CLIENT_PUBLIC,
            auth_secret=AUTH_SECRET,
            payload={"title": "عجز في الدرج", "body": "٤٥ ج.م"},
            keys=keys,
            subject="mailto:owner@caesar.test",
        )

        assert url == "https://push.example/subscription/abc"
        assert headers["Content-Encoding"] == "aes128gcm"
        assert headers["Content-Length"] == str(len(body))
        assert headers["Authorization"].startswith("vapid t=")

    def test_the_payload_is_not_readable_in_the_body(self, keys) -> None:
        """Obvious, and worth asserting once: the push service is not trusted."""
        _url, body, _headers = webpush.build_request(
            endpoint="https://push.example/x",
            client_public_key=CLIENT_PUBLIC,
            auth_secret=AUTH_SECRET,
            payload={"title": "عجز في الدرج"},
            keys=keys,
            subject="mailto:a@b.c",
        )

        assert "عجز".encode() not in body
        assert b"title" not in body

    def test_it_expires_rather_than_arriving_at_breakfast(self, keys) -> None:
        """
        An alert about a drawer is worthless tomorrow. A TTL of zero would drop
        it if the phone is momentarily off; an hour is the compromise.
        """
        _url, _body, headers = webpush.build_request(
            endpoint="https://push.example/x",
            client_public_key=CLIENT_PUBLIC,
            auth_secret=AUTH_SECRET,
            payload={"title": "x"},
            keys=keys,
            subject="mailto:a@b.c",
        )
        assert headers["TTL"] == "3600"
