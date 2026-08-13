"""
Web Push, implemented rather than depended on.

Two RFCs, both small enough to write and — this is the part that matters — both
published with complete worked test vectors:

  * **RFC 8291** — Message Encryption for Web Push. ECDH to a key the browser
    generated, HKDF to derive a content key, AES-128-GCM to encrypt, all wrapped
    in the `aes128gcm` content coding of RFC 8188.
  * **RFC 8292** — VAPID. A signed JWT that identifies this server to the push
    service, so a stolen endpoint cannot be used by somebody else to spam a
    customer's phone.

Same reasoning as `apps/accounts/totp.py`: a dependency here would be four
packages deep (`pywebpush` → `http-ece` → `py-vapid` → …) to do about a hundred
lines of standard primitives that `cryptography` already provides. And an
encryption bug is invisible — the push service returns 201 and the notification
simply never appears — so being able to run the RFC's own vectors against this
code is worth considerably more than the lines it saves.

`tests/test_webpush.py` does exactly that: §5 of RFC 8291 and §2.4 of RFC 8292,
byte for byte.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import time
from dataclasses import dataclass
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

#: RFC 8188 §2 — the record size we advertise. One record is plenty: a
#: notification that does not fit in 4 kB is a notification nobody reads.
RECORD_SIZE = 4096

#: RFC 8291 §3.3 — the fixed strings that bind each derivation to its purpose.
KEY_INFO = b"WebPush: info\x00"
CEK_INFO = b"Content-Encoding: aes128gcm\x00"
NONCE_INFO = b"Content-Encoding: nonce\x00"

#: How long a VAPID assertion is good for. Twelve hours, well inside the 24 the
#: spec allows: a clock an hour out should not lock a cafe out of its alerts.
VAPID_TTL_SECONDS = 12 * 60 * 60


class PushError(Exception):
    """Anything that stops a notification being built or delivered."""


class SubscriptionGone(PushError):
    """
    The push service says this endpoint is dead (404/410).

    Not a failure to retry. The browser was uninstalled, the user revoked
    permission, or the subscription expired — and the only correct response is
    to delete the row, which is what the caller does.
    """


def b64url_decode(value: str) -> bytes:
    """Base64url without padding, which is how every Web Push field arrives."""
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


# ── RFC 8291: message encryption ─────────────────────────────────────────────


def _public_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    """Uncompressed P-256 point — the 65-byte form every Web Push field uses."""
    return key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def encrypt(
    payload: bytes,
    *,
    client_public_key: bytes,
    auth_secret: bytes,
    server_private_key: ec.EllipticCurvePrivateKey | None = None,
    salt: bytes | None = None,
) -> bytes:
    """
    One `aes128gcm` record, ready to POST.

    `server_private_key` and `salt` are parameters only so the RFC's test
    vectors can be reproduced exactly. In production both are generated here and
    used once — a reused salt with a reused key would repeat a nonce, which is
    the one mistake AES-GCM does not survive.
    """
    salt = salt or os.urandom(16)
    server_key = server_private_key or ec.generate_private_key(ec.SECP256R1())

    client_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), client_public_key)
    shared = server_key.exchange(ec.ECDH(), client_key)
    server_public = _public_bytes(server_key.public_key())

    # RFC 8291 §3.3: the auth secret salts a first HKDF whose info binds the two
    # public keys together, so a shared secret alone is not enough to decrypt.
    ikm = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=auth_secret,
        info=KEY_INFO + client_public_key + server_public,
    ).derive(shared)

    content_key = HKDF(algorithm=hashes.SHA256(), length=16, salt=salt, info=CEK_INFO).derive(ikm)
    nonce = HKDF(algorithm=hashes.SHA256(), length=12, salt=salt, info=NONCE_INFO).derive(ikm)

    # RFC 8188 §2: a single record is padded with 0x02 (the last-record
    # delimiter) rather than 0x01, because there is nothing after it.
    ciphertext = AESGCM(content_key).encrypt(nonce, payload + b"\x02", None)

    header = salt + struct.pack("!L", RECORD_SIZE) + bytes([len(server_public)]) + server_public
    return header + ciphertext


# ── RFC 8292: VAPID ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class VapidKeys:
    """
    The identity this server pushes under.

    `public_key` goes to the browser and ends up baked into every subscription
    it creates. **Rotating the private key invalidates every existing
    subscription**, because the push service checks the assertion against the
    key the subscription was made with — so the pair is generated once, kept in
    the environment, and never in Git (§62).
    """

    private_key: str
    public_key: str

    @classmethod
    def generate(cls) -> VapidKeys:
        key = ec.generate_private_key(ec.SECP256R1())
        private = key.private_numbers().private_value.to_bytes(32, "big")
        return cls(
            private_key=b64url_encode(private),
            public_key=b64url_encode(_public_bytes(key.public_key())),
        )

    def load(self) -> ec.EllipticCurvePrivateKey:
        return ec.derive_private_key(
            int.from_bytes(b64url_decode(self.private_key), "big"), ec.SECP256R1()
        )


def _sign_jwt(claims: dict, key: ec.EllipticCurvePrivateKey) -> str:
    """
    ES256, with the signature in the raw R||S form JWS wants.

    `cryptography` produces DER; converting is two lines and forgetting to is a
    signature every push service rejects with a 401 that says nothing useful.
    """
    header = b64url_encode(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
    body = b64url_encode(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{body}".encode()

    der = key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der)
    signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")

    return f"{header}.{body}.{b64url_encode(signature)}"


def vapid_headers(
    endpoint: str, keys: VapidKeys, *, subject: str, now: int | None = None
) -> dict[str, str]:
    """
    The `Authorization` header for one push, scoped to one push service.

    `aud` is the ORIGIN of the endpoint, not the endpoint itself: the assertion
    is for "whoever runs fcm.googleapis.com", and including the path would leak
    the subscription into a token that travels further than it needs to.

    `sub` must be a mailto: or https: the push service can use to contact the
    operator when something is wrong. A cafe's own address, not a placeholder —
    the one time it is used is the day deliveries start failing.
    """
    origin = urlparse(endpoint)
    now = now if now is not None else int(time.time())

    token = _sign_jwt(
        {
            "aud": f"{origin.scheme}://{origin.netloc}",
            "exp": now + VAPID_TTL_SECONDS,
            "sub": subject,
        },
        keys.load(),
    )
    return {"Authorization": f"vapid t={token}, k={keys.public_key}"}


# ── one delivery ─────────────────────────────────────────────────────────────


def build_request(
    *,
    endpoint: str,
    client_public_key: str,
    auth_secret: str,
    payload: dict,
    keys: VapidKeys,
    subject: str,
    ttl: int = 3600,
) -> tuple[str, bytes, dict[str, str]]:
    """
    (url, body, headers) for one notification.

    Separated from sending so the whole construction is testable without a
    network, and so the transport can be swapped without touching the crypto.
    """
    body = encrypt(
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        client_public_key=b64url_decode(client_public_key),
        auth_secret=b64url_decode(auth_secret),
    )

    headers = {
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "Content-Length": str(len(body)),
        # How long the push service holds it if the device is offline. An alert
        # about a drawer is worthless tomorrow, so it expires rather than
        # arriving at breakfast about last night.
        "TTL": str(ttl),
        "Urgency": "normal",
        **vapid_headers(endpoint, keys, subject=subject),
    }
    return endpoint, body, headers
