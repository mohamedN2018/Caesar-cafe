"""
Errors that carry the status the client branches on.

`tests/test_architecture_guards.py` proves statically that no call site passes a
keyword `AppError` cannot take. This proves the consequence at runtime: the
paths that were raising `TypeError` — and therefore answering 500 with no
machine code — now answer with the status and the code the clients are written
against.

Every one of these was reachable in production and none of them had a test.
"""

from __future__ import annotations

import pytest

from apps.core.exceptions import AppError, ConflictError, NotFoundError

pytestmark = pytest.mark.django_db


class TestAppErrorCarriesItsStatus:
    def test_the_default_is_400(self) -> None:
        assert AppError("خطأ").status_code == 400

    def test_an_explicit_status_is_honoured(self) -> None:
        assert AppError("موقوف", code="DEVICE_REVOKED", status_code=403).status_code == 403

    def test_the_class_default_is_not_mutated(self) -> None:
        """
        Setting it per instance must not reach the class, or one 403 would turn
        every later AppError in the process into a 403.
        """
        AppError("موقوف", status_code=403)
        assert AppError("خطأ").status_code == 400

    def test_subclasses_keep_their_own_status(self) -> None:
        assert ConflictError().status_code == 409
        assert NotFoundError().status_code == 404


class TestBranchRequired:
    """
    A fresh web login has no branch selected yet. Asking for a branch-scoped
    report then is a 400 telling the user to pick one — not a 500.
    """

    def test_a_report_without_a_branch_answers_400(self, make_user, authed) -> None:
        client = authed(make_user(role="BRANCH_MANAGER"), branch=None)

        response = client.get("/api/v1/reports/sales/summary/")

        assert response.status_code == 400
        assert response.json()["code"] == "BRANCH_REQUIRED"

    def test_creating_a_branch_scoped_record_without_a_branch_answers_400(
        self, make_user, authed
    ) -> None:
        client = authed(make_user(role="BRANCH_MANAGER"), branch=None)

        response = client.post("/api/v1/suppliers/", {"name": "مورد"}, format="json")

        assert response.status_code == 400
        assert response.json()["code"] == "BRANCH_REQUIRED"


class TestRevokedDeviceBackstop:
    """
    Revoking a device normally stops its token resolving at all, so the push is
    rejected with a 401 before the view runs. This is the view's own check — the
    one that matters when a revocation lands mid-request — and until the
    `status_code` fix it raised TypeError instead of refusing.
    """

    def test_the_view_refuses_a_device_revoked_after_its_token_was_issued(
        self, monkeypatch, branch
    ) -> None:
        import uuid

        from apps.licensing.models import Device, DeviceStatus, License, LicenseType
        from apps.sync import views

        licence = License.objects.create(
            organization=branch.organization,
            branch=branch,
            key_hash=uuid.uuid4().hex,
            key_prefix="QSR-TEST",
            customer_email="owner@caesar.test",
            license_type=LicenseType.YEARLY,
            max_devices=3,
        )
        device = Device.objects.create(
            license=licence,
            branch=branch,
            device_name="كاشير ١",
            secret_hash="x" * 32,
            status=DeviceStatus.REVOKED,
        )

        class FakePrincipal:
            device_id = device.id
            branch_id = branch.id
            organization_id = branch.organization_id
            user_id = None

        monkeypatch.setattr(views, "auth_context", lambda _request: FakePrincipal())

        with pytest.raises(AppError) as raised:
            views._device(object())

        assert raised.value.code == "DEVICE_REVOKED"
        assert raised.value.status_code == 403, "not a 500 — the Desktop branches on this"
