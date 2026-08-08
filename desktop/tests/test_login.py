"""
Offline PIN login.

Two things are being checked, and they pull against each other:

  * a cashier can log in with the internet down — otherwise the whole offline
    design is theatre;
  * a 4-digit secret is still defensible, which is the lockout's job, and a
    revoked user must not survive in the cache.

The hashes here are produced by Django's own `make_password` output format, so
the terminal is verifying the same string the server wrote.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from caesar_pos.local.db import Database, connect
from caesar_pos.security import pin as pin_module
from caesar_pos.security.session import (
    Authenticator,
    DeviceLocked,
    PinRejected,
    settings_from_mirror,
)
from caesar_pos.ui.login.pin_pad import PinPad


def pbkdf2_hash(raw: str, *, iterations: int = 100, salt: str = "abcd1234") -> str:
    """Django's `pbkdf2_sha256$iterations$salt$b64` layout."""
    derived = hashlib.pbkdf2_hmac("sha256", raw.encode(), salt.encode(), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${base64.b64encode(derived).decode()}"


def argon2_hash(raw: str) -> str:
    from argon2 import PasswordHasher

    return "argon2" + PasswordHasher().hash(raw)


@pytest.fixture
def db(tmp_path):
    connection = connect(tmp_path / "login.db")
    yield Database(connection)
    connection.close()


@pytest.fixture
def staff(db):
    def add(
        user_id: str,
        name: str,
        pin: str,
        permissions: list[str],
        *,
        active=True,
        hasher=pbkdf2_hash,
    ):
        db.upsert_mirror(
            "m_users",
            {
                "id": user_id,
                "email": f"{user_id}@caesar.test",
                "full_name_ar": name,
                "pin_hash": hasher(pin),
                "is_active": int(active),
                "payload": "{}",
            },
        )
        db.upsert_mirror(
            "m_permissions",
            {
                "id": f"ra-{user_id}",
                "user_id": user_id,
                "role_code": "CASHIER" if "orders.refund" not in permissions else "BRANCH_MANAGER",
                "permissions": json.dumps(permissions),
                "payload": "{}",
            },
        )

    return add


# ── hash verification ────────────────────────────────────────────────────────


class TestPinVerification:
    def test_a_correct_pbkdf2_pin_verifies(self) -> None:
        assert pin_module.verify("1234", pbkdf2_hash("1234")) is True

    def test_a_wrong_pbkdf2_pin_does_not(self) -> None:
        assert pin_module.verify("9999", pbkdf2_hash("1234")) is False

    def test_a_correct_argon2_pin_verifies(self) -> None:
        """The hasher the server actually prefers."""
        assert pin_module.verify("1234", argon2_hash("1234")) is True

    def test_a_wrong_argon2_pin_does_not(self) -> None:
        assert pin_module.verify("0000", argon2_hash("1234")) is False

    def test_both_algorithms_are_supported(self) -> None:
        """
        A terminal that could not verify a legacy hash would lock out the one
        member of staff who has not changed their PIN since the upgrade.
        """
        assert pin_module.verify("1111", pbkdf2_hash("1111"))
        assert pin_module.verify("1111", argon2_hash("1111"))

    def test_an_unknown_algorithm_raises_rather_than_reporting_a_wrong_pin(self) -> None:
        """
        Telling a cashier their PIN is wrong when the server upgraded its hasher
        would have them retyping until they lock the terminal.
        """
        with pytest.raises(pin_module.UnsupportedHash, match="hasher"):
            pin_module.verify("1234", "scrypt$16384$8$1$salt$hash")

    def test_an_empty_pin_or_hash_is_false_not_an_error(self) -> None:
        assert pin_module.verify("", pbkdf2_hash("1234")) is False
        assert pin_module.verify("1234", "") is False


# ── login ────────────────────────────────────────────────────────────────────


class TestLogin:
    def test_a_cashier_logs_in_with_no_network(self, db, staff) -> None:
        """The whole offline design is theatre if this does not work."""
        staff("u1", "أحمد", "1234", ["orders.create", "payments.take"])

        session = Authenticator(db).login("1234")

        assert session.user_id == "u1"
        assert session.full_name_ar == "أحمد"
        assert session.can("payments.take")
        assert not session.can("orders.refund")

    def test_a_wrong_pin_is_rejected(self, db, staff) -> None:
        staff("u1", "أحمد", "1234", ["orders.create"])

        with pytest.raises(PinRejected):
            Authenticator(db).login("9999")

    def test_an_inactive_user_cannot_log_in(self, db, staff) -> None:
        staff("u1", "مفصول", "1234", ["orders.create"], active=False)

        with pytest.raises(PinRejected):
            Authenticator(db).login("1234")

    def test_permissions_are_read_not_derived(self, db, staff) -> None:
        """
        The server already resolved branch-scoped and org-wide assignments.
        Re-deriving them here would be a second implementation of the rules.
        """
        staff("u1", "مدير", "1234", ["orders.refund", "orders.void_order"])

        session = Authenticator(db).login("1234")
        assert session.permissions == frozenset({"orders.refund", "orders.void_order"})

    def test_a_revoked_assignment_removes_the_permission(self, db, staff) -> None:
        """
        The mirror update that is a security control. The puller applies the
        DELETE; this is what it costs the user.
        """
        staff("u1", "مدير", "1234", ["orders.refund"])
        assert Authenticator(db).login("1234").can("orders.refund")

        db.delete_mirror("m_permissions", "ra-u1")
        assert not Authenticator(db).login("1234").can("orders.refund")

    def test_a_user_with_no_pin_is_not_a_candidate(self, db) -> None:
        db.upsert_mirror(
            "m_users",
            {"id": "u1", "full_name_ar": "بدون رمز", "pin_hash": "", "payload": "{}"},
        )

        with pytest.raises(PinRejected):
            Authenticator(db).login("1234")

    def test_an_unverifiable_hash_does_not_lock_out_everyone_else(self, db, staff) -> None:
        """One unreadable row must not take the whole terminal down with it."""
        db.upsert_mirror(
            "m_users",
            {
                "id": "broken",
                "full_name_ar": "قديم",
                "pin_hash": "scrypt$1$2$3$4$5",
                "is_active": 1,
                "payload": "{}",
            },
        )
        staff("u1", "أحمد", "1234", ["orders.create"])

        assert Authenticator(db).login("1234").user_id == "u1"

    def test_logging_out_clears_the_session(self, db, staff) -> None:
        staff("u1", "أحمد", "1234", [])
        auth = Authenticator(db)
        auth.login("1234")

        auth.logout()
        assert auth.session is None


# ── the lockout ──────────────────────────────────────────────────────────────


class TestLockout:
    def test_five_wrong_pins_lock_the_device(self, db, staff) -> None:
        """What makes a 4-digit secret defensible (docs/09, S2)."""
        staff("u1", "أحمد", "1234", [])
        auth = Authenticator(db, max_attempts=5, lockout_seconds=900)

        for _ in range(5):
            with pytest.raises(PinRejected):
                auth.login("0000")

        with pytest.raises(DeviceLocked):
            auth.login("1234")

    def test_the_lock_is_on_the_device_not_the_account(self, db, staff) -> None:
        """
        Locking the account would let anyone with a keypad lock out the manager —
        a denial of service that needs no credentials at all.
        """
        staff("mgr", "مدير", "1234", ["orders.refund"])
        auth = Authenticator(db, max_attempts=2, lockout_seconds=900)

        for _ in range(2):
            with pytest.raises(PinRejected):
                auth.login("0000")

        # A different terminal is unaffected.
        assert Authenticator(db).login("1234").user_id == "mgr"

    def test_the_lock_expires(self, db, staff) -> None:
        staff("u1", "أحمد", "1234", [])
        auth = Authenticator(db, max_attempts=2, lockout_seconds=60)
        now = datetime.now(UTC)

        for _ in range(2):
            with pytest.raises(PinRejected):
                auth.login("0000", now=now)

        with pytest.raises(DeviceLocked):
            auth.login("1234", now=now + timedelta(seconds=30))

        assert auth.login("1234", now=now + timedelta(seconds=90)).user_id == "u1"

    def test_a_correct_pin_resets_the_counter(self, db, staff) -> None:
        staff("u1", "أحمد", "1234", [])
        auth = Authenticator(db, max_attempts=3)

        with pytest.raises(PinRejected):
            auth.login("0000")
        auth.login("1234")

        assert auth.attempts_remaining == 3

    def test_remaining_attempts_are_reported(self, db, staff) -> None:
        """A cashier who does not know they are on their last try locks the till."""
        staff("u1", "أحمد", "1234", [])
        auth = Authenticator(db, max_attempts=5)

        with pytest.raises(PinRejected):
            auth.login("0000")

        assert auth.attempts_remaining == 4

    def test_the_lockout_policy_comes_from_the_mirror(self, db) -> None:
        for key, value in [("pin_lockout_attempts", 3), ("pin_lockout_minutes", 5)]:
            db.upsert_mirror(
                "m_settings",
                {"key": f"security.{key}", "value": json.dumps(value), "payload": "{}"},
                key="key",
            )

        assert settings_from_mirror(db) == (3, 300)

    def test_it_falls_back_to_the_documented_defaults(self, db) -> None:
        assert settings_from_mirror(db) == (5, 900)


# ── step-up approval ─────────────────────────────────────────────────────────


class TestStepUp:
    def test_a_manager_approves_without_logging_the_cashier_out(self, db, staff) -> None:
        """
        Systems that force a full logout get defeated by managers sharing their
        PIN, which destroys accountability entirely.
        """
        staff("cashier", "كاشير", "1111", ["orders.create"])
        staff("mgr", "مدير", "2222", ["orders.refund"])

        auth = Authenticator(db)
        cashier = auth.login("1111")

        approver = auth.approve("2222", "orders.refund")

        assert approver.user_id == "mgr"
        assert auth.session is cashier, "the cashier is still on the till"

    def test_an_approver_without_the_permission_is_refused(self, db, staff) -> None:
        """Otherwise any two cashiers could authorise each other."""
        staff("cashier", "كاشير", "1111", ["orders.create"])
        staff("other", "كاشير آخر", "2222", ["orders.create"])

        auth = Authenticator(db)
        auth.login("1111")

        with pytest.raises(PinRejected, match="لا يملك صلاحية"):
            auth.approve("2222", "orders.refund")

    def test_asking_the_wrong_colleague_does_not_count_toward_the_lockout(self, db, staff) -> None:
        """
        The PIN was right; the person simply cannot authorise this. Counting it
        would let a cashier lock the terminal by asking the wrong person twice.
        """
        staff("cashier", "كاشير", "1111", ["orders.create"])
        staff("other", "زميل", "2222", ["orders.create"])

        auth = Authenticator(db, max_attempts=2)
        auth.login("1111")

        for _ in range(3):
            with pytest.raises(PinRejected):
                auth.approve("2222", "orders.refund")

        assert not auth.is_locked

    def test_a_wrong_approval_pin_does_count(self, db, staff) -> None:
        staff("cashier", "كاشير", "1111", ["orders.create"])
        auth = Authenticator(db, max_attempts=2)
        auth.login("1111")

        for _ in range(2):
            with pytest.raises(PinRejected):
                auth.approve("9999", "orders.refund")

        assert auth.is_locked


# ── the pad ──────────────────────────────────────────────────────────────────


class TestPinPad:
    def test_it_submits_itself_at_the_configured_length(self, qtbot) -> None:
        """Asking for a fifth tap after four is one interaction too many."""
        pad = PinPad(length=4)
        qtbot.addWidget(pad)

        submitted = []
        pad.submitted.connect(submitted.append)

        for digit in "1234":
            pad.press(digit)

        assert submitted == ["1234"]

    def test_it_clears_after_submitting(self, qtbot) -> None:
        pad = PinPad(length=4)
        qtbot.addWidget(pad)
        for digit in "1234":
            pad.press(digit)

        assert pad.value == ""

    def test_backspace_removes_the_last_digit(self, qtbot) -> None:
        pad = PinPad(length=6)
        qtbot.addWidget(pad)
        for key in ["1", "2", "3", "⌫"]:
            pad.press(key)

        assert pad.value == "12"

    def test_it_never_shows_the_digits(self, qtbot) -> None:
        """The screen faces the room."""
        pad = PinPad(length=4)
        qtbot.addWidget(pad)
        pad.press("7")

        assert "7" not in pad.dots.text()
        assert pad.dots.text() == "●○○○"

    def test_extra_digits_are_ignored_rather_than_wrapping(self, qtbot) -> None:
        pad = PinPad(length=4)
        qtbot.addWidget(pad)

        submitted = []
        pad.submitted.connect(submitted.append)
        for digit in "123456":
            pad.press(digit)

        assert submitted == ["1234"]

    def test_confirm_submits_a_short_pin(self, qtbot) -> None:
        """A six-digit policy still lets somebody with a four-digit PIN confirm."""
        pad = PinPad(length=6)
        qtbot.addWidget(pad)

        submitted = []
        pad.submitted.connect(submitted.append)
        for key in ["1", "2", "3", "✓"]:
            pad.press(key)

        assert submitted == ["123"]

    def test_confirming_nothing_does_nothing(self, qtbot) -> None:
        pad = PinPad()
        qtbot.addWidget(pad)

        submitted = []
        pad.submitted.connect(submitted.append)
        pad.press("✓")

        assert submitted == []


class TestLoginWindow:
    def test_a_correct_pin_emits_the_session(self, qtbot, db, staff) -> None:
        from caesar_pos.ui.login.window import LoginWindow

        staff("u1", "أحمد", "1234", ["orders.create"])
        window = LoginWindow(Authenticator(db))
        qtbot.addWidget(window)

        sessions = []
        window.logged_in.connect(sessions.append)
        for digit in "1234":
            window.pad.press(digit)

        assert len(sessions) == 1
        assert sessions[0].full_name_ar == "أحمد"

    def test_a_wrong_pin_shows_the_remaining_attempts(self, qtbot, db, staff) -> None:
        from caesar_pos.ui.login.window import LoginWindow

        staff("u1", "أحمد", "1234", [])
        window = LoginWindow(Authenticator(db, max_attempts=5))
        qtbot.addWidget(window)

        for digit in "0000":
            window.pad.press(digit)

        # `isHidden`, not `isVisible`: a child is only "visible" once its window
        # has been shown, and these tests never open one.
        assert not window.message.isHidden()
        assert "4" in window.message.text()

    def test_a_locked_device_says_how_long(self, qtbot, db, staff) -> None:
        from caesar_pos.ui.login.window import LoginWindow

        staff("u1", "أحمد", "1234", [])
        auth = Authenticator(db, max_attempts=1, lockout_seconds=900)
        window = LoginWindow(auth)
        qtbot.addWidget(window)

        for digit in "0000":
            window.pad.press(digit)
        for digit in "1234":
            window.pad.press(digit)

        assert "دقيقة" in window.message.text()

    def test_the_sync_state_is_visible_before_login(self, qtbot, db) -> None:
        """
        A cashier arriving to a terminal that has been offline since Tuesday
        should learn that here, not after taking three orders.
        """
        from caesar_pos.ui.login.window import LoginWindow

        window = LoginWindow(Authenticator(db), sync_label="🔴 غير متصل (45)")
        qtbot.addWidget(window)

        assert "غير متصل" in window.sync.text()
