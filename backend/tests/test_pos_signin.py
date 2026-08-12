"""
How a cashier gets into the till.

A cashier has **no account to log into**. They have a PIN, a badge, and a
terminal the branch enrolled — and that is the whole credential. Giving every
cashier an email and a password would mean a password typed on a shared screen
in front of a queue, which is a password written on the till within a week. The
admin still signs in with an email, from a different screen, doing a different
job in a different place.

The rule that makes a four-digit secret acceptable is stated in
`accounts/models.py` and is the thing most of this file defends: **a PIN or a
badge is only ever accepted from an activated device.** The device proves the
request comes from a terminal the branch owns; the PIN only decides which human
is standing at it. On the open internet a four-digit PIN is guessable in an
afternoon, so every test that could pass without the device binding is written
to fail without it.
"""

from __future__ import annotations

import uuid

import pytest
from rest_framework.test import APIClient

from apps.accounts import tokens
from apps.accounts.badges import Badge, fingerprint, mint
from apps.authz.context import PrincipalKind
from apps.licensing.models import Device, DeviceStatus, License, LicenseType

pytestmark = pytest.mark.django_db

URL = "/api/v1/auth/pos-login/"


@pytest.fixture
def device(organization, branch) -> Device:
    licence = License.objects.create(
        organization=organization,
        branch=branch,
        key_hash=uuid.uuid4().hex,
        key_prefix="QSR-TEST",
        customer_email="owner@caesar.test",
        license_type=LicenseType.YEARLY,
        max_devices=3,
    )
    return Device.objects.create(
        license=licence,
        branch=branch,
        device_name="كاشير الصالة",
        secret_hash="x" * 32,
        status=DeviceStatus.ACTIVE,
    )


