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


class TestDuplicateRows:
    """
    A constraint violation is the operator's mistake, not a crash.

    Typing a product code that already exists returned **500** — "حدث خطأ غير
    متوقع. تم تسجيل المشكلة." — a message that says the software broke, about the
    one class of mistake that is entirely the user's to fix and takes one
    keystroke to correct.

    It cannot be caught earlier. DRF's uniqueness validators only cover fields the
    serializer HAS, and every one of these constraints includes `branch` or
    `product` — columns injected from the authenticated principal and deliberately
    never accepted from a request body. The check cannot run before the insert.
    """

    def test_a_duplicate_sku_is_a_409_not_a_500(self, authed, make_user, branch, organization):
        from apps.catalog.models import Category

        category = Category.objects.create(
            organization=organization, branch=branch, name_ar="مشروبات"
        )
        client = authed(make_user(role="SUPER_ADMIN"), branch=branch)
        body = {"name_ar": "لاتيه", "sku": "DUP-1", "category": str(category.id)}

        assert client.post("/api/v1/catalog/products/", body, format="json").status_code == 201

        clash = client.post("/api/v1/catalog/products/", body, format="json")

        assert clash.status_code == 409
        payload = clash.json()
        assert payload["code"] == "DUPLICATE"
        # The message names the FIELD, so the form can point at the box to fix.
        assert "sku" in payload["errors"]
        assert "مستخدم بالفعل" in payload["message"]

    def test_a_real_integrity_failure_is_still_a_500(self, monkeypatch):
        """
        Only duplicates are translated.

        A foreign-key or NOT NULL violation is not something a message can help
        with, and dressing it as a 409 would tell an operator to change something
        they did not type. It stays a 500 and stays logged.
        """
        from django.db import IntegrityError

        from apps.core.exceptions import _duplicate_or_conflict

        with pytest.raises(IntegrityError):
            _duplicate_or_conflict(IntegrityError('null value in column "branch_id"'))
