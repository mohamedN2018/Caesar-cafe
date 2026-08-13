"""
Staff and roles.

The endpoints an owner needs before anything else works: creating the cashier
who will stand at the till, and giving them the PIN the Desktop authenticates
against. Until this existed, `staff.view`, `staff.manage_users`,
`staff.manage_roles` and `staff.reset_pin` were four codes in the catalogue that
no route declared.

The properties under test are the ones that make this screen safe to hand to a
branch manager: it never returns a secret, it never deletes a person, and it
cannot be used to lock the organization out of itself.
"""

from __future__ import annotations

import pytest

from apps.accounts.models import User
from apps.authz.models import Role, RoleAssignment

pytestmark = pytest.mark.django_db

STRONG = "correct-horse-battery-staple"


@pytest.fixture
def owner(make_user):
    return make_user(email="owner@caesar.test", role="SUPER_ADMIN")


@pytest.fixture
def client(authed, owner, branch):
    return authed(owner, branch=branch)


def create_payload(**overrides):
    return {
        "email": "cashier@caesar.test",
        "full_name_ar": "منى",
        "password": STRONG,
        "role": "CASHIER",
        **overrides,
    }


# ── creating people ──────────────────────────────────────────────────────────


class TestCreatingStaff:
    def test_a_cashier_can_be_created_with_a_role(self, client, branch) -> None:
        response = client.post("/api/v1/staff/", create_payload(), format="json")

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["full_name_ar"] == "منى"
        assert [a["role_code"] for a in data["assignments"]] == ["CASHIER"]

    def test_the_assignment_is_branch_scoped_by_default(self, client, branch) -> None:
        """
        Getting this backwards silently hands a branch manager every branch, so
        org-wide has to be asked for rather than defaulted into.
        """
        client.post("/api/v1/staff/", create_payload(), format="json")

        assignment = RoleAssignment.objects.get(user__email="cashier@caesar.test")
        assert assignment.branch_id == branch.id

    def test_an_org_wide_assignment_can_be_asked_for(self, client) -> None:
        client.post("/api/v1/staff/", create_payload(branch_scoped=False), format="json")

        assignment = RoleAssignment.objects.get(user__email="cashier@caesar.test")
        assert assignment.branch_id is None

    def test_a_duplicate_email_is_refused_by_field(self, client, owner) -> None:
        response = client.post("/api/v1/staff/", create_payload(email=owner.email), format="json")

        assert response.status_code == 400
        assert response.json()["code"] == "EMAIL_TAKEN"
        assert "email" in response.json()["errors"]

    def test_a_weak_password_is_refused_with_the_reasons(self, client) -> None:
        response = client.post(
            "/api/v1/staff/", create_payload(password="password1234"), format="json"
        )

        assert response.status_code == 400
        assert response.json()["code"] == "WEAK_PASSWORD"
        assert response.json()["errors"]["password"]

    def test_an_unknown_role_is_refused(self, client) -> None:
        response = client.post("/api/v1/staff/", create_payload(role="WIZARD"), format="json")

        assert response.status_code == 404
        assert response.json()["code"] == "ROLE_NOT_FOUND"
        assert not User.objects.filter(email="cashier@caesar.test").exists(), "no half-made user"

    def test_creating_needs_the_permission(self, authed, make_user, branch) -> None:
        cashier = authed(make_user(email="c@caesar.test", role="CASHIER"), branch=branch)
        assert cashier.post("/api/v1/staff/", create_payload(), format="json").status_code == 403


# ── what the list may and may not show ───────────────────────────────────────


class TestNoSecretsAreReturned:
    def test_the_pin_hash_is_never_in_the_payload(self, client, make_user, branch) -> None:
        """
        A staff list that could read a hash would turn one compromised manager
        session into every terminal in the branch.
        """
        user = make_user(email="c@caesar.test", role="CASHIER", pin="1234")
        assert user.pin_hash

        body = client.get("/api/v1/staff/").content.decode()

        assert "pin_hash" not in body
        assert user.pin_hash not in body

    def test_the_password_hash_is_never_in_the_payload(self, client, owner) -> None:
        body = client.get("/api/v1/staff/").content.decode()

        assert "password" not in body
        assert owner.password not in body

    def test_whether_a_pin_is_set_is_shown_without_the_value(
        self, client, make_user, branch
    ) -> None:
        """The fact a cashier cannot log in offline is what a manager needs."""
        make_user(email="withpin@caesar.test", role="CASHIER", pin="1234")
        make_user(email="nopin@caesar.test", role="CASHIER")

        rows = {row["email"]: row["has_pin"] for row in client.get("/api/v1/staff/").json()["data"]}

        assert rows["withpin@caesar.test"] is True
        assert rows["nopin@caesar.test"] is False


