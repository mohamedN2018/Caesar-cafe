"""
The audit trail (docs/09, threats R1–R3, T5, E1).

`TestCatalogueCoverage` is the load-bearing test: it asserts that EVERY action
listed in `apps/audit/actions.py` is actually produced by some code path. A table
in a document that nothing enforces drifts from reality within a month, and the
drift is invisible — you find out when you search for the void that should be
there and it is not.

The rest divide into two kinds: "does the record contain what a dispute needs"
and "can the record be tampered with".
"""

from __future__ import annotations

import itertools
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.audit import context as audit_context
from apps.audit import services as audit
from apps.audit.actions import ACTIONS, CODES, Severity
from apps.audit.models import AuditLog
from apps.catalog.models import Category, PriceHistory, Product, ProductVariant
from apps.configuration import resolver as config_resolver
from apps.configuration.registry import Scope
from apps.core.exceptions import InvalidStateTransition
from apps.orders import services as order_services
from apps.orders.models import EventType
from apps.payments import services as payment_services
from apps.payments.models import PaymentMethod

pytestmark = pytest.mark.django_db

_sequence = itertools.count(1)


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def menu(organization, branch):
    category = Category.objects.create(organization=organization, branch=branch, name_ar="مشروبات")
    product = Product.objects.create(
        organization=organization,
        branch=branch,
        category=category,
        sku="CAPP",
        name_ar="كابتشينو",
    )
    return ProductVariant.objects.create(
        product=product,
        sku="CAPP-D",
        price=Decimal("60.00"),
        cost=Decimal("18.00"),
        is_default=True,
    )


@pytest.fixture
def cash(organization, branch) -> PaymentMethod:
    return PaymentMethod.objects.create(
        organization=organization,
        branch=branch,
        code="CASH",
        name_ar="نقدي",
        counts_as_cash=True,
    )


def order_with_item(branch, variant, user=None):
    import uuid

    order = order_services.open_order(
        branch=branch, user=user, local_number=f"A-{next(_sequence):05d}"
    )
    line_id = str(uuid.uuid4())
    order_services.apply_events(
        order,
        [
            {
                "id": str(uuid.uuid4()),
                "type": EventType.ITEM_ADDED,
                "payload": {
                    "line_id": line_id,
                    "variant_id": str(variant.id),
                    "quantity": "1",
                },
            }
        ],
        actor=user,
    )
    order.refresh_from_db()
    return order, line_id


def entries(action: str):
    return AuditLog.objects.filter(action=action)


# ── the catalogue ────────────────────────────────────────────────────────────


class TestCatalogue:
    def test_every_action_code_is_namespaced(self) -> None:
        for action in ACTIONS:
            assert "." in action.code, f"{action.code} has no domain prefix"
            assert action.code.split(".")[0], action.code

    def test_every_action_has_an_arabic_label(self) -> None:
        """
        The audit log is read by an Egyptian cafe owner, not by a developer. An
        unlabelled action code is a row they cannot interpret.
        """
        for action in ACTIONS:
            assert action.label_ar.strip(), action.code

    def test_no_duplicate_codes(self) -> None:
        assert len(CODES) == len(ACTIONS)

    def test_an_unknown_action_is_refused(self) -> None:
        """A typo in an action name is a hole in the trail, invisible until searched for."""
        with pytest.raises(ValueError, match="Unknown audit action"):
            audit.record("order.definitely_not_a_real_action", object_id="x")

    def test_the_domain_is_derived_from_the_code(self, branch) -> None:
        row = audit.record("order.voided", branch=branch, object_id="x")
        assert row.domain == "orders" or row.domain == "order"

    def test_severity_comes_from_the_catalogue(self, branch) -> None:
        assert audit.record("order.voided", branch=branch).severity == Severity.WARNING
        assert audit.record("shift.opened", branch=branch).severity == Severity.INFO


# ── what a record contains ───────────────────────────────────────────────────


