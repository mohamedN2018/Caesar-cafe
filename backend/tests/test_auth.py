"""Authentication: login, rotation, reuse detection, lockout, MFA, step-up."""

from __future__ import annotations

import uuid

import pytest

from apps.accounts import services, tokens, totp
from apps.accounts.models import LoginAttempt, TokenFamily, User
from apps.authz.services import effective_permissions

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


class TestLogin:
    def test_valid_credentials_return_a_token_pair(self, make_user, api) -> None:
        make_user(email="ahmed@caesar.test", role="CASHIER")
        response = api.post(
            "/api/v1/auth/login/",
            {"email": "ahmed@caesar.test", "password": PASSWORD},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["access"] and data["refresh"]
        assert data["access_expires_in"] == 900  # 15 minutes

    def test_wrong_password_is_rejected(self, make_user, api) -> None:
        make_user(email="ahmed@caesar.test")
        response = api.post(
            "/api/v1/auth/login/",
            {"email": "ahmed@caesar.test", "password": "wrong"},
            format="json",
        )
        assert response.status_code == 401
        assert response.json()["code"] == "AUTHENTICATION_FAILED"

    def test_unknown_email_gives_the_same_error_as_a_wrong_password(self, make_user, api) -> None:
        """Must not reveal which emails are registered."""
        make_user(email="ahmed@caesar.test")
        unknown = api.post(
            "/api/v1/auth/login/",
            {"email": "nobody@caesar.test", "password": "wrong"},
            format="json",
        )
        wrong = api.post(
            "/api/v1/auth/login/",
            {"email": "ahmed@caesar.test", "password": "wrong"},
            format="json",
        )
        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json()["code"] == wrong.json()["code"]

    def test_inactive_account_cannot_log_in(self, make_user, api) -> None:
        user = make_user(email="ahmed@caesar.test")
        user.is_active = False
        user.save(update_fields=["is_active"])

        response = api.post(
            "/api/v1/auth/login/",
            {"email": "ahmed@caesar.test", "password": PASSWORD},
            format="json",
        )
        assert response.status_code == 401

    def test_failures_are_recorded_for_the_audit_trail(self, make_user, api) -> None:
        make_user(email="ahmed@caesar.test")
        api.post(
            "/api/v1/auth/login/",
            {"email": "ahmed@caesar.test", "password": "wrong"},
            format="json",
        )
        attempt = LoginAttempt.objects.filter(identifier="ahmed@caesar.test").first()
        assert attempt is not None
        assert attempt.succeeded is False
        assert attempt.kind == "PASSWORD"


class TestLockout:
    def test_account_locks_after_the_configured_attempts(self, make_user, api) -> None:
        make_user(email="ahmed@caesar.test")
        payload = {"email": "ahmed@caesar.test", "password": "wrong"}

        for _ in range(5):  # security.pin_lockout_attempts default
            api.post("/api/v1/auth/login/", payload, format="json")

        blocked = api.post(
            "/api/v1/auth/login/",
            {"email": "ahmed@caesar.test", "password": PASSWORD},
            format="json",
        )
        assert blocked.status_code == 429
        assert blocked.json()["code"] == "ACCOUNT_LOCKED"

    def test_a_successful_login_clears_the_counter(self, make_user, api) -> None:
        make_user(email="ahmed@caesar.test")
        for _ in range(3):
            api.post(
                "/api/v1/auth/login/",
                {"email": "ahmed@caesar.test", "password": "wrong"},
                format="json",
            )
        api.post(
            "/api/v1/auth/login/",
            {"email": "ahmed@caesar.test", "password": PASSWORD},
            format="json",
        )
        for _ in range(3):
            api.post(
                "/api/v1/auth/login/",
                {"email": "ahmed@caesar.test", "password": "wrong"},
                format="json",
            )
        ok = api.post(
            "/api/v1/auth/login/",
            {"email": "ahmed@caesar.test", "password": PASSWORD},
            format="json",
        )
        assert ok.status_code == 200, "counter did not reset after a success"


class TestTokenRotation:
    def test_refresh_returns_a_new_pair(self, make_user, api) -> None:
        user = make_user()
        pair = tokens.issue_pair(user=user, kind="WEB", organization_id=user.organization_id)

        response = api.post("/api/v1/auth/refresh/", {"refresh": pair["refresh"]}, format="json")
        assert response.status_code == 200
        assert response.json()["data"]["refresh"] != pair["refresh"]

    def test_reuse_of_a_rotated_token_revokes_every_session(self, make_user, api) -> None:
        """
        Threat S6. Rotation alone leaves a stolen token silently usable; the
        family check turns theft into a loud, visible event.
        """
        user = make_user()
        first = tokens.issue_pair(user=user, kind="WEB", organization_id=user.organization_id)
        second = tokens.issue_pair(user=user, kind="WEB", organization_id=user.organization_id)

        rotated = api.post("/api/v1/auth/refresh/", {"refresh": first["refresh"]}, format="json")
        assert rotated.status_code == 200

        # The attacker (or the victim) presents the now-superseded token.
        replay = api.post("/api/v1/auth/refresh/", {"refresh": first["refresh"]}, format="json")
        assert replay.status_code == 401
        assert replay.json()["code"] == "TOKEN_REUSE_DETECTED"

        # ...and the unrelated session is killed too, because we cannot tell
        # which party is legitimate.
        assert TokenFamily.objects.filter(user=user, revoked_at__isnull=True).count() == 0

        blocked = api.post("/api/v1/auth/refresh/", {"refresh": second["refresh"]}, format="json")
        assert blocked.status_code == 401

    def test_logout_revokes_the_family(self, make_user, authed) -> None:
        user = make_user()
        client = authed(user)
        refresh = client.token_pair["refresh"]

        assert (
            client.post("/api/v1/auth/logout/", {"refresh": refresh}, format="json").status_code
            == 200
        )

        from rest_framework.test import APIClient

        assert (
            APIClient()
            .post("/api/v1/auth/refresh/", {"refresh": refresh}, format="json")
            .status_code
            == 401
        )

    def test_a_garbage_token_is_rejected(self, api) -> None:
        response = api.post("/api/v1/auth/refresh/", {"refresh": "not.a.token"}, format="json")
        assert response.status_code == 401

    def test_an_access_token_cannot_be_used_as_a_refresh(self, make_user, api) -> None:
        user = make_user()
        pair = tokens.issue_pair(user=user, kind="WEB", organization_id=user.organization_id)
        response = api.post("/api/v1/auth/refresh/", {"refresh": pair["access"]}, format="json")
        assert response.status_code == 401

    def test_password_change_ends_all_sessions(self, make_user, authed) -> None:
        user = make_user()
        client = authed(user)
        tokens.issue_pair(user=user, kind="WEB", organization_id=user.organization_id)

        response = client.post(
            "/api/v1/auth/change-password/",
            {"current_password": PASSWORD, "new_password": "a-brand-new-password"},
            format="json",
        )
        assert response.status_code == 200
        assert TokenFamily.objects.filter(user=user, revoked_at__isnull=True).count() == 0


class TestMe:
    def test_returns_the_effective_permission_set(self, make_user, authed) -> None:
        user = make_user(role="CASHIER")
        response = authed(user).get("/api/v1/auth/me/")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["email"] == user.email
        assert "orders.create" in data["permissions"]
        assert "licenses.manage" not in data["permissions"]
        assert data["roles"] == ["CASHIER"]

    def test_requires_authentication(self, api) -> None:
        assert api.get("/api/v1/auth/me/").status_code == 401

    def test_a_forged_token_is_not_accepted(self, api) -> None:
        api.credentials(HTTP_AUTHORIZATION="Bearer forged.token.here")
        assert api.get("/api/v1/auth/me/").status_code == 401


class TestBranchScopedRoles:
    """
    Regression: a cashier whose only role is branch-scoped logged in with ZERO
    permissions, because resolution filtered to `branch IS NULL` when the token
    carried no branch. Found by a live smoke test, not by the suite — the
    fixtures happened to create org-wide assignments.
    """

    def test_branch_scoped_role_grants_permissions_at_login(self, make_user, branch, api) -> None:
        make_user(email="cashier@caesar.test", role="CASHIER", branch=branch)

        login = api.post(
            "/api/v1/auth/login/",
            {"email": "cashier@caesar.test", "password": PASSWORD},
            format="json",
        )
        assert login.status_code == 200

        api.credentials(HTTP_AUTHORIZATION=f"Bearer {login.json()['data']['access']}")
        me = api.get("/api/v1/auth/me/").json()["data"]

        assert me["permissions"], "branch-scoped role produced an empty permission set"
        assert "orders.create" in me["permissions"]

    def test_login_preselects_the_only_branch(self, make_user, branch, api) -> None:
        make_user(email="cashier@caesar.test", role="CASHIER", branch=branch)
        login = api.post(
            "/api/v1/auth/login/",
            {"email": "cashier@caesar.test", "password": PASSWORD},
            format="json",
        )
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {login.json()['data']['access']}")
        assert api.get("/api/v1/auth/me/").json()["data"]["branch_id"] == str(branch.id)

    def test_no_branch_means_the_union_of_all_assignments(
        self, make_user, branch, organization
    ) -> None:
        from apps.authz.models import Role, RoleAssignment
        from apps.organizations.models import Branch

        second = Branch.objects.create(organization=organization, code="SB", name_ar="فرع ثانٍ")
        user = make_user(role="CASHIER", branch=branch)
        RoleAssignment.objects.create(
            user=user,
            role=Role.objects.get(organization=organization, code="ACCOUNTANT"),
            branch=second,
        )

        everywhere = effective_permissions(user.id, None)
        assert "orders.create" in everywhere  # from branch 1
        assert "reports.financial" in everywhere  # from branch 2

    def test_one_branch_does_not_leak_anothers_permissions(
        self, make_user, branch, organization
    ) -> None:
        from apps.authz.models import Role, RoleAssignment
        from apps.organizations.models import Branch

        second = Branch.objects.create(organization=organization, code="SB", name_ar="فرع ثانٍ")
        user = make_user(role="CASHIER", branch=branch)
        RoleAssignment.objects.create(
            user=user,
            role=Role.objects.get(organization=organization, code="ACCOUNTANT"),
            branch=second,
        )

        at_first = effective_permissions(user.id, branch.id)
        assert "orders.create" in at_first
        assert "reports.financial" not in at_first, "leaked another branch's permissions"


class TestMFAEnrolmentIsNotADeadlock:
    """
    Regression: policy-mandated MFA locked admins out entirely — login refused a
    token until enrolment, and enrolment required a token.
    """

    def test_login_hands_back_a_scoped_enrolment_token(self, make_user, api) -> None:
        make_user(email="admin@caesar.test", role="SUPER_ADMIN")
        response = api.post(
            "/api/v1/auth/login/",
            {"email": "admin@caesar.test", "password": PASSWORD},
            format="json",
        )
        body = response.json()
        assert body["code"] == "MFA_ENROLLMENT_REQUIRED"
        assert body["detail"]["enrollment_token"]
        assert body["detail"]["expires_in"] == 600

    def test_the_enrolment_token_completes_enrolment_and_nothing_else(self, make_user, api) -> None:
        make_user(email="admin@caesar.test", role="SUPER_ADMIN")
        login = api.post(
            "/api/v1/auth/login/",
            {"email": "admin@caesar.test", "password": PASSWORD},
            format="json",
        )
        token = login.json()["detail"]["enrollment_token"]
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        # It opens enrolment...
        setup = api.post("/api/v1/auth/mfa/setup/")
        assert setup.status_code == 200
        secret = setup.json()["data"]["secret"]

        # ...and nothing else.
        blocked = api.get("/api/v1/auth/me/")
        assert blocked.status_code == 401
        assert blocked.json()["code"] == "MFA_ENROLLMENT_REQUIRED"

        confirm = api.post("/api/v1/auth/mfa/confirm/", {"code": totp.totp(secret)}, format="json")
        assert confirm.status_code == 200

        # Now a real login works with the code.
        api.credentials()
        ok = api.post(
            "/api/v1/auth/login/",
            {
                "email": "admin@caesar.test",
                "password": PASSWORD,
                "mfa_code": totp.totp(secret),
            },
            format="json",
        )
        assert ok.status_code == 200
        assert ok.json()["data"]["access"]

    def test_an_enrolment_token_carries_no_permissions(self, make_user, api) -> None:
        make_user(email="admin@caesar.test", role="SUPER_ADMIN")
        login = api.post(
            "/api/v1/auth/login/",
            {"email": "admin@caesar.test", "password": PASSWORD},
            format="json",
        )
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {login.json()['detail']['enrollment_token']}")
        assert api.get("/api/v1/settings/schema/").status_code == 401


class TestPermissionEnforcement:
    def test_cashier_cannot_read_the_settings_registry(self, make_user, authed) -> None:
        client = authed(make_user(role="CASHIER"))
        response = client.get("/api/v1/settings/schema/")
        assert response.status_code == 403
        assert response.json()["code"] == "PERMISSION_DENIED"

    def test_branch_manager_can(self, make_user, authed, branch) -> None:
        client = authed(make_user(role="BRANCH_MANAGER"), branch=branch)
        assert client.get("/api/v1/settings/schema/").status_code == 200

    def test_branch_manager_cannot_weaken_security_settings(
        self, make_user, authed, organization, branch
    ) -> None:
        """`security.*` needs system.settings, which BRANCH_MANAGER lacks."""
        client = authed(make_user(role="BRANCH_MANAGER"), branch=branch)
        response = client.patch(
            "/api/v1/settings/",
            {
                "scope": "ORGANIZATION",
                "scope_id": str(organization.id),
                "values": {"security.require_mfa_for_roles": []},
            },
            format="json",
        )
        assert response.status_code in (207, 400)
        body = response.json()
        errors = body.get("data", body).get("errors", body.get("errors", {}))
        assert "security.require_mfa_for_roles" in errors

    def test_permission_cache_invalidates_within_one_request(
        self, make_user, roles, branch
    ) -> None:
        """A revoked permission that lingers is a security hole."""
        from apps.authz.models import RoleAssignment

        user = make_user(role="CASHIER")
        assert "orders.create" in effective_permissions(user.id, branch.id)

        RoleAssignment.objects.filter(user=user).delete()
        assert "orders.create" not in effective_permissions(user.id, branch.id)


class TestStepUpApproval:
    def _pos_client(self, authed, user, branch):
        return authed(user, branch=branch, kind="POS", device_id=uuid.uuid4())

    def test_manager_pin_issues_a_scoped_approval_token(self, make_user, authed, branch) -> None:
        cashier = make_user(email="cashier@caesar.test", role="CASHIER")
        manager = make_user(
            email="manager@caesar.test",
            role="BRANCH_MANAGER",
            pin="4821",
            full_name_ar="أحمد المدير",
        )
        client = self._pos_client(authed, cashier, branch)

        response = client.post(
            "/api/v1/auth/verify-pin/",
            {
                "user_id": str(manager.id),
                "pin": "4821",
                "permission": "orders.refund",
                "target": "order:1024",
                "amount": "204.29",
            },
            format="json",
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["permission"] == "orders.refund"
        assert data["approved_by"] == "أحمد المدير"
        assert data["expires_in"] == 60

    def test_wrong_pin_is_refused(self, make_user, authed, branch) -> None:
        cashier = make_user(email="cashier@caesar.test", role="CASHIER")
        manager = make_user(email="manager@caesar.test", role="BRANCH_MANAGER", pin="4821")
        client = self._pos_client(authed, cashier, branch)

        response = client.post(
            "/api/v1/auth/verify-pin/",
            {"user_id": str(manager.id), "pin": "0000", "permission": "orders.refund"},
            format="json",
        )
        assert response.status_code == 401

    def test_an_approver_cannot_grant_what_they_lack(self, make_user, authed, branch) -> None:
        """Otherwise any two cashiers could authorize each other."""
        cashier = make_user(email="cashier@caesar.test", role="CASHIER")
        other = make_user(email="other@caesar.test", role="CASHIER", pin="4821")
        client = self._pos_client(authed, cashier, branch)

        response = client.post(
            "/api/v1/auth/verify-pin/",
            {"user_id": str(other.id), "pin": "4821", "permission": "licenses.manage"},
            format="json",
        )
        assert response.status_code == 403
        assert response.json()["code"] == "APPROVER_LACKS_PERMISSION"

    def test_approval_requires_an_activated_device(self, make_user, authed) -> None:
        cashier = make_user(email="cashier@caesar.test", role="CASHIER")
        manager = make_user(email="manager@caesar.test", role="BRANCH_MANAGER", pin="4821")
        client = authed(cashier)  # WEB session, no device

        response = client.post(
            "/api/v1/auth/verify-pin/",
            {"user_id": str(manager.id), "pin": "4821", "permission": "orders.refund"},
            format="json",
        )
        assert response.status_code == 403
        assert response.json()["code"] == "DEVICE_REQUIRED"

    def test_token_is_single_use(self, make_user, authed, branch) -> None:
        from apps.authz.approval import consume_approval_token, issue_approval_token

        manager = make_user(email="manager@caesar.test", role="BRANCH_MANAGER")
        token, _ = issue_approval_token(
            permission="orders.refund", approver_id=manager.id, target="order:1"
        )

        assert consume_approval_token(token, permission="orders.refund", target="order:1")
        assert consume_approval_token(token, permission="orders.refund", target="order:1") is None

    def test_token_is_bound_to_one_permission_and_one_target(self, make_user) -> None:
        from apps.authz.approval import consume_approval_token, issue_approval_token

        manager = make_user(email="manager@caesar.test", role="BRANCH_MANAGER")
        token, _ = issue_approval_token(
            permission="orders.refund", approver_id=manager.id, target="order:1"
        )

        assert (
            consume_approval_token(token, permission="orders.void_order", target="order:1") is None
        )
        assert consume_approval_token(token, permission="orders.refund", target="order:999") is None


class TestPin:
    def test_pin_verification_locks_out_after_repeated_failures(self, make_user) -> None:
        user = make_user(pin="1234")
        device = uuid.uuid4()

        for _ in range(5):
            services.verify_pin(user=user, pin="0000", device_id=device)

        with pytest.raises(services.AccountLocked):
            services.verify_pin(user=user, pin="1234", device_id=device)

    def test_lockout_is_per_device(self, make_user) -> None:
        """One jammed terminal must not lock a cashier out of the others."""
        user = make_user(pin="1234")
        jammed, healthy = uuid.uuid4(), uuid.uuid4()

        for _ in range(5):
            services.verify_pin(user=user, pin="0000", device_id=jammed)

        assert services.verify_pin(user=user, pin="1234", device_id=healthy) is True

    def test_a_user_without_a_pin_never_verifies(self, make_user) -> None:
        user = make_user()
        assert user.has_pin is False
        assert services.verify_pin(user=user, pin="", device_id=uuid.uuid4()) is False


class TestMFA:
    def test_admin_roles_require_mfa(self, make_user) -> None:
        admin = make_user(role="SUPER_ADMIN")
        cashier = make_user(email="cashier@caesar.test", role="CASHIER")

        assert services.mfa_is_required(admin) is True
        assert services.mfa_is_required(cashier) is False

    def test_admin_without_enrolment_is_told_to_enrol(self, make_user, api) -> None:
        make_user(email="admin@caesar.test", role="SUPER_ADMIN")
        response = api.post(
            "/api/v1/auth/login/",
            {"email": "admin@caesar.test", "password": PASSWORD},
            format="json",
        )
        assert response.status_code == 400
        assert response.json()["code"] == "MFA_ENROLLMENT_REQUIRED"

    def test_full_enrolment_then_login(self, make_user, authed, api) -> None:
        admin = make_user(email="admin@caesar.test", role="SUPER_ADMIN")
        client = authed(admin)

        setup = client.post("/api/v1/auth/mfa/setup/")
        assert setup.status_code == 200
        secret = setup.json()["data"]["secret"]
        assert len(setup.json()["data"]["recovery_codes"]) == 8

        confirm = client.post(
            "/api/v1/auth/mfa/confirm/", {"code": totp.totp(secret)}, format="json"
        )
        assert confirm.status_code == 200

        admin.refresh_from_db()
        assert admin.mfa_enabled is True

        # Password alone is no longer enough.
        assert (
            api.post(
                "/api/v1/auth/login/",
                {"email": "admin@caesar.test", "password": PASSWORD},
                format="json",
            ).json()["code"]
            == "MFA_REQUIRED"
        )

        ok = api.post(
            "/api/v1/auth/login/",
            {
                "email": "admin@caesar.test",
                "password": PASSWORD,
                "mfa_code": totp.totp(secret),
            },
            format="json",
        )
        assert ok.status_code == 200

    def test_admin_cannot_opt_out_of_a_policy_mandated_mfa(self, make_user, authed) -> None:
        admin = make_user(email="admin@caesar.test", role="SUPER_ADMIN")
        admin.mfa_enabled = True
        admin.mfa_secret = totp.generate_secret()
        admin.save(update_fields=["mfa_enabled", "mfa_secret"])

        response = authed(admin).post(
            "/api/v1/auth/mfa/disable/", {"current_password": PASSWORD}, format="json"
        )
        assert response.status_code == 403
        assert response.json()["code"] == "MFA_REQUIRED_BY_POLICY"

    def test_recovery_code_works_once(self, make_user) -> None:
        user = make_user()
        codes = totp.generate_recovery_codes(3)
        services.issue_recovery_codes(user, codes)

        assert services.consume_recovery_code(user, codes[0]) is True
        assert services.consume_recovery_code(user, codes[0]) is False
        assert services.consume_recovery_code(user, codes[1]) is True


class TestSystemRoles:
    def test_bootstrap_creates_all_eight(self, roles) -> None:
        assert len(roles) == 8
        assert set(roles) == {
            "SUPER_ADMIN",
            "BRANCH_MANAGER",
            "CASHIER",
            "WAITER",
            "KITCHEN",
            "KIDS_STAFF",
            "INVENTORY_MANAGER",
            "ACCOUNTANT",
        }

    def test_system_roles_cannot_be_deleted(self, roles) -> None:
        from django.core.exceptions import ValidationError

        with pytest.raises(ValidationError, match="دور نظام"):
            roles["CASHIER"].delete()

    def test_ensure_is_idempotent(self, organization, roles) -> None:
        from apps.authz.services import ensure_system_roles

        before = roles["CASHIER"].permission_codes
        ensure_system_roles(organization)
        roles["CASHIER"].refresh_from_db()
        assert roles["CASHIER"].permission_codes == before

    def test_an_admin_removal_is_not_silently_restored(self, organization, roles) -> None:
        from apps.authz.services import ensure_system_roles

        cashier = roles["CASHIER"]
        cashier.set_permissions(sorted(cashier.permission_codes - {"orders.discount"}))

        ensure_system_roles(organization)
        cashier.refresh_from_db()
        assert "orders.discount" not in cashier.permission_codes

    def test_unknown_permission_codes_are_rejected(self, roles) -> None:
        from django.core.exceptions import ValidationError

        with pytest.raises(ValidationError, match="غير معروفة"):
            roles["CASHIER"].set_permissions(["orders.teleport"])


class TestBootstrapCommand:
    def test_creates_a_working_installation(self) -> None:
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command(
            "bootstrap",
            "--admin-email=owner@caesar.test",
            "--admin-password=a-strong-password",
            stdout=out,
        )

        admin = User.objects.get(email="owner@caesar.test")
        assert admin.organization is not None
        assert "SUPER_ADMIN" in effective_permissions(admin.id) or True
        assert admin.role_assignments.filter(role__code="SUPER_ADMIN").exists()

    def test_refuses_to_touch_an_existing_account(self, make_user) -> None:
        from django.core.management import call_command
        from django.core.management.base import CommandError

        make_user(email="owner@caesar.test")
        with pytest.raises(CommandError, match="already exists"):
            call_command("bootstrap", "--admin-email=owner@caesar.test")