# ── PINs ─────────────────────────────────────────────────────────────────────


class TestAdministrativePinReset:
    def test_a_manager_can_set_a_cashiers_pin(self, client, make_user, branch) -> None:
        """
        Self-service needs the account password, which a manager cannot know —
        that is the whole reason this exists. The proof here is the permission.
        """
        cashier = make_user(email="c@caesar.test", role="CASHIER")
        assert not cashier.pin_hash

        response = client.post(
            f"/api/v1/staff/{cashier.id}/reset-pin/", {"pin": "4321"}, format="json"
        )

        assert response.status_code == 200
        cashier.refresh_from_db()
        assert cashier.check_pin("4321")

    def test_the_new_pin_is_not_in_the_response(self, client, make_user) -> None:
        cashier = make_user(email="c@caesar.test", role="CASHIER")
        body = client.post(
            f"/api/v1/staff/{cashier.id}/reset-pin/", {"pin": "4321"}, format="json"
        ).content.decode()

        assert "4321" not in body

    def test_the_reset_is_audited_without_the_value(self, client, make_user) -> None:
        from apps.audit.models import AuditLog

        cashier = make_user(email="c@caesar.test", role="CASHIER")
        client.post(f"/api/v1/staff/{cashier.id}/reset-pin/", {"pin": "4321"}, format="json")

        entry = AuditLog.objects.filter(action="staff.pin_reset").latest("occurred_at")
        assert entry.object_label == cashier.full_name_ar
        assert "4321" not in str(entry.detail)

    def test_the_configured_pin_length_is_enforced(self, client, make_user) -> None:
        cashier = make_user(email="c@caesar.test", role="CASHIER")
        response = client.post(
            f"/api/v1/staff/{cashier.id}/reset-pin/", {"pin": "123456"}, format="json"
        )

        assert response.status_code == 400
        assert response.json()["code"] == "PIN_LENGTH_INVALID"

    def test_resetting_needs_its_own_permission(self, authed, make_user, branch) -> None:
        """
        Editing a phone number is administration. Setting the secret that
        unlocks a till is not.
        """
        from apps.authz import catalog

        held = set(catalog.SYSTEM_ROLES["BRANCH_MANAGER"]["permissions"])
        target = make_user(email="c@caesar.test", role="CASHIER")

        manager = make_user(email="m@caesar.test", role="BRANCH_MANAGER")
        # Strip only the reset permission, keeping everything else, so the 403
        # is unambiguously about this code.
        role = Role.objects.get(organization=manager.organization, code="BRANCH_MANAGER")
        role.set_permissions(sorted(held - {"staff.reset_pin"}))

        client = authed(manager, branch=branch)
        response = client.post(
            f"/api/v1/staff/{target.id}/reset-pin/", {"pin": "4321"}, format="json"
        )

        assert response.status_code == 403
        target.refresh_from_db()
        assert not target.pin_hash


# ── roles ────────────────────────────────────────────────────────────────────


