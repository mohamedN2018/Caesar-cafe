"""
Device credential storage.

The device secret goes into the Windows Credential Manager (via `keyring`,
which is DPAPI-backed and encrypted with the Windows user account's key) — NOT
into a JSON file beside the executable. Copying the app directory to another
machine therefore does not carry the credential with it.

This is the practical half of commitment C4: the credential is server-issued and
OS-protected, rather than derived from hardware the client can lie about.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import keyring
from keyring.errors import KeyringError

from ..config import KEYRING_DEVICE_ID, KEYRING_DEVICE_SECRET, KEYRING_SERVICE

logger = logging.getLogger(__name__)


class CredentialError(RuntimeError):
    """The OS credential store could not be reached."""


@dataclass(frozen=True)
class DeviceCredential:
    device_id: str
    device_secret: str


def load() -> DeviceCredential | None:
    """The stored credential, or None if this machine has never activated."""
    try:
        device_id = keyring.get_password(KEYRING_SERVICE, KEYRING_DEVICE_ID)
        device_secret = keyring.get_password(KEYRING_SERVICE, KEYRING_DEVICE_SECRET)
    except KeyringError as exc:
        raise CredentialError("تعذّر الوصول إلى مخزن بيانات الاعتماد في Windows.") from exc

    if not device_id or not device_secret:
        return None
    return DeviceCredential(device_id=device_id, device_secret=device_secret)


def store(credential: DeviceCredential) -> None:
    try:
        keyring.set_password(KEYRING_SERVICE, KEYRING_DEVICE_ID, credential.device_id)
        keyring.set_password(KEYRING_SERVICE, KEYRING_DEVICE_SECRET, credential.device_secret)
    except KeyringError as exc:
        raise CredentialError("تعذّر حفظ بيانات الجهاز في مخزن Windows.") from exc

    # Deliberately not logged, not even at debug level.
    logger.info("Device credential stored", extra={"device_id": credential.device_id})


def clear() -> None:
    """Forget this device. Used on revocation and on operator-initiated reset."""
    for key in (KEYRING_DEVICE_ID, KEYRING_DEVICE_SECRET):
        try:
            keyring.delete_password(KEYRING_SERVICE, key)
        except KeyringError:
            # Already absent is the desired end state, so this is not an error.
            logger.debug("Credential key already absent", extra={"key": key})


def is_activated() -> bool:
    try:
        return load() is not None
    except CredentialError:
        return False
