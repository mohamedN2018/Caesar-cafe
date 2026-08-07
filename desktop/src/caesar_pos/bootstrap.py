"""
Startup sequence, with no Qt in it.

Kept UI-free on purpose: the order of these checks is security-relevant, so it
is unit-tested directly rather than only through a window that is awkward to
drive. `app.py` renders whatever this decides.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

from .api.client import ApiClient, ApiError, NetworkUnavailable
from .api.licensing import heartbeat, obtain_device_token
from .config import paths
from .security import credentials
from .security.license_gate import GateDecision, GateResult, evaluate, load_state, save_state

logger = logging.getLogger(__name__)


class Screen(StrEnum):
    ACTIVATION = "ACTIVATION"
    LOGIN = "LOGIN"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class Startup:
    screen: Screen
    gate: GateResult
    online: bool
    access_token: str | None = None
    branch_id: str | None = None


def start(client: ApiClient, *, now=None) -> Startup:
    """
    Decide what to show, refreshing the licence from the server when reachable.

    The offline path is not a fallback bolted on the side — it is the same code
    path with the network step skipped, so it cannot rot from disuse.
    """
    store = paths().state_file
    state = load_state(store)
    credential = None

    try:
        credential = credentials.load()
    except credentials.CredentialError:
        logger.exception("Windows credential store unreachable")

    is_activated = credential is not None
    online = False
    access_token = None
    branch_id = None

    if is_activated:
        try:
            token_response = obtain_device_token(
                client,
                device_id=credential.device_id,
                device_secret=credential.device_secret,
            )
            access_token = token_response["access"]
            client.access_token = access_token
            online = True

            beat = heartbeat(client)
            state.token = beat["offline_token"]
            logger.info("Licence refreshed from server", extra={"stage": beat.get("stage")})

        except NetworkUnavailable:
            # Expected during an outage. Fall through to the cached token —
            # this is the whole point of the offline design.
            logger.info("Server unreachable; evaluating the cached licence token")

        except ApiError as exc:
            # The server answered and said no. That is authoritative and must
            # not be masked by falling back to a cached token.
            if exc.code in {"DEVICE_REVOKED", "LICENSE_REVOKED", "NOT_AUTHENTICATED"}:
                logger.error("Device rejected by server", extra={"code": exc.code})
                credentials.clear()
                state.token = None
                save_state(store, state)
                return Startup(
                    screen=Screen.ACTIVATION,
                    gate=GateResult(
                        decision=GateDecision.NOT_ACTIVATED,
                        can_open_new_orders=False,
                        can_close_open_orders=False,
                        message_ar=exc.message,
                        reason_code=exc.code,
                    ),
                    online=True,
                )
            logger.warning("Licence refresh failed", extra={"code": exc.code})

    gate, advanced = evaluate(state, is_activated=is_activated, now=now)
    state.ratchet = advanced.ratchet
    save_state(store, state)

    if gate.payload:
        branch_id = gate.payload.get("branch_id")

    if gate.decision is GateDecision.NOT_ACTIVATED:
        screen = Screen.ACTIVATION
    elif gate.may_start:
        screen = Screen.LOGIN
    else:
        screen = Screen.BLOCKED

    return Startup(
        screen=screen,
        gate=gate,
        online=online,
        access_token=access_token,
        branch_id=branch_id,
    )


def complete_activation(*, device_id: str, device_secret: str, offline_token: str) -> None:
    """Persist a successful activation: credential to the OS, token to disk."""
    credentials.store(
        credentials.DeviceCredential(device_id=device_id, device_secret=device_secret)
    )

    store = paths().state_file
    state = load_state(store)
    state.token = offline_token
    save_state(store, state)
    logger.info("Activation stored", extra={"device_id": device_id})
