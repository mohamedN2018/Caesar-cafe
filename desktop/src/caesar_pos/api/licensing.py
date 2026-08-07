"""Activation, device tokens and the heartbeat."""

from __future__ import annotations

import logging
import platform
from dataclasses import dataclass
from typing import Any

from ..config import APP_VERSION
from ..vendored import keys
from .client import ApiClient, ApiError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActivationResult:
    device_id: str
    device_secret: str
    offline_token: str
    branch_id: str
    branch_name: str
    device_name: str
    mode: str


def device_fingerprint() -> str:
    """
    Advisory telemetry ONLY — never sent as proof of anything.

    The server records it to spot a credential copied between machines. It is
    explicitly not an authentication factor (commitment C4): it is spoofable,
    it changes on a Windows update, and the client computes it.
    """
    parts = [platform.node(), platform.machine(), platform.system()]
    return "|".join(p for p in parts if p)[:128]


def activate(
    client: ApiClient,
    *,
    license_key: str,
    email: str,
    device_name: str,
    mode: str = "POS",
) -> ActivationResult:
    """
    Exchange a licence key for a device credential.

    The key is normalized locally first, so an obvious typo is corrected before
    it costs the user one of five activation attempts per hour.
    """
    try:
        canonical = keys.normalize(license_key)
    except ValueError as exc:
        raise ApiError(
            "LICENSE_KEY_MALFORMED",
            "صيغة مفتاح الترخيص غير صحيحة. المفتاح مكوّن من ١٦ حرفاً ورقماً.",
        ) from exc

    data = client.post(
        "/licensing/activate/",
        {
            "license_key": canonical,
            "email": email.strip(),
            "device_name": device_name.strip(),
            "mode": mode,
            "platform": f"{platform.system()} {platform.release()}",
            "app_version": APP_VERSION,
            "fingerprint": device_fingerprint(),
        },
        authenticated=False,
    )

    return ActivationResult(
        device_id=data["device_id"],
        device_secret=data["device_secret"],
        offline_token=data["offline_token"],
        branch_id=data["branch_id"],
        branch_name=data["branch_name"],
        device_name=data["device_name"],
        mode=data["mode"],
    )


def obtain_device_token(client: ApiClient, *, device_id: str, device_secret: str) -> dict[str, Any]:
    """Exchange the long-lived device secret for a short-lived access token."""
    return client.post(
        "/licensing/device-token/",
        {"device_id": device_id, "device_secret": device_secret},
        authenticated=False,
    )


def heartbeat(client: ApiClient, *, pending_operations: int = 0) -> dict[str, Any]:
    """
    Report liveness and collect a fresh offline token.

    Each successful call slides the offline grace window forward, so a terminal
    that is online daily never encounters the mechanism.
    """
    return client.post(
        "/licensing/heartbeat/",
        {"app_version": APP_VERSION, "pending_operations": pending_operations},
    )


def allocate_invoice_block(client: ApiClient) -> dict[str, Any]:
    """Reserve the next disjoint invoice-number range for this device (C9)."""
    return client.post("/licensing/invoice-blocks/")
