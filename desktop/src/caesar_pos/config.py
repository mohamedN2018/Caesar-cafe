"""
Desktop configuration and on-disk locations.

Nothing here is a business rule. Every operational value — tax, service model,
grace hours, sync intervals — arrives from the server (commitment C10). This
module only knows where files live and how to reach the API.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "CaesarPOS"
APP_VERSION = "0.1.0"

#: The Ed25519 public key matching the server's LICENSE_SIGNING_KEY.
#: Embedded in the binary; the PRIVATE half never leaves the server, which is
#: what makes an offline licence token unforgeable (commitment C5).
LICENSE_PUBLIC_KEY_B64 = os.environ.get(
    "CAESAR_LICENSE_PUBLIC_KEY", "UYhmleHFEw0aMDmRXJnw+gpJwLFyW7Dh/tLrAhj18hI="
)

KEYRING_SERVICE = "CaesarPOS"
KEYRING_DEVICE_ID = "device_id"
KEYRING_DEVICE_SECRET = "device_secret"  # noqa: S105 — a key name, not a secret


def data_dir() -> Path:
    """
    %LOCALAPPDATA%\\CaesarPOS on Windows; XDG-ish elsewhere for development.

    Overridable via CAESAR_DATA_DIR, which is what the tests use.
    """
    if override := os.environ.get("CAESAR_DATA_DIR"):
        path = Path(override)
    elif local_appdata := os.environ.get("LOCALAPPDATA"):
        path = Path(local_appdata) / APP_NAME
    else:
        path = Path.home() / ".local" / "share" / APP_NAME.lower()

    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def database(self) -> Path:
        return self.root / "local.db"

    @property
    def state_file(self) -> Path:
        """Offline token + ratchet. Not secret — it is signature-protected."""
        return self.root / "license_state.json"

    @property
    def settings_file(self) -> Path:
        return self.root / "settings.json"

    @property
    def log_file(self) -> Path:
        return self.root / "caesar-pos.log"


def paths() -> Paths:
    return Paths(root=data_dir())