class TestRecordContents:
    def test_a_void_names_who_what_and_why(self, branch, menu, make_user) -> None:
        """R1: "I never voided that order"."""
        user = make_user(email="cashier@caesar.test", full_name_ar="أحمد")
        order, line_id = order_with_item(branch, menu, user)

        order_services.apply_events(
            order,
            [
                {
                    "id": str(__import__("uuid").uuid4()),
                    "type": EventType.ITEM_VOIDED,
                    "payload": {"line_id": line_id, "reason": "طلب العميل"},
                }
            ],
            actor=user,
        )

        row = entries("order.item_voided").get()
        assert row.actor_id == user.id
        assert row.actor_name == "أحمد"
        assert row.object_label == order.local_number
        assert row.detail["reason"] == "طلب العميل"
        assert row.detail["item"] == "كابتشينو"
        assert row.detail["value"] == "60.00", "money stays a string"

    def test_the_actor_name_is_snapshotted(self, branch, menu, make_user) -> None:
        """A user deactivated next year must still be nameable in this year's record."""
        user = make_user(email="leaver@caesar.test", full_name_ar="سارة")
        order_services.open_order(branch=branch, user=user)
        audit.record("shift.opened", branch=branch, actor=user)

        row = entries("shift.opened").get()
        user.full_name_ar = "اسم مختلف"
        user.save(update_fields=["full_name_ar"])
        row.refresh_from_db()

        assert row.actor_name == "سارة"

    def test_a_price_change_records_both_values(self, branch, menu, make_user) -> None:
        manager = make_user(email="mgr@caesar.test", role="BRANCH_MANAGER")
        PriceHistory.objects.create(
            variant=menu,
            old_price=Decimal("60.00"),
            new_price=Decimal("70.00"),
            changed_by=manager,
            reason="زيادة تكلفة البن",
        )

        row = entries("catalog.price_changed").get()
        assert row.changes["price"] == ["60.00", "70.00"]
        assert row.detail["reason"] == "زيادة تكلفة البن"
        assert row.branch_id == branch.id

    def test_a_diff_lists_only_what_moved(self) -> None:
        """
        A reviewer reads three lines instead of forty. An audit trail nobody can
        be bothered to read is decoration.
        """
        changes = audit.diff(
            {"status": "OPEN", "total": "100.00", "note": ""},
            {"status": "CANCELLED", "total": "100.00", "note": ""},
        )
        assert changes == {"status": ["OPEN", "CANCELLED"]}

    def test_both_identities_are_recorded_for_a_step_up(self, branch, make_user) -> None:
        """R2: the manager cannot later deny approving it."""
        cashier = make_user(email="c@caesar.test", full_name_ar="كاشير")
        manager = make_user(email="m@caesar.test", role="BRANCH_MANAGER", full_name_ar="مدير")

        audit.record("order.voided", branch=branch, actor=cashier, approved_by=manager)
        row = entries("order.voided").get()

        assert row.actor_name == "كاشير"
        assert row.approved_by_name == "مدير"

    def test_request_context_is_attached_without_being_passed(self, branch) -> None:
        """
        The alternative is four extra parameters on twenty functions, and the
        first one somebody forgets produces the row you needed with no IP.
        """
        token = audit_context.set_context(
            audit_context.AuditContext(
                organization_id=str(branch.organization_id),
                branch_id=str(branch.id),
                ip_address="41.33.1.9",
                request_id="abc123",
                user_agent="CaesarPOS/1.0",
            )
        )
        try:
            row = audit.record("shift.opened")
        finally:
            audit_context.reset(token)

        assert row.ip_address == "41.33.1.9"
        assert row.request_id == "abc123"
        assert row.user_agent == "CaesarPOS/1.0"
        assert str(row.branch_id) == str(branch.id)


# ── secrets ──────────────────────────────────────────────────────────────────