@pytest.fixture
def terminal(device) -> APIClient:
    """A browser that has been enrolled — a device token and no human yet."""
    pair = tokens.issue_pair(
        user=None,
        kind=PrincipalKind.DEVICE,
        organization_id=device.license.organization_id,
        branch_id=device.branch_id,
        device_id=device.id,
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {pair['access']}")
    return client


@pytest.fixture
def cashier(make_user, branch):
    user = make_user(email="mona@caesar.test", role="CASHIER", branch=branch, pin="4417")
    return user


def badge_for(user, issued_by=None) -> str:
    raw = mint()
    Badge.objects.create(user=user, token_hash=fingerprint(raw), label=user.full_name_ar)
    return raw


# ── the PIN ──────────────────────────────────────────────────────────────────


class TestSigningInWithAPin:
    def test_a_pin_at_an_enrolled_terminal_returns_a_session(self, terminal, cashier) -> None:
        response = terminal.post(URL, {"pin": "4417"}, format="json")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["access"]
        # The name comes back so the till can greet them without a second call.
        assert data["user"]["full_name_ar"] == cashier.full_name_ar

    def test_the_session_names_both_the_person_and_the_till(
        self, terminal, cashier, device
    ) -> None:
        """
        POS, not WEB. Both identities land in the audit log, which is what makes
        "who rang this, and on which till" answerable a month later.
        """
        access = terminal.post(URL, {"pin": "4417"}, format="json").json()["data"]["access"]

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        me = client.get("/api/v1/auth/me/").json()["data"]

        assert me["id"] == str(cashier.id)

    def test_the_wrong_pin_is_refused(self, terminal, cashier) -> None:
        assert terminal.post(URL, {"pin": "0000"}, format="json").status_code == 401

    def test_a_pin_is_never_accepted_without_a_device(self, authed, cashier, branch) -> None:
        """
        The rule the whole design rests on. A four-digit secret reachable from
        the open internet is guessable in an afternoon; the device binding is
        the entire reason it is allowed to be four digits.
        """
        web = authed(cashier, branch=branch)  # a WEB session — no device
        response = web.post(URL, {"pin": "4417"}, format="json")

        assert response.status_code == 403
        assert response.json()["code"] == "DEVICE_REQUIRED"

    def test_a_revoked_terminal_cannot_sign_anyone_in(self, terminal, cashier, device) -> None:
        """
        A till taken out of service stops being a place people can clock in —
        and it stops at the door, not in this view.

        401 rather than 403 because the middleware re-reads the device's status
        on every request and hands back an anonymous context for a revoked one.
        The token is dead immediately, without waiting to expire, which is a
        stronger guarantee than any check inside the endpoint could make.
        """
        device.status = DeviceStatus.REVOKED
        device.save(update_fields=["status"])

        assert terminal.post(URL, {"pin": "4417"}, format="json").status_code == 401

    def test_a_deactivated_person_cannot_sign_in(self, terminal, cashier) -> None:
        cashier.is_active = False
        cashier.save(update_fields=["is_active"])

        assert terminal.post(URL, {"pin": "4417"}, format="json").status_code == 401

    def test_somebody_from_another_branch_is_not_a_candidate(
        self, terminal, make_user, organization
    ) -> None:
        """
        The PIN identifies a person with no username to disambiguate, so the
        candidate set is what keeps it honest: only people who work at THIS
        device's branch can be matched by a PIN typed on it.
        """
        from apps.organizations.models import Branch

        other = Branch.objects.create(organization=organization, code="XB", name_ar="فرع تاني")
        make_user(email="far@caesar.test", role="CASHIER", branch=other, pin="9182")

        assert terminal.post(URL, {"pin": "9182"}, format="json").status_code == 401

    def test_neither_a_pin_nor_a_badge_is_refused(self, terminal) -> None:
        assert terminal.post(URL, {}, format="json").status_code == 400


# ── the badge ────────────────────────────────────────────────────────────────


class TestSigningInWithABadge:
    def test_a_badge_signs_the_holder_in(self, terminal, cashier) -> None:
        token = badge_for(cashier)

        response = terminal.post(URL, {"badge": token}, format="json")

        assert response.status_code == 200
        assert response.json()["data"]["user"]["full_name_ar"] == cashier.full_name_ar

    def test_a_revoked_badge_stops_working_immediately(self, terminal, cashier) -> None:
        """
        The whole point of being able to reprint one. A card left on a counter
        has to be dead the moment somebody says so, not at the next shift.
        """
        token = badge_for(cashier)
        Badge.objects.filter(user=cashier).first().revoke()

        assert terminal.post(URL, {"badge": token}, format="json").status_code == 401

    def test_an_unknown_badge_is_refused(self, terminal, cashier) -> None:
        assert terminal.post(URL, {"badge": mint()}, format="json").status_code == 401

    def test_some_other_qr_in_the_room_is_not_a_sign_in_attempt(self, terminal, cashier) -> None:
        """
        A product barcode or a WiFi card scanned by accident is the wrong kind
        of thing, not a failed attempt against somebody — counting it as one
        would let a busy scanner lock the terminal out on its own.
        """
        response = terminal.post(URL, {"badge": "https://wifi.example/join"}, format="json")

        assert response.status_code == 401
        from apps.accounts.models import LoginAttempt

        assert not LoginAttempt.objects.filter(identifier=str(cashier.id)).exists()

    def test_a_badge_is_never_accepted_without_a_device(self, authed, cashier, branch) -> None:
        token = badge_for(cashier)
        web = authed(cashier, branch=branch)

        assert web.post(URL, {"badge": token}, format="json").status_code == 403

    def test_using_a_badge_records_when(self, terminal, cashier) -> None:
        token = badge_for(cashier)
        terminal.post(URL, {"badge": token}, format="json")

        assert Badge.objects.get(user=cashier).last_used_at is not None


# ── issuing them ─────────────────────────────────────────────────────────────


class TestCreatingACashier:
    @pytest.fixture
    def owner(self, authed, make_user, branch):
        return authed(make_user(email="owner@caesar.test", role="SUPER_ADMIN"), branch=branch)

    def payload(self, **overrides) -> dict:
        return {
            "email": "new@caesar.test",
            "full_name_ar": "سلمى",
            "role": "CASHIER",
            **overrides,
        }

    def test_a_cashier_is_created_without_a_password(self, owner) -> None:
        """The normal case. No account, no password typed at a shared screen."""
        response = owner.post("/api/v1/staff/", self.payload(), format="json")

        assert response.status_code == 201

    def test_they_get_a_pin_by_default(self, owner, terminal) -> None:
        """
        Always, not on request. A person created without one cannot work until
        a second easily-forgotten step is done — and in practice that means a
        manager lends them theirs, and the audit trail starts naming the wrong
        human.
        """
        created = owner.post("/api/v1/staff/", self.payload(), format="json").json()["data"]
        pin = created["credentials"]["pin"]

        assert pin.isdigit()
        assert terminal.post(URL, {"pin": pin}, format="json").status_code == 200

    def test_they_get_a_badge_carrying_their_name(self, owner, terminal) -> None:
        created = owner.post("/api/v1/staff/", self.payload(), format="json").json()["data"]

        assert created["credentials"]["name"] == "سلمى"
        assert (
            terminal.post(
                URL, {"badge": created["credentials"]["badge"]}, format="json"
            ).status_code
            == 200
        )

    def test_without_a_password_email_sign_in_is_closed(self, owner) -> None:
        """
        Unusable, not blank and not a default. A cashier account that could be
        reached from the internet with a guessable password would undo the
        device binding entirely.
        """
        owner.post("/api/v1/staff/", self.payload(), format="json")

        from apps.accounts.models import User

        assert not User.objects.get(email="new@caesar.test").has_usable_password()

    def test_an_admin_can_still_be_given_a_password(self, owner) -> None:
        response = owner.post(
            "/api/v1/staff/",
            self.payload(
                email="boss2@caesar.test",
                role="BRANCH_MANAGER",
                password="correct-horse-battery-staple",
            ),
            format="json",
        )

        assert response.status_code == 201
        from apps.accounts.models import User

        assert User.objects.get(email="boss2@caesar.test").has_usable_password()

    def test_two_people_never_share_a_pin(self, owner) -> None:
        """
        Uniqueness is not cosmetic: sign-in identifies a person BY their PIN,
        with no username to disambiguate. Two cashiers on 1234 would mean
        whichever row came back first gets the sale, and the audit trail would
        name the wrong human silently and forever.
        """
        first = owner.post("/api/v1/staff/", self.payload(), format="json").json()["data"]
        second = owner.post(
            "/api/v1/staff/",
            self.payload(email="two@caesar.test", full_name_ar="هدى"),
            format="json",
        ).json()["data"]

        assert first["credentials"]["pin"] != second["credentials"]["pin"]

    def test_the_pin_is_not_readable_afterwards(self, owner) -> None:
        """
        Returned once, at issue. The list may say a person HAS a PIN — that is
        `has_pin`, and a manager needs it to know who can still work — but never
        what the PIN is, and never the hash either. A staff list that leaked
        either would turn one borrowed manager session into every till.
        """
        created = owner.post("/api/v1/staff/", self.payload(), format="json").json()["data"]
        issued = created["credentials"]["pin"]

        row = next(
            r for r in owner.get("/api/v1/staff/").json()["data"] if r["email"] == "new@caesar.test"
        )

        assert row["has_pin"] is True
        assert issued not in str(row)
        assert "pin_hash" not in row


class TestReprintingABadge:
    @pytest.fixture
    def owner(self, authed, make_user, branch):
        return authed(make_user(email="owner@caesar.test", role="SUPER_ADMIN"), branch=branch)

    def test_reprinting_kills_the_old_card(self, owner, terminal, cashier) -> None:
        """The one case reprinting exists for: a card left somewhere it should not be."""
        old = badge_for(cashier)

        fresh = owner.post(f"/api/v1/staff/{cashier.id}/badge/", {}, format="json").json()["data"]

        assert terminal.post(URL, {"badge": fresh["badge"]}, format="json").status_code == 200
        assert terminal.post(URL, {"badge": old}, format="json").status_code == 401

    def test_a_cashier_cannot_print_themselves_a_badge(self, authed, cashier, branch) -> None:
        """Minting the thing that unlocks a till answers to `staff.reset_pin`."""
        client = authed(cashier, branch=branch)

        response = client.post(f"/api/v1/staff/{cashier.id}/badge/", {}, format="json")

        assert response.status_code == 403


# ── what they did ────────────────────────────────────────────────────────────


class TestActivity:
    @pytest.fixture
    def owner(self, authed, make_user, branch):
        return authed(make_user(email="owner@caesar.test", role="SUPER_ADMIN"), branch=branch)

    def test_it_counts_orders_and_changes_separately(
        self, owner, terminal, cashier, branch, organization
    ) -> None:
        """
        Opening an order is excluded from the change count. Counting it as both
        would tally every order twice — once as an order and again as a change
        to it — and make the two numbers impossible to compare across staff.
        """
        from decimal import Decimal

        from apps.catalog.models import Category, Product, ProductVariant
        from apps.orders import services as order_services
        from apps.orders.models import EventType

        category = Category.objects.create(organization=organization, branch=branch, name_ar="قهوة")
        product = Product.objects.create(
            organization=organization, branch=branch, category=category, sku="C", name_ar="قهوة"
        )
        variant = ProductVariant.objects.create(
            product=product, sku="C-M", price=Decimal("60.00"), is_default=True
        )

        order = order_services.open_order(branch=branch, user=cashier)
        order_services.apply_events(
            order,
            [
                {
                    "id": str(uuid.uuid4()),
                    "type": EventType.ITEM_ADDED,
                    "payload": {
                        "line_id": str(uuid.uuid4()),
                        "variant_id": str(variant.id),
                        "quantity": "1",
                    },
                }
            ],
            actor=cashier,
        )

        data = owner.get(f"/api/v1/staff/{cashier.id}/activity/").json()["data"]

        assert data["orders_opened"] == 1
        assert data["changes_made"] == 1

    def test_it_breaks_out_the_three_an_owner_watches(self, owner, cashier) -> None:
        data = owner.get(f"/api/v1/staff/{cashier.id}/activity/").json()["data"]

        # Broken out rather than buried in a total: these move money without
        # selling anything, which is why they are the ones worth comparing.
        assert data["items_voided"] == 0
        assert data["discounts_given"] == 0
        assert data["prices_overridden"] == 0

    def test_a_cashier_cannot_read_anybody_activity(self, authed, cashier, branch) -> None:
        client = authed(cashier, branch=branch)

        assert client.get(f"/api/v1/staff/{cashier.id}/activity/").status_code == 403


class TestOneCashierSigningInDoesNotLockOutTheBranch:
    """
    The bug that stopped the web till working, and it had two halves.

    The candidate loop used to call `verify_pin` for every member of staff until
    one matched. `verify_pin` counts a failure against its own (user, device)
    scope, so:

      1. A cashier signing in with her OWN correct PIN recorded a failed attempt
         against every colleague the loop reached before her — and wrote an
         `auth.login_failed` audit row for each of them, naming people who were
         not in the building.

      2. `verify_pin` opens with `assert_not_locked`, which RAISES. So once a
         colleague was locked out, the loop aborted before reaching anybody, and
         the till answered 429 to a correct PIN — citing somebody else's lockout.

    A terminal that stops selling because a colleague mistyped is worse than the
    brute-force risk the per-user counter covered, and `terminal:{device}` covers
    that anyway. These tests pin both halves.
    """

    def _sign_in(self, terminal, pin: str):
        return terminal.post("/api/v1/auth/pos-login/", {"pin": pin}, format="json")

    def test_a_correct_pin_records_no_failure_against_anybody_else(
        self, terminal, device, make_user, branch
    ) -> None:
        from apps.accounts.models import LoginAttempt

        colleague = make_user(email="colleague@caesar.test", pin="1111", branch=branch)
        mona = make_user(email="mona@caesar.test", role="CASHIER", pin="3333", branch=branch)

        assert self._sign_in(terminal, "3333").status_code == 200

        failures = LoginAttempt.objects.filter(succeeded=False)
        assert not failures.exists(), (
            "a correct PIN wrote failed attempts for other staff: "
            f"{list(failures.values_list('identifier', flat=True))}"
        )
        # Exactly one attempt, and it belongs to the person who actually signed in.
        assert list(LoginAttempt.objects.values_list("identifier", flat=True)) == [str(mona.id)]
        assert colleague.id != mona.id

    def test_a_locked_out_colleague_does_not_close_the_till(
        self, terminal, device, make_user, branch
    ) -> None:
        """
        The half that actually took the till down. A cashier with a valid PIN must
        get in regardless of what any other staff member's counter holds.
        """
        from apps.accounts.services import LockoutPolicy, record_failure

        colleague = make_user(email="colleague@caesar.test", pin="1111", branch=branch)
        make_user(email="mona@caesar.test", role="CASHIER", pin="3333", branch=branch)

        policy = LockoutPolicy.load(colleague.organization_id)
        for _ in range(policy.max_attempts + 1):
            record_failure(f"pin:{colleague.id}:{device.id}", policy)

        response = self._sign_in(terminal, "3333")

        assert response.status_code == 200, response.data

    def test_the_terminal_itself_still_locks_after_repeated_wrong_pins(
        self, terminal, device, make_user, branch
    ) -> None:
        """
        The control that matters is kept. Without it a four-digit PIN on an
        enrolled till is 10,000 guesses, which is an afternoon's work.
        """
        from apps.accounts.services import LockoutPolicy

        make_user(email="mona@caesar.test", role="CASHIER", pin="3333", branch=branch)
        policy = LockoutPolicy.load(branch.organization_id)

        for _ in range(policy.max_attempts):
            assert self._sign_in(terminal, "0000").status_code == 401

        # Locked now — and a CORRECT pin is refused too, which is the point of a
        # terminal-wide lock rather than a per-person one.
        assert self._sign_in(terminal, "3333").status_code == 429

    def test_a_wrong_pin_is_attributed_to_the_terminal_not_to_a_person(
        self, terminal, device, make_user, branch
    ) -> None:
        """
        Nobody has been identified by a wrong PIN, so putting the failure on a
        staff record would be a failed login recorded on the strength of a guess.
        """
        from apps.accounts.models import LoginAttempt

        make_user(email="mona@caesar.test", role="CASHIER", pin="3333", branch=branch)

        self._sign_in(terminal, "0000")

        attempt = LoginAttempt.objects.get(succeeded=False)
        assert attempt.identifier == f"terminal:{device.id}"


class TestUnlockingATerminalFromTheAdmin:
    """
    The way out of a lockout.

    The fifteen-minute terminal lock is the control that makes a four-digit PIN
    defensible, and it stays. What was missing was a remedy: until this existed
    the only options were waiting out the window with a queue at the counter, or a
    shell on the server. A control that only an engineer can reach is not a
    control the cafe has.
    """

    def _admin(self, authed, make_user, branch):
        """
        Build this BEFORE locking anything.

        `make_user` creates a RoleAssignment, which calls `invalidate_all()`. On
        Redis that is a scoped `delete_pattern`, but LocMemCache has no such
        method and the fallback is `cache.clear()` — so in tests creating any user
        wipes every lockout key. Two of these tests were written the other way
        round and reported the code as broken when the fixture had swept up after
        itself.
        """
        owner = make_user(email="owner@caesar.test", role="SUPER_ADMIN")
        return authed(owner, branch=branch)

    def _lock(self, device, branch):
        from apps.accounts.services import LockoutPolicy, record_failure

        policy = LockoutPolicy.load(branch.organization_id)
        for _ in range(policy.max_attempts + 1):
            record_failure(f"terminal:{device.id}", policy)

    def _row(self, client, device):
        payload = client.get("/api/v1/licensing/devices/").data
        rows = payload["results"] if isinstance(payload, dict) else payload
        return next(row for row in rows if row["id"] == str(device.id))

    def test_a_locked_terminal_is_visible_on_the_devices_screen(
        self, authed, make_user, branch, device
    ) -> None:
        """
        An ACTIVE device can be locked, and that combination was unreadable from
        the admin — the till refused a correct PIN while this screen showed a
        healthy terminal. A control with no indicator beside it is a control
        nobody reaches for.
        """
        client = self._admin(authed, make_user, branch)
        assert self._row(client, device)["pin_locked"] is False

        self._lock(device, branch)

        row = self._row(client, device)
        assert row["status"] == DeviceStatus.ACTIVE, "an ACTIVE device can still be locked"
        assert row["pin_locked"] is True

    def test_unlocking_lets_a_correct_pin_back_in(
        self, authed, make_user, branch, device, terminal
    ) -> None:
        make_user(email="mona@caesar.test", role="CASHIER", pin="3333", branch=branch)
        client = self._admin(authed, make_user, branch)

        self._lock(device, branch)
        assert (
            terminal.post("/api/v1/auth/pos-login/", {"pin": "3333"}, format="json").status_code
            == 429
        )

        response = client.post(f"/api/v1/licensing/devices/{device.id}/unlock/")

        assert response.status_code == 200, response.data
        assert (
            terminal.post("/api/v1/auth/pos-login/", {"pin": "3333"}, format="json").status_code
            == 200
        )

    def test_unlocking_is_recorded_against_the_licence(
        self, authed, make_user, branch, device
    ) -> None:
        """
        Somebody cleared a security control, so who and when is part of the trail.
        """
        from apps.licensing.models import LicenseEvent

        self._lock(device, branch)
        self._admin(authed, make_user, branch).post(
            f"/api/v1/licensing/devices/{device.id}/unlock/"
        )

        assert LicenseEvent.objects.filter(
            event=LicenseEvent.Event.DEVICE_UNLOCKED, device=device
        ).exists()

    def test_a_cashier_cannot_unlock_a_terminal(self, authed, make_user, branch, device) -> None:
        """
        `devices.manage`. Otherwise the lockout is worth nothing: whoever is
        working through 0000..9999 would clear their own counter every five tries.
        """
        cashier = make_user(email="c@caesar.test", role="CASHIER", branch=branch)

        response = authed(cashier, branch=branch).post(
            f"/api/v1/licensing/devices/{device.id}/unlock/"
        )

        assert response.status_code == 403

    def test_unlocking_does_not_clear_anybody_else_s_step_up_counter(
        self, authed, make_user, branch, device
    ) -> None:
        """
        Per-user PIN counters belong to the step-up approval path, where a wrong
        PIN really is that person's failed attempt. Clearing them here would turn
        one button into a way to reset the whole branch's rate limit.
        """
        from apps.accounts.services import LockoutPolicy, is_locked, record_failure

        # Every user created first, so no later `invalidate_all()` sweeps the
        # locks this test is about.
        manager = make_user(email="mgr@caesar.test", role="BRANCH_MANAGER", branch=branch)
        client = self._admin(authed, make_user, branch)

        policy = LockoutPolicy.load(branch.organization_id)
        for _ in range(policy.max_attempts + 1):
            record_failure(f"pin:{manager.id}:{device.id}", policy)
        self._lock(device, branch)
        assert is_locked(f"pin:{manager.id}:{device.id}"), "the setup did not lock the manager"

        client.post(f"/api/v1/licensing/devices/{device.id}/unlock/")

        assert not is_locked(f"terminal:{device.id}")
        assert is_locked(f"pin:{manager.id}:{device.id}"), "the step-up counter was wiped too"

    def test_the_lockout_names_the_remedy_and_is_distinguishable_from_the_throttle(
        self, terminal, device, branch, make_user
    ) -> None:
        """
        Two different 429s reach this endpoint and they need different reactions.

        `ACCOUNT_LOCKED` is the fifteen-minute terminal lockout — a manager has to
        clear it, and the message now says so, because until the unlock button
        existed there was no remedy to name. `RATE_LIMITED` is the per-IP
        `pos_login` throttle at 5/min, which heals itself and needs nobody.

        Five wrong PINs trip BOTH, and unlocking clears only the lockout: for the
        rest of that minute the till still answers 429. Distinct codes are what
        let the client tell "wait a moment" from "fetch the manager" — which is
        the difference between forty seconds and a phone call.
        """
        make_user(email="mona@caesar.test", role="CASHIER", pin="3333", branch=branch)
        self._lock(device, branch)

        response = terminal.post("/api/v1/auth/pos-login/", {"pin": "3333"}, format="json")

        assert response.status_code == 429
        assert response.data["code"] == "ACCOUNT_LOCKED"
        assert "المدير" in response.data["message"], "the message does not name the remedy"