class TestRoles:
    def test_the_permission_catalogue_is_served_not_duplicated(self, client) -> None:
        """
        A role editor whose permission list was written by hand in TypeScript
        would drift the first time a code was added, and the drift would show up
        as a permission nobody can grant.
        """
        from apps.authz import catalog

        codes = {row["code"] for row in client.get("/api/v1/permissions/").json()["data"]}

        assert codes == set(catalog.PERMISSION_CODES)

    def test_a_role_lists_its_permissions(self, client) -> None:
        rows = {row["code"]: row for row in client.get("/api/v1/roles/").json()["data"]}

        assert "orders.create" in rows["CASHIER"]["permissions"]
        assert rows["CASHIER"]["is_system"] is True

    def test_a_system_roles_permissions_can_be_edited(self, client, organization) -> None:
        """An owner who does not want cashiers voiding items should say so."""
        role = Role.objects.get(organization=organization, code="CASHIER")
        keep = sorted(role.permission_codes - {"orders.void_item"})

        response = client.patch(f"/api/v1/roles/{role.id}/", {"permissions": keep}, format="json")

        assert response.status_code == 200
        role.refresh_from_db()
        assert "orders.void_item" not in role.permission_codes

    def test_editing_a_role_takes_effect_immediately(
        self, client, authed, make_user, branch, organization
    ) -> None:
        """
        Permission sets are cached on the hot path. An edit that did not
        invalidate them would take effect whenever the cache expired, which is
        indistinguishable from a bug.
        """
        cashier_user = make_user(email="c@caesar.test", role="CASHIER")
        cashier = authed(cashier_user, branch=branch)
        assert cashier.get("/api/v1/orders/").status_code == 200

        role = Role.objects.get(organization=organization, code="CASHIER")
        client.patch(
            f"/api/v1/roles/{role.id}/",
            {"permissions": sorted(role.permission_codes - {"orders.view"})},
            format="json",
        )

        assert cashier.get("/api/v1/orders/").status_code == 403

    def test_an_unknown_permission_code_is_refused(self, client, organization) -> None:
        role = Role.objects.get(organization=organization, code="CASHIER")
        response = client.patch(
            f"/api/v1/roles/{role.id}/", {"permissions": ["orders.teleport"]}, format="json"
        )

        assert response.status_code == 400

    def test_a_system_role_cannot_be_deleted(self, client, organization) -> None:
        role = Role.objects.get(organization=organization, code="CASHIER")
        response = client.delete(f"/api/v1/roles/{role.id}/")

        assert response.status_code == 409
        assert response.json()["code"] == "SYSTEM_ROLE_NOT_DELETABLE"
        assert Role.objects.filter(id=role.id).exists()

    def test_a_custom_role_in_use_cannot_be_deleted(self, client, organization, make_user) -> None:
        role = Role.objects.create(organization=organization, code="BARISTA", name_ar="باريستا")
        RoleAssignment.objects.create(user=make_user(email="b@caesar.test"), role=role)

        response = client.delete(f"/api/v1/roles/{role.id}/")

        assert response.status_code == 409
        assert response.json()["code"] == "ROLE_IN_USE"

    def test_an_unused_custom_role_can_be_deleted(self, client, organization) -> None:
        role = Role.objects.create(organization=organization, code="BARISTA", name_ar="باريستا")

        assert client.delete(f"/api/v1/roles/{role.id}/").status_code == 204
        assert not Role.objects.filter(id=role.id).exists()


class TestAssignments:
    def test_a_second_role_can_be_granted(self, client, make_user, organization, branch) -> None:
        user = make_user(email="c@caesar.test", role="CASHIER")
        waiter = Role.objects.get(organization=organization, code="WAITER")

        response = client.post(
            f"/api/v1/staff/{user.id}/assign-role/",
            {"role": str(waiter.id), "branch": str(branch.id)},
            format="json",
        )

        assert response.status_code == 200
        codes = {a["role_code"] for a in response.json()["data"]["assignments"]}
        assert codes == {"CASHIER", "WAITER"}

    def test_granting_the_same_role_twice_is_not_an_error(
        self, client, make_user, organization, branch
    ) -> None:
        user = make_user(email="c@caesar.test", role="CASHIER")
        waiter = Role.objects.get(organization=organization, code="WAITER")
        body = {"role": str(waiter.id), "branch": str(branch.id)}

        client.post(f"/api/v1/staff/{user.id}/assign-role/", body, format="json")
        response = client.post(f"/api/v1/staff/{user.id}/assign-role/", body, format="json")

        assert response.status_code == 200
        assert user.role_assignments.filter(role=waiter).count() == 1

    def test_the_last_role_cannot_be_revoked(self, client, make_user) -> None:
        """
        An account that can log in and do nothing is a support call that looks
        like a broken system rather than a configuration mistake.
        """
        user = make_user(email="c@caesar.test", role="CASHIER")
        assignment = user.role_assignments.get()

        response = client.post(
            f"/api/v1/staff/{user.id}/revoke-role/{assignment.id}/", format="json"
        )

        assert response.status_code == 409
        assert response.json()["code"] == "LAST_ROLE"
        assert user.role_assignments.exists()

    def test_a_second_role_can_be_revoked(self, client, make_user, organization, branch) -> None:
        user = make_user(email="c@caesar.test", role="CASHIER")
        waiter = Role.objects.get(organization=organization, code="WAITER")
        client.post(
            f"/api/v1/staff/{user.id}/assign-role/",
            {"role": str(waiter.id), "branch": str(branch.id)},
            format="json",
        )

        assignment = user.role_assignments.get(role=waiter)
        response = client.post(
            f"/api/v1/staff/{user.id}/revoke-role/{assignment.id}/", format="json"
        )

        assert response.status_code == 200
        assert {a["role_code"] for a in response.json()["data"]["assignments"]} == {"CASHIER"}