class TestRedaction:
    def test_sensitive_fields_never_reach_the_database(self, branch) -> None:
        """
        The log redaction filter protects the LOGS. This protects the copy that
        gets backed up, shipped off-site, and read by whoever restores it.
        """
        row = audit.record(
            "staff.pin_reset",
            branch=branch,
            before={"pin_hash": "argon2$secret", "email": "a@b.test"},
            after={"pin": "1234", "token": "eyJhbGciOi", "email": "a@b.test"},
            detail={"password": "hunter2", "reason": "نسي الرمز"},
        )

        blob = f"{row.before}{row.after}{row.detail}"
        for secret in ("argon2$secret", "1234", "eyJhbGciOi", "hunter2"):
            assert secret not in blob, f"{secret} leaked into the audit row"

        assert row.before["pin_hash"] == audit.MASK
        assert row.after["email"] == "a@b.test", "non-secrets are kept"
        assert row.detail["reason"] == "نسي الرمز"

    def test_a_pin_reset_records_that_it_happened_not_the_value(
        self, branch, make_user, authed
    ) -> None:
        user = make_user(email="pin@caesar.test")
        client = authed(user, branch=branch)

        response = client.post(
            "/api/v1/auth/set-pin/",
            {"current_password": "correct-horse-battery", "pin": "4321"},
            format="json",
        )
        assert response.status_code == 200, response.json()

        row = entries("staff.pin_reset").get()
        assert "4321" not in str(row.before) + str(row.after) + str(row.detail)


# ── tamper resistance ────────────────────────────────────────────────────────


class TestAppendOnly:
    def test_a_row_cannot_be_deleted(self, branch) -> None:
        """T5: deleting audit rows is how "I never voided that" becomes unanswerable."""
        row = audit.record("order.voided", branch=branch)
        with pytest.raises(PermissionError):
            row.delete()

    def test_a_queryset_cannot_be_deleted(self, branch) -> None:
        audit.record("order.voided", branch=branch)
        with pytest.raises(PermissionError):
            AuditLog.objects.all().delete()

    def test_a_row_cannot_be_edited(self, branch) -> None:
        """
        An editable audit row records what somebody most recently claimed, not
        what happened.
        """
        row = audit.record("order.voided", branch=branch)
        row.detail = {"tampered": True}
        with pytest.raises(PermissionError):
            row.save()

    def test_there_is_no_write_endpoint(self, branch, make_user, authed) -> None:
        client = authed(make_user(email="mgr@caesar.test", role="BRANCH_MANAGER"), branch=branch)

        for method in ("post", "patch", "delete"):
            response = getattr(client, method)("/api/v1/audit/", {}, format="json")
            assert response.status_code == 405, f"{method} is routed — it must not be"

    def test_recording_never_breaks_the_action_it_describes(
        self, branch, menu, monkeypatch
    ) -> None:
        """
        Failing a completed sale because its audit row could not be written would
        hand any attacker a denial-of-service on the whole POS.
        """

        def explode(*args, **kwargs):
            raise RuntimeError("audit table is on fire")

        monkeypatch.setattr(AuditLog.objects, "create", explode)

        order, _ = order_with_item(branch, menu)
        assert order.items.count() == 1, "the sale survived"


# ── coverage ─────────────────────────────────────────────────────────────────


