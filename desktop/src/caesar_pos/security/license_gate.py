"""
The startup gate: may this terminal open, and what may it do?

This is the client half of commitment C5. It decides using ONLY a
cryptographically signed token — never a plain flag in a file — so the check
cannot be defeated by editing local state. Deleting the state file means "not
activated"; it can never mean "valid forever".

Read `docs/06` for what this does and does not guarantee. In short: a determined
attacker with the .exe and a debugger can patch this out. That is true of all
client-side licensing. The answer is that the patched client is worthless — it
has no catalog, no prices, no sync and no invoice numbering, because those live
on the server.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from ..config import LICENSE_PUBLIC_KEY_B64
from ..vendored import offline_token as ot

logger = logging.getLogger(__name__)


class GateDecision(StrEnum):
    NOT_ACTIVATED = "NOT_ACTIVATED"
    """No credential on this machine. Show the activation screen."""

    ALLOWED = "ALLOWED"
    RESTRICTED = "RESTRICTED"
    """Open, but new orders are blocked. Open tables can still be settled."""

    BLOCKED = "BLOCKED"
    """Revoked, tampered, or the offline window has elapsed."""


@dataclass(frozen=True)
class GateResult:
    decision: GateDecision
    can_open_new_orders: bool
    can_close_open_orders: bool
    message_ar: str = ""
    reason_code: str = ""
    payload: dict | None = None

    @property
    def may_start(self) -> bool:
        return self.decision in (GateDecision.ALLOWED, GateDecision.RESTRICTED)


def public_key() -> bytes:
    return base64.b64decode(LICENSE_PUBLIC_KEY_B64)


# ── persisted state ──────────────────────────────────────────────────────────


@dataclass
class LicenseState:
    token: str | None = None
    ratchet: ot.RatchetState = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.ratchet is None:
            self.ratchet = ot.RatchetState()

    def to_json(self) -> str:
        return json.dumps(
            {"token": self.token, "ratchet": self.ratchet.as_dict()},
            ensure_ascii=False,
            indent=2,
        )

    @classmethod
    def from_json(cls, raw: str) -> LicenseState:
        data = json.loads(raw)
        return cls(
            token=data.get("token"),
            ratchet=ot.RatchetState.from_dict(data.get("ratchet")),
        )


def load_state(path: Path) -> LicenseState:
    if not path.exists():
        return LicenseState()
    try:
        return LicenseState.from_json(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError):
        # A corrupt file must fail CLOSED — treat it as "no token", which sends
        # the user to activation rather than granting anything.
        logger.warning("Licence state file is unreadable; treating as not activated")
        return LicenseState()


def save_state(path: Path, state: LicenseState) -> None:
    path.write_text(state.to_json(), encoding="utf-8")


# ── the gate ─────────────────────────────────────────────────────────────────


def evaluate(
    state: LicenseState,
    *,
    is_activated: bool,
    now: datetime | None = None,
) -> tuple[GateResult, LicenseState]:
    """
    Decide whether the application may start, and return the advanced state.

    The caller MUST persist the returned state — that is what moves the clock
    ratchet forward.
    """
    now = now or datetime.now(UTC)

    if not is_activated:
        return (
            GateResult(
                decision=GateDecision.NOT_ACTIVATED,
                can_open_new_orders=False,
                can_close_open_orders=False,
                reason_code="NOT_ACTIVATED",
            ),
            state,
        )

    if not state.token:
        # Activated but no token: the credential exists and the server can
        # re-issue one, so this is recoverable online rather than fatal.
        return (
            GateResult(
                decision=GateDecision.BLOCKED,
                can_open_new_orders=False,
                can_close_open_orders=False,
                message_ar="لا يوجد ترخيص محفوظ. برجاء الاتصال بالإنترنت لتحديث الترخيص.",
                reason_code="NO_TOKEN",
            ),
            state,
        )

    try:
        payload, advanced_ratchet = ot.accept(state.token, public_key(), state.ratchet, now=now)
    except ot.TokenSignatureInvalid:
        return _blocked(
            state,
            "ملف الترخيص غير صالح. برجاء إعادة تفعيل الجهاز.",
            "TOKEN_TAMPERED",
            log="Offline token failed signature verification — possible tampering",
        )
    except ot.TokenReplayed:
        return _blocked(
            state,
            "تم اكتشاف ترخيص قديم. برجاء الاتصال بالإنترنت.",
            "TOKEN_REPLAYED",
            log="Older licence token presented after a newer one",
        )
    except ot.ClockRolledBack:
        return _blocked(
            state,
            "ساعة الجهاز غير صحيحة. برجاء ضبط التاريخ والوقت ثم إعادة التشغيل.",
            "CLOCK_ROLLED_BACK",
            log="System clock is behind the last observed server time",
        )
    except ot.TokenExpired:
        return _blocked(
            state,
            "انتهت مهلة العمل بدون إنترنت. برجاء الاتصال بالإنترنت.",
            "OFFLINE_GRACE_ELAPSED",
            log="Offline grace window elapsed",
        )
    except ot.TokenError:
        return _blocked(
            state,
            "ملف الترخيص غير صالح. برجاء إعادة تفعيل الجهاز.",
            "TOKEN_MALFORMED",
            log="Offline token is malformed",
        )

    # The ratchet only moves forward once the signature has been verified, so a
    # forged token can never advance it.
    advanced = LicenseState(token=state.token, ratchet=advanced_ratchet)

    # Signature verified — only now may the payload influence anything.
    if payload.get("status") in {"REVOKED", "SUSPENDED"}:
        return (
            GateResult(
                decision=GateDecision.BLOCKED,
                can_open_new_orders=False,
                can_close_open_orders=bool(payload.get("status") == "SUSPENDED"),
                message_ar="تم إيقاف الترخيص. تواصل مع مدير النظام.",
                reason_code=str(payload.get("status")),
                payload=payload,
            ),
            advanced,
        )

    can_open = bool(payload.get("can_open_new_orders", False))
    stage = str(payload.get("stage", "ACTIVE"))

    return (
        GateResult(
            decision=GateDecision.ALLOWED if can_open else GateDecision.RESTRICTED,
            can_open_new_orders=can_open,
            # Always true for a valid token: a cafe with open tables must be able
            # to finish serving and settle them, whatever the licence stage.
            can_close_open_orders=True,
            message_ar=_stage_message(stage, payload),
            reason_code=stage,
            payload=payload,
        ),
        advanced,
    )


def _blocked(state, message: str, code: str, *, log: str) -> tuple[GateResult, LicenseState]:
    logger.error(log, extra={"reason_code": code})
    return (
        GateResult(
            decision=GateDecision.BLOCKED,
            can_open_new_orders=False,
            can_close_open_orders=False,
            message_ar=message,
            reason_code=code,
        ),
        state,
    )


def _stage_message(stage: str, payload: dict) -> str:
    if stage == "ACTIVE":
        return ""
    if stage in {"NOTICE", "WARNING"}:
        expires = payload.get("license_expires_at", "")
        return f"ينتهي الترخيص قريباً ({expires[:10]}). برجاء التجديد."
    if stage == "GRACE":
        return "انتهت صلاحية الترخيص. النظام يعمل مؤقتاً — برجاء التجديد."
    if stage == "RESTRICTED":
        return "انتهت صلاحية الترخيص. يمكن إنهاء الطلبات المفتوحة وتحصيلها فقط."
    return ""
