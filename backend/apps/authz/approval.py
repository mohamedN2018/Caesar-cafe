"""
Step-up approval tokens (docs/05).

When a cashier lacks a permission, the Desktop does not dead-end them: a manager
types their PIN and the server issues a token authorizing exactly one action.

Properties that make this safe rather than a hole:
  * names ONE permission and ONE target — cannot approve a second refund
  * short TTL (settings: security.approval_token_seconds), single use
  * burned server-side on redemption
  * BOTH identities are recorded — the cashier performed it, the manager
    authorized it, and neither can later disclaim it
  * the cashier is never logged out, so the queue keeps moving. Systems that
    force a full logout get defeated by managers sharing their PIN, which
    destroys accountability entirely.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from django.core import signing
from django.core.cache import cache

logger = logging.getLogger(__name__)

SALT = "caesar.stepup.approval"
BURN_PREFIX = "authz:approval:used"


@dataclass(frozen=True)
class Approval:
    jti: str
    permission: str
    target: str | None
    approver_id: str
    actor_id: str | None
    amount: str | None


def issue_approval_token(
    *,
    permission: str,
    approver_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
    target: str | None = None,
    amount: str | None = None,
    ttl_seconds: int = 60,
) -> tuple[str, int]:
    """Returns (token, ttl_seconds). The caller must have verified the approver."""
    jti = uuid.uuid4().hex
    payload: dict[str, Any] = {
        "jti": jti,
        "permission": permission,
        "target": target,
        "approver_id": str(approver_id),
        "actor_id": str(actor_id) if actor_id else None,
        "amount": amount,
    }
    token = signing.dumps(payload, salt=SALT, compress=True)

    # Reserve the jti for the token's whole life so a replay finds it burned.
    cache.set(f"{BURN_PREFIX}:{jti}", "issued", ttl_seconds + 10)
    return token, ttl_seconds


def consume_approval_token(
    token: str, *, permission: str, target: str | None = None, max_age: int = 60
) -> Approval | None:
    """
    Validate and burn a token. Returns None for anything not exactly right.

    Deliberately silent about *why* it failed — an attacker probing tokens
    should learn nothing from the difference between "expired" and "wrong
    permission".
    """
    try:
        payload = signing.loads(token, salt=SALT, max_age=max_age)
    except signing.BadSignature:
        logger.warning("Rejected approval token: bad signature or expired")
        return None

    if payload.get("permission") != permission:
        logger.warning(
            "Rejected approval token: permission mismatch",
            extra={"expected": permission, "got": payload.get("permission")},
        )
        return None

    # A token issued for a specific object may only be used on that object.
    token_target = payload.get("target")
    if token_target is not None and target is not None and token_target != target:
        logger.warning("Rejected approval token: target mismatch")
        return None

    jti = payload.get("jti")
    burn_key = f"{BURN_PREFIX}:{jti}"
    if cache.get(burn_key) != "issued":
        logger.warning("Rejected approval token: already used or unknown", extra={"jti": jti})
        return None

    cache.set(burn_key, "used", 300)  # tombstone, so a replay is detectable

    return Approval(
        jti=jti,
        permission=payload["permission"],
        target=token_target,
        approver_id=payload["approver_id"],
        actor_id=payload.get("actor_id"),
        amount=payload.get("amount"),
    )