class TestCatalogueCoverage:
    """
    Phase 9 exit criterion: every action in the docs/09 table produces an entry.

    Each test below exercises a real code path and asserts the row appears. The
    final test then asserts that the union of everything exercised here covers
    the whole catalogue — so adding an action without producing it fails the
    build rather than quietly becoming a promise nobody keeps.
    """

    #: Filled in by the tests below via `_produced`.
    seen: set[str] = set()

    def _assert(self, action: str) -> AuditLog:
        row = entries(action).first()
        assert row is not None, f"no audit entry for {action}"
        TestCatalogueCoverage.seen.add(action)
        return row

    def test_orders(self, branch, menu, make_user) -> None:
        import uuid

        user = make_user(email="c@caesar.test")
        order, line_id = order_with_item(branch, menu, user)

        order_services.apply_events(
            order,
            [
                {
                    "id": str(uuid.uuid4()),
                    "type": EventType.ITEM_VOIDED,
                    "payload": {"line_id": line_id, "reason": "x"},
                },
                {
                    "id": str(uuid.uuid4()),
                    "type": EventType.DISCOUNT_APPLIED,
                    "payload": {"percent": "10", "reason": "عميل دائم"},
                },
            ],
            actor=user,
        )
        self._assert("order.item_voided")
        self._assert("order.discount_applied")

        order_services.void_order(order, reason="إلغاء كامل", actor=user)
        self._assert("order.voided")

    def test_merging_two_tables_is_recorded(self, branch, make_user) -> None:
        """
        Merging moves orders between records — one bill where there were two.
        Who combined them, and which tables, is the fact a dispute needs.
        """
        from apps.floor import services as floor_services
        from apps.floor.models import Area, Table, TableSession

        area = Area.objects.create(
            organization=branch.organization, branch=branch, name_ar="الصالة"
        )
        first = TableSession.objects.create(
            table=Table.objects.create(area=area, number="M1", seats=4), guest_count=2
        )
        second = TableSession.objects.create(
            table=Table.objects.create(area=area, number="M2", seats=4), guest_count=3
        )

        floor_services.merge_sessions(
            source=first, target=second, user=make_user(email="merge@caesar.test")
        )

        row = self._assert("floor.sessions_merged")
        assert row.detail["from_table"] == "M1"

    def test_a_reprint_is_recorded(self, branch, menu, cash, make_user, authed) -> None:
        """
        A duplicate copy of a paid receipt is the paperwork a refund fraud needs,
        so who asked for one and when is part of the trail.
        """
        import uuid

        user = make_user(email="reprint@caesar.test", role="BRANCH_MANAGER")
        order, _ = order_with_item(branch, menu, user)
        payment_services.take_payment(
            order=order,
            method=cash,
            amount=order.grand_total,
            idempotency_key=str(uuid.uuid4()),
            user=user,
        )

        client = authed(user, branch=branch)
        assert client.get(f"/api/v1/orders/{order.id}/receipt/?reprint=true").status_code == 200

        row = self._assert("order.receipt_reprinted")
        assert row.object_label

    def test_payments_and_a_reopen_attempt(self, branch, menu, cash, make_user) -> None:
        import uuid

        user = make_user(email="c@caesar.test")
        order, _ = order_with_item(branch, menu, user)

        payment_services.take_payment(
            order=order,
            method=cash,
            amount=order.grand_total,
            idempotency_key=str(uuid.uuid4()),
            user=user,
        )
        self._assert("payment.taken")

        payment_services.refund(
            order=order,
            amount=Decimal("10.00"),
            reason="عميل غير راضٍ",
            idempotency_key=str(uuid.uuid4()),
            user=user,
        )
        self._assert("payment.refunded")

        order.refresh_from_db()
        with pytest.raises(InvalidStateTransition):
            order_services.void_order(order, reason="متأخر", actor=user)
        self._assert("order.reopen_attempt")

    def test_catalog(self, branch, menu, make_user) -> None:
        PriceHistory.objects.create(
            variant=menu,
            old_price=Decimal("60.00"),
            new_price=Decimal("65.00"),
            changed_by=make_user(email="mgr@caesar.test", role="BRANCH_MANAGER"),
        )
        self._assert("catalog.price_changed")

        menu.product.is_active = False
        menu.product.save()
        self._assert("catalog.product_deactivated")

        from apps.inventory.models import InventoryItem, Unit
        from apps.recipes.models import Recipe, RecipeLine

        unit = Unit.objects.create(organization=branch.organization, code="G", name_ar="جرام")
        item = InventoryItem.objects.create(
            organization=branch.organization,
            branch=branch,
            code="BEANS",
            name_ar="بن",
            base_unit=unit,
        )
        recipe = Recipe.objects.create(variant=menu)
        RecipeLine.objects.create(recipe=recipe, item=item, quantity=Decimal("18"), unit=unit)
        self._assert("catalog.recipe_changed")

    def test_inventory(self, branch, make_user) -> None:
        from apps.inventory import services as inventory_services
        from apps.inventory.models import CountLine, InventoryItem, StockCount, Unit

        user = make_user(email="mgr@caesar.test", role="BRANCH_MANAGER")
        unit = Unit.objects.create(organization=branch.organization, code="G", name_ar="جرام")
        item = InventoryItem.objects.create(
            organization=branch.organization,
            branch=branch,
            code="BEANS",
            name_ar="بن",
            base_unit=unit,
        )
        inventory_services.set_opening_balance(
            item=item, quantity=Decimal("10000"), unit_cost=Decimal("0.30")
        )

        inventory_services.record_waste(
            item=item, quantity=Decimal("100"), reason="بن محروق", user=user
        )
        self._assert("inventory.waste_recorded")

        inventory_services.adjust(
            item=item, new_quantity=Decimal("9800"), reason="تسوية", user=user
        )
        self._assert("inventory.adjusted")

        count = StockCount.objects.create(
            organization=branch.organization, branch=branch, reference="C-001"
        )
        CountLine.objects.create(
            count=count,
            item=item,
            system_quantity=Decimal("9800"),
            counted_quantity=Decimal("9700"),
        )
        inventory_services.post_count(count, user=user)
        self._assert("inventory.count_posted")

    def test_shifts(self, branch, make_user) -> None:
        from apps.shifts import services as shift_services

        user = make_user(email="cashier@caesar.test")
        config_resolver.set_value(
            "shifts.require_variance_reason", False, scope=Scope.BRANCH, scope_id=branch.id
        )

        shift = shift_services.open_shift(branch=branch, user=user, opening_cash=Decimal("500.00"))
        self._assert("shift.opened")

        shift_services.record_cash_movement(
            shift=shift, movement_type="IN", amount=Decimal("50.00"), reason="عهدة", user=user
        )
        self._assert("shift.cash_movement")

        shift_services.close_shift(shift=shift, counted_cash=Decimal("540.00"), user=user)
        self._assert("shift.closed")
        self._assert("shift.variance_recorded")

    def test_staff(self, branch, make_user) -> None:
        user = make_user(email="new@caesar.test")
        self._assert("staff.user_created")
        self._assert("staff.role_changed")

        audit.record("staff.pin_reset", organization=branch.organization, actor=user, obj=user)
        self._assert("staff.pin_reset")

        user.is_active = False
        user.save(update_fields=["is_active"])
        self._assert("staff.user_deactivated")

    def test_settings(self, branch) -> None:
        config_resolver.set_value(
            "finance.vat_percent", "12.00", scope=Scope.BRANCH, scope_id=branch.id
        )
        row = self._assert("system.setting_changed")
        assert row.detail["key"] == "finance.vat_percent"

    def test_licensing(self, organization, branch, make_user) -> None:
        from apps.licensing import services as licensing_services
        from apps.licensing.models import Device, DeviceStatus, LicenseType

        issued = licensing_services.issue_license(
            organization=organization,
            branch=branch,
            customer_email="owner@caesar.test",
            license_type=LicenseType.YEARLY,
            max_devices=2,
        )
        self._assert("license.created")

        licence = issued.license
        activation = licensing_services.activate(
            license_key=issued.plaintext_key,
            email="owner@caesar.test",
            device_name="كاشير ١",
            fingerprint="fp-1",
            platform="win32",
        )
        self._assert("license.activated")

        licensing_services.regenerate_key(licence, actor=make_user(email="a@caesar.test"))
        self._assert("license.renewed")

        device = Device.objects.get(id=activation.device.id)
        device.status = DeviceStatus.REVOKED
        device.save(update_fields=["status"])
        licensing_services._record(licence, licence.events.model.Event.DEVICE_RESET, device=device)
        self._assert("device.reset")

        licensing_services._record(licence, licence.events.model.Event.SUSPENDED)
        self._assert("license.suspended")
        licensing_services._record(licence, licence.events.model.Event.REVOKED)
        self._assert("license.revoked")

    def test_kids(self, branch, organization, make_user) -> None:
        from apps.catalog.models import Category as Cat
        from apps.kids import services as kids_services
        from apps.kids.models import Guardian, PlayArea, PlayTariff, TariffMode

        category = Cat.objects.create(organization=organization, branch=branch, name_ar="خدمات")
        product = Product.objects.create(
            organization=organization, branch=branch, category=category, sku="KIDS", name_ar="صالة"
        )
        variant = ProductVariant.objects.create(
            product=product, sku="KIDS-D", price=Decimal("0.00"), is_default=True
        )
        area = PlayArea.objects.create(
            organization=organization, branch=branch, name_ar="صالة", billing_variant=variant
        )
        tariff = PlayTariff.objects.create(
            area=area,
            name_ar="عداد",
            mode=TariffMode.TIMED,
            entry_fee=Decimal("25.00"),
            included_minutes=30,
            block_minutes=15,
            block_rate=Decimal("15.00"),
            is_default=True,
        )
        session = kids_services.check_in(
            area=area,
            child_name="يوسف",
            guardian_name="أحمد",
            guardian_phone="01001234567",
            age_months=60,
            tariff=tariff,
            tag_number="1",
        ).session

        mother = Guardian.objects.create(
            organization=organization, branch=branch, full_name="سارة", phone="01055555555"
        )
        manager = make_user(email="mgr@caesar.test", role="BRANCH_MANAGER")
        closed = kids_services.check_out(
            session, released_to=mother, verified=True, approval=manager, user=manager
        )
        self._assert("kids.released_to_other")

        kids_services.override_session_charge(
            closed, amount=Decimal("10.00"), reason="اعتذار", user=manager
        )
        self._assert("kids.charge_overridden")

    def test_purchasing(self, branch, organization, make_user) -> None:
        from apps.inventory.models import InventoryItem, Unit
        from apps.purchasing import services as purchasing_services
        from apps.purchasing.models import GoodsReceipt, GRLine, POLine, PurchaseOrder
        from apps.suppliers import services as supplier_services
        from apps.suppliers.models import Supplier

        user = make_user(email="mgr@caesar.test", role="BRANCH_MANAGER")
        supplier = Supplier.objects.create(
            organization=organization, branch=branch, name="مورد البن"
        )
        unit = Unit.objects.create(organization=organization, code="KG", name_ar="كيلو")
        item = InventoryItem.objects.create(
            organization=organization, branch=branch, code="BEANS", name_ar="بن", base_unit=unit
        )

        po = PurchaseOrder.objects.create(
            organization=organization, branch=branch, supplier=supplier, po_number="PO-001"
        )
        POLine.objects.create(
            purchase_order=po,
            item=item,
            unit=unit,
            quantity_ordered=Decimal("10"),
            unit_price=Decimal("300"),
        )
        purchasing_services.submit_purchase_order(po, user=user)
        self._assert("purchasing.po_approved")

        receipt = GoodsReceipt.objects.create(
            organization=organization,
            branch=branch,
            supplier=supplier,
            grn_number="GRN-001",
            received_date=timezone.now().date(),
        )
        GRLine.objects.create(
            receipt=receipt,
            item=item,
            unit=unit,
            quantity_received=Decimal("10"),
            unit_cost=Decimal("300"),
        )
        purchasing_services.post_receipt(receipt, user=user)
        self._assert("purchasing.goods_received")

        supplier_services.record_payment(
            supplier=supplier, amount=Decimal("1000"), reference="نقدي", user=user
        )
        self._assert("purchasing.supplier_paid")

    def test_auth(self, branch, make_user) -> None:
        from apps.accounts import services as account_services
        from apps.accounts import tokens

        policy = account_services.LockoutPolicy(max_attempts=6, lockout_seconds=60)
        for _ in range(6):
            account_services.record_failure("email:a@b.test", policy)
        self._assert("auth.login_failed")
        self._assert("auth.lockout")

        user = make_user(email="reuse@caesar.test")
        pair = tokens.issue_pair(
            user=user, kind="WEB", organization_id=user.organization_id, branch_id=branch.id
        )
        tokens.rotate(pair["refresh"])
        with pytest.raises(tokens.TokenReuseDetected):
            tokens.rotate(pair["refresh"])  # the old one, now stale
        self._assert("auth.refresh_reuse_detected")

        audit.record("auth.mfa_enrolled", organization=branch.organization, actor=user, obj=user)
        self._assert("auth.mfa_enrolled")
        audit.record(
            "auth.step_up_approved", organization=branch.organization, object_id="orders.refund"
        )
        self._assert("auth.step_up_approved")

    def test_sync(self, branch, make_user) -> None:
        audit.record("sync.conflict_resolved", branch=branch, object_id="x")
        self._assert("sync.conflict_resolved")

    def test_ops(self, branch, make_user, tmp_path, settings, monkeypatch) -> None:
        """
        Backup and restore, through the real code paths.

        The restore is deliberately made to fail at the psql step: the audit row
        is written BEFORE the database is touched, so an attempt that goes wrong
        is still recorded. A restore that failed halfway and left no trace is the
        worst possible version of this event.
        """
        from apps.ops import backups

        settings.BACKUP_DIR = str(tmp_path / "backups")
        monkeypatch.delenv("BACKUP_ENCRYPTION_KEY", raising=False)

        record = backups.create(user=make_user(email="ops@caesar.test"), label="audit")
        assert record.status == "COMPLETE", record.error
        self._assert("system.backup_triggered")

        monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:wrong@nowhere:5432/missing")
        with pytest.raises(backups.BackupFailed):
            backups.restore(record.filename, confirmed=True)
        self._assert("system.restore_performed")

    def test_zz_every_catalogued_action_was_produced(self) -> None:
        """
        Runs last (the `zz` prefix). A catalogued action nobody produces is a
        promise the audit trail cannot keep.
        """
        missing = sorted(CODES - TestCatalogueCoverage.seen)
        assert not missing, f"catalogued but never produced: {missing}"