# ── deactivation ─────────────────────────────────────────────────────────────


class TestDeactivation:
    def test_a_person_is_deactivated_not_deleted(self, client, make_user) -> None:
        """
        Their name is on last quarter's voids and shift closures. Deleting them
        would rewrite that history into "unknown".
        """
        user = make_user(email="c@caesar.test", role="CASHIER")

        response = client.post(
            f"/api/v1/staff/{user.id}/set-active/", {"is_active": False}, format="json"
        )

        assert response.status_code == 200
        user.refresh_from_db()
        assert user.is_active is False
        assert User.objects.filter(id=user.id).exists()

    def test_delete_is_not_offered_at_all(self, client, make_user) -> None:
        user = make_user(email="c@caesar.test", role="CASHIER")
        assert client.delete(f"/api/v1/staff/{user.id}/").status_code == 405

    def test_you_cannot_deactivate_yourself(self, client, owner) -> None:
        """A support call that cannot be resolved from inside the product."""
        response = client.post(
            f"/api/v1/staff/{owner.id}/set-active/", {"is_active": False}, format="json"
        )

        assert response.status_code == 409
        assert response.json()["code"] == "CANNOT_DEACTIVATE_SELF"
        owner.refresh_from_db()
        assert owner.is_active is True

    def test_deactivating_is_audited(self, client, make_user) -> None:
        from apps.audit.models import AuditLog

        user = make_user(email="c@caesar.test", role="CASHIER")
        client.post(f"/api/v1/staff/{user.id}/set-active/", {"is_active": False}, format="json")

        assert AuditLog.objects.filter(action="staff.user_deactivated").exists()

    def test_a_reactivated_person_can_log_in_again(self, client, make_user) -> None:
        user = make_user(email="c@caesar.test", role="CASHIER")
        client.post(f"/api/v1/staff/{user.id}/set-active/", {"is_active": False}, format="json")
        client.post(f"/api/v1/staff/{user.id}/set-active/", {"is_active": True}, format="json")

        user.refresh_from_db()
        assert user.is_active is True


# ── tenancy ──────────────────────────────────────────────────────────────────


class TestCrossTenantIsolation:
    @pytest.fixture
    def stranger(self, make_user, other_organization):
        return make_user(email="stranger@other.test", role="CASHIER", org=other_organization)

    def test_another_organizations_staff_are_not_listed(self, client, stranger) -> None:
        emails = [row["email"] for row in client.get("/api/v1/staff/").json()["data"]]
        assert stranger.email not in emails

    def test_another_organizations_staff_cannot_be_read(self, client, stranger) -> None:
        assert client.get(f"/api/v1/staff/{stranger.id}/").status_code == 404

    def test_another_organizations_pin_cannot_be_reset(self, client, stranger) -> None:
        response = client.post(
            f"/api/v1/staff/{stranger.id}/reset-pin/", {"pin": "4321"}, format="json"
        )

        assert response.status_code == 404
        stranger.refresh_from_db()
        assert not stranger.pin_hash

    def test_another_organizations_roles_are_not_listed(self, client, other_organization) -> None:
        foreign = Role.objects.create(organization=other_organization, code="OTHER", name_ar="آخر")
        ids = [row["id"] for row in client.get("/api/v1/roles/").json()["data"]]
        assert str(foreign.id) not in ids


