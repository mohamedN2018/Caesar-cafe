"""
Who is logged in at this terminal, and what they may do.

A POS login is not a web login. The cashier changes every few hours, the screen
is shared, and the whole thing has to work with the internet down. So:

  * The identity comes from the mirrored staff list, verified against the
    mirrored PIN hash. No round-trip.
  * The permission set comes from the mirrored role assignments. It is the same
    set the server computed — this terminal does not derive permissions, it
    reads the ones it was sent.
  * **The server re-checks everything anyway.** A permission cached here is a UI
    decision; every operation that syncs is authorised again on the ordinary
    path. If the two ever disagree, the server wins and the operation lands in
    `sync_conflicts` where somebody sees it.

The lockout is what makes a 4-digit secret defensible (docs/09, S2 and accepted
risk AR4). Five wrong PINs lock the DEVICE for fifteen minutes — not the account,
because locking the account would let anyone with a keypad lock out the manager.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from ..local.db import Database
from .pin import DEFAULT_LOCKOUT_SECONDS, DEFAULT_MAX_ATTEMPTS, UnsupportedHash, verify

logger = logging.getLogger(__name__)


class PinRejected(Exception):
    """Wrong PIN, or no user has that PIN on this terminal."""


class DeviceLocked(Exception):
    def __init__(self, seconds_remaining: int) -> None:
        minutes = max(1, round(seconds_remaining / 60))
        super().__init__(f"الجهاز مقفل مؤقتاً — حاول بعد {minutes} دقيقة")
        self.seconds_remaining = seconds_remaining


@dataclass(frozen=True)
class Session:
    user_id: str
    full_name_ar: str
    permissions: frozenset[str]
    started_at: datetime
    roles: tuple[str, ...] = ()

    def can(self, code: str) -> bool:
        return code in self.permissions

    def can_any(self, *codes: str) -> bool:
        return any(self.can(code) for code in codes)


@dataclass
class Authenticator:
    """
    Holds the lockout counter for this terminal.

    In memory rather than on disk on purpose: a restart clearing the counter is
    an acceptable weakness against an attacker who can already power-cycle the
    machine, and a counter that survived a crash would strand a cashier at 7am
    with no way to clear it.
    """

    db: Database
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    lockout_seconds: int = DEFAULT_LOCKOUT_SECONDS

    _failures: int = 0
    _locked_until: datetime | None = None
    _session: Session | None = None
    _listeners: list = field(default_factory=list)

    # ── login ────────────────────────────────────────────────────────────────

    def login(self, pin: str, *, now: datetime | None = None) -> Session:
        now = now or datetime.now(UTC)
        self._assert_not_locked(now)

        user = self._match(pin)
        if user is None:
            self._record_failure(now)
            raise PinRejected("رمز الدخول غير صحيح")

        self._failures = 0
        self._locked_until = None

        session = Session(
            user_id=user["id"],
            full_name_ar=user["full_name_ar"] or user["email"] or "مستخدم",
            permissions=frozenset(self._permissions_for(user["id"])),
            roles=tuple(self._roles_for(user["id"])),
            started_at=now,
        )
        self._session = session

        logger.info(
            "PIN login",
            extra={"user": session.user_id, "permissions": len(session.permissions)},
        )
        return session

    def logout(self) -> None:
        self._session = None

    @property
    def session(self) -> Session | None:
        return self._session

    @property
    def is_locked(self) -> bool:
        return self._locked_until is not None and datetime.now(UTC) < self._locked_until

    @property
    def attempts_remaining(self) -> int:
        return max(0, self.max_attempts - self._failures)

    # ── step-up ──────────────────────────────────────────────────────────────

    def approve(self, pin: str, permission: str, *, now: datetime | None = None) -> Session:
        """
        A manager authorises ONE action without the cashier being logged out.

        Systems that force a full logout get defeated by managers sharing their
        PIN, which destroys accountability entirely — so the queue keeps moving
        and both identities are recorded (docs/05).

        The approver must actually hold the permission. Otherwise any two
        cashiers could authorise each other.
        """
        now = now or datetime.now(UTC)
        self._assert_not_locked(now)

        user = self._match(pin)
        if user is None:
            self._record_failure(now)
            raise PinRejected("رمز الدخول غير صحيح")

        permissions = frozenset(self._permissions_for(user["id"]))
        if permission not in permissions:
            # Not a lockout-worthy failure: the PIN was right, the person simply
            # cannot authorise this. Counting it would let a cashier lock the
            # terminal by asking the wrong colleague.
            raise PinRejected(f"{user['full_name_ar']} لا يملك صلاحية: {permission}")

        self._failures = 0
        return Session(
            user_id=user["id"],
            full_name_ar=user["full_name_ar"] or "",
            permissions=permissions,
            roles=tuple(self._roles_for(user["id"])),
            started_at=now,
        )

    # ── internals ────────────────────────────────────────────────────────────

    def _assert_not_locked(self, now: datetime) -> None:
        if self._locked_until and now < self._locked_until:
            raise DeviceLocked(int((self._locked_until - now).total_seconds()))
        if self._locked_until and now >= self._locked_until:
            self._locked_until = None
            self._failures = 0

    def _record_failure(self, now: datetime) -> None:
        self._failures += 1
        if self._failures >= self.max_attempts:
            self._locked_until = now + timedelta(seconds=self.lockout_seconds)
            logger.warning(
                "Device locked after repeated PIN failures",
                extra={"attempts": self._failures, "seconds": self.lockout_seconds},
            )

    def _match(self, pin: str):
        """
        Find the active user whose hash this PIN matches.

        Every candidate is checked rather than short-circuiting on the first
        match, so the time taken does not reveal how many staff have PINs.
        """
        matched = None

        for row in self.db.query(
            "SELECT id, email, full_name_ar, pin_hash FROM m_users "
            "WHERE is_active = 1 AND pin_hash IS NOT NULL AND pin_hash != ''"
        ):
            try:
                if verify(pin, row["pin_hash"]) and matched is None:
                    matched = row
            except UnsupportedHash:
                # A hash this build cannot read. Log it and keep going — one
                # unreadable row must not lock out everybody else.
                logger.error("Unverifiable PIN hash in the mirror", extra={"user": row["id"]})

        return matched

    def _permissions_for(self, user_id: str) -> set[str]:
        """
        The union of every role assignment. Read, not derived.

        A branch-scoped assignment and an org-wide one both apply; the server
        already resolved that, and re-deriving it here would be a second
        implementation of the permission rules.
        """
        permissions: set[str] = set()
        for row in self.db.query(
            "SELECT permissions FROM m_permissions WHERE user_id = ?", (user_id,)
        ):
            permissions.update(json.loads(row["permissions"] or "[]"))
        return permissions

    def _roles_for(self, user_id: str) -> list[str]:
        return [
            row["role_code"]
            for row in self.db.query(
                "SELECT role_code FROM m_permissions WHERE user_id = ?", (user_id,)
            )
            if row["role_code"]
        ]


def settings_from_mirror(db: Database) -> tuple[int, int]:
    """`security.pin_lockout_*`, falling back to the documented defaults."""

    def value(key: str, fallback: int) -> int:
        raw = db.scalar("SELECT value FROM m_settings WHERE key = ?", (f"security.{key}",))
        return int(json.loads(raw)) if raw else fallback

    return (
        value("pin_lockout_attempts", DEFAULT_MAX_ATTEMPTS),
        value("pin_lockout_minutes", DEFAULT_LOCKOUT_SECONDS // 60) * 60,
    )