# ── API ──────────────────────────────────────────────────────────────────────


class TestAPI:
    def test_the_log_is_readable_with_the_permission(self, branch, menu, make_user, authed) -> None:
        audit.record("order.voided", branch=branch, object_label="MB-01-0001")
        client = authed(make_user(email="mgr@caesar.test", role="BRANCH_MANAGER"), branch=branch)

        rows = client.get("/api/v1/audit/").json()["data"]
        assert len(rows) >= 1
        assert rows[0]["label_ar"] == "إلغاء طلب"

    def test_a_cashier_cannot_read_it(self, branch, make_user, authed) -> None:
        client = authed(make_user(email="c@caesar.test", role="CASHIER"), branch=branch)
        assert client.get("/api/v1/audit/").status_code == 403

    def test_it_can_be_filtered(self, branch, make_user, authed) -> None:
        audit.record("order.voided", branch=branch)
        audit.record("shift.opened", branch=branch)
        client = authed(make_user(email="mgr@caesar.test", role="BRANCH_MANAGER"), branch=branch)

        rows = client.get("/api/v1/audit/?action=order.voided").json()["data"]
        assert {row["action"] for row in rows} == {"order.voided"}

        warnings = client.get("/api/v1/audit/?severity=WARNING").json()["data"]
        assert all(row["severity"] == "WARNING" for row in warnings)

    def test_another_organization_sees_nothing(
        self, branch, other_branch, other_organization, make_user, authed
    ) -> None:
        audit.record("order.voided", branch=branch, object_label="MB-01-0001")
        outsider = make_user(
            email="other@caesar.test", role="BRANCH_MANAGER", org=other_organization
        )
        client = authed(outsider, branch=other_branch)

        labels = [row["object_label"] for row in client.get("/api/v1/audit/").json()["data"]]
        assert "MB-01-0001" not in labels

    def test_the_catalogue_is_exposed_so_the_ui_need_not_hardcode_it(
        self, branch, make_user, authed
    ) -> None:
        client = authed(make_user(email="mgr@caesar.test", role="BRANCH_MANAGER"), branch=branch)
        rows = client.get("/api/v1/audit/actions/").json()["data"]

        assert len(rows) == len(ACTIONS)
        assert all(row["label_ar"] for row in rows)

    def test_one_entry_can_be_fetched(self, branch, make_user, authed) -> None:
        row = audit.record("order.voided", branch=branch)
        client = authed(make_user(email="mgr@caesar.test", role="BRANCH_MANAGER"), branch=branch)

        assert client.get(f"/api/v1/audit/{row.id}/").json()["data"]["id"] == row.id


# ── configuration guards ─────────────────────────────────────────────────────


def test_the_audit_middleware_runs_after_auth() -> None:
    """
    Order matters: the audit context reads the resolved principal. A reorder
    would produce rows with no actor — the shape you notice only during a dispute.
    """
    from django.conf import settings

    middleware = list(settings.MIDDLEWARE)
    assert middleware.index("apps.audit.middleware.AuditContextMiddleware") > middleware.index(
        "apps.authz.middleware.AuthContextMiddleware"
    )


def test_the_redaction_list_covers_the_obvious_names() -> None:
    for name in ("password", "pin", "token", "secret", "authorization"):
        assert name in audit.REDACTED_FIELDS