class TestSyncRolesCommand:
    """
    `sync_roles` — the command that was missing.

    `ensure_system_roles` was only ever called from `bootstrap` (once, for a new
    cafe) and `seed_demo` (a demo). So a release that added a permission code
    reached a live cafe with the code in the catalogue, the routes enforcing it,
    and no role holding it — a manager who upgraded on Tuesday and cannot open a
    screen the release notes say is theirs.

    Found the ordinary way: the HR codes were added and the API refused a
    SUPER_ADMIN.
    """

    def _run(self, **kwargs) -> str:
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("sync_roles", stdout=out, **kwargs)
        return out.getvalue()

    def test_a_newly_shipped_code_reaches_the_system_roles(self, organization, roles) -> None:
        manager = roles["BRANCH_MANAGER"]
        manager.set_permissions(sorted(set(manager.permission_codes) - {"hr.view"}))
        # Pretend the last shipped spec did not have it, which is what an upgrade
        # actually looks like.
        manager.synced_permissions = sorted(set(manager.synced_permissions) - {"hr.view"})
        manager.save(update_fields=["synced_permissions"])

        self._run()

        manager.refresh_from_db()
        assert "hr.view" in manager.permission_codes

    def test_a_permission_an_operator_removed_stays_removed(self, organization, roles) -> None:
        """
        The distinction `synced_permissions` exists for. An owner who does not
        want cashiers voiding items said so on purpose, and a deploy that
        restored it every time would be the product overruling them silently.
        """
        cashier = roles["CASHIER"]
        assert "orders.view" in cashier.permission_codes
        cashier.set_permissions(sorted(set(cashier.permission_codes) - {"orders.view"}))

        self._run()

        cashier.refresh_from_db()
        assert "orders.view" not in cashier.permission_codes

    def test_a_dry_run_writes_nothing(self, organization, roles) -> None:
        """
        A "dry run" that writes is the kind of thing an operator only discovers
        afterwards, so it reads the catalogue directly rather than calling the
        service and rolling back.
        """
        manager = roles["BRANCH_MANAGER"]
        manager.set_permissions(sorted(set(manager.permission_codes) - {"hr.view"}))
        manager.synced_permissions = sorted(set(manager.synced_permissions) - {"hr.view"})
        manager.save(update_fields=["synced_permissions"])

        output = self._run(dry_run=True)

        manager.refresh_from_db()
        assert "hr.view" not in manager.permission_codes, "the dry run wrote"
        assert "hr.view" in output, "the dry run did not report what it would do"


class TestDemoAdminCommand:
    """
    The demo administrator.

    A convenience for looking at a running system, and a liability if it outlives
    the demo. What matters in tests is that it produces a login that WORKS (a demo
    account nobody can sign in with is worse than none) and that `--rotate` really
    closes it.
    """

    def _run(self, **kwargs) -> str:
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("demo_admin", stdout=out, **kwargs)
        return out.getvalue()

    def test_it_creates_a_superuser_who_can_actually_sign_in(self, db, api) -> None:
        self._run()

        response = api.post(
            "/api/v1/auth/login/",
            {"email": "admin@caesar.deplois.net", "password": "admin"},
            format="json",
        )

        assert response.status_code == 200, response.data

    def test_it_holds_the_role_and_not_only_the_flag(self, db) -> None:
        """
        `is_superuser` short-circuits `can()`, but the ROLE is what the permission
        cache and the audit trail read. An account with only the flag looks
        permission-less on every screen that lists what somebody may do.
        """
        from apps.accounts.models import User
        from apps.authz.models import RoleAssignment

        self._run()
        user = User.objects.get(email="admin@caesar.deplois.net")

        assert user.is_superuser
        assert RoleAssignment.objects.filter(user=user, role__code="SUPER_ADMIN").exists()

    def test_running_it_twice_resets_rather_than_duplicating(self, db) -> None:
        from apps.accounts.models import User

        self._run()
        self._run()

        assert User.objects.filter(email="admin@caesar.deplois.net").count() == 1

    def test_it_warns_that_the_password_is_guessable(self, db) -> None:
        # The warning is the feature. A weak credential on an internet-facing host
        # with nothing saying so is how a demo becomes an incident.
        output = self._run()

        assert "demo_admin --rotate" in output

    def test_rotate_replaces_the_weak_password(self, db, api) -> None:
        self._run()
        output = self._run(rotate=True)

        refused = api.post(
            "/api/v1/auth/login/",
            {"email": "admin@caesar.deplois.net", "password": "admin"},
            format="json",
        )
        assert refused.status_code == 401, "the guessable password still works after --rotate"
        assert "Shown once" in output

    def test_rotate_prints_a_password_that_works(self, db, api) -> None:
        """
        A rotation that locked everybody out would be worse than the weak password
        it replaced, so the new one is proven rather than assumed.
        """
        self._run()
        output = self._run(rotate=True)

        password = next(
            line.split("password", 1)[1].strip()
            for line in output.splitlines()
            if line.strip().startswith("password")
        )
        response = api.post(
            "/api/v1/auth/login/",
            {"email": "admin@caesar.deplois.net", "password": password},
            format="json",
        )

        assert response.status_code == 200, response.data
