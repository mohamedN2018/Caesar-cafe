"""
Registry and resolution tests.

The Phase 1 exit criterion these enforce: a setting registered in code resolves
by scope, validates, and audits — with no migration and no frontend change.
"""

from __future__ import annotations

import uuid
from datetime import time
from decimal import Decimal

import pytest

from apps.configuration import resolver
from apps.configuration.models import SettingChangeLog, SettingValue
from apps.configuration.registry import (
    Range,
    Scope,
    SettingDefinition,
    SettingType,
    SubsetOf,
    ValidationFailed,
    registry,
)
from apps.organizations.models import Branch, Organization

pytestmark = pytest.mark.django_db


@pytest.fixture
def org(organization: Organization) -> Organization:
    return organization


@pytest.fixture
def context(organization: Organization, branch: Branch) -> resolver.ScopeContext:
    return resolver.ScopeContext(organization_id=organization.id, branch_id=branch.id)


class TestRegistry:
    def test_catalog_is_populated(self) -> None:
        assert len(registry.all()) > 40

    def test_every_definition_is_well_formed(self) -> None:
        problems = []
        for key, definition in registry.all().items():
            if not definition.label_ar:
                problems.append(f"{key}: missing Arabic label")
            if not definition.group:
                problems.append(f"{key}: missing group")
            if definition.type is SettingType.ENUM and not definition.choices:
                problems.append(f"{key}: ENUM without choices")
            if definition.type is SettingType.ENUM and definition.default not in definition.choices:
                problems.append(f"{key}: default not among choices")
        assert not problems, "\n".join(problems)

    def test_defaults_survive_their_own_validators(self) -> None:
        """A shipped default that fails validation would break a fresh install."""
        for key, definition in registry.all().items():
            try:
                definition.validate(definition.default)
            except ValidationFailed as exc:  # pragma: no cover
                pytest.fail(f"{key}: default {definition.default!r} is invalid — {exc}")

    def test_duplicate_registration_is_rejected(self) -> None:
        with pytest.raises(RuntimeError, match="already registered"):
            registry.register(
                SettingDefinition(
                    key="finance.vat_percent",
                    type=SettingType.DECIMAL,
                    default=Decimal("1"),
                    scope=Scope.BRANCH,
                    group="finance",
                    label_ar="مكرر",
                )
            )

    def test_unknown_key_names_where_to_fix_it(self) -> None:
        with pytest.raises(KeyError, match=r"definitions\.py"):
            registry.get("finance.does_not_exist")


class TestParsingAndValidation:
    def test_decimal_never_becomes_float(self) -> None:
        definition = registry.get("finance.vat_percent")
        value = definition.clean("14.00")
        assert isinstance(value, Decimal)
        assert value == Decimal("14.00")

    def test_time_parses_from_string(self) -> None:
        assert registry.get("finance.business_day_start").clean("04:00") == time(4, 0)

    def test_boolean_accepts_common_truthy_strings(self) -> None:
        definition = registry.get("finance.vat_enabled")
        assert definition.clean("true") is True
        assert definition.clean("0") is False

    def test_range_validator_rejects_out_of_bounds(self) -> None:
        with pytest.raises(ValidationFailed):
            registry.get("finance.vat_percent").clean("140")

    def test_enum_validator_rejects_unknown_choice(self) -> None:
        with pytest.raises(ValidationFailed):
            registry.get("floor.service_mode").clean("WAITER_ROBOT")

    def test_subset_validator(self) -> None:
        with pytest.raises(ValidationFailed):
            SubsetOf(("DINE_IN", "TAKE_AWAY"))(["DINE_IN", "TELEPORT"])

    def test_range_accepts_the_boundaries(self) -> None:
        Range(Decimal("0"), Decimal("100"))(Decimal("0"))
        Range(Decimal("0"), Decimal("100"))(Decimal("100"))


class TestResolution:
    def test_default_when_nothing_is_stored(self, context) -> None:
        result = resolver.resolve("finance.vat_percent", context)
        assert result.value == Decimal("14.00")
        assert result.is_default is True
        assert result.origin == "DEFAULT"

    def test_branch_override_wins_over_default(self, context, branch) -> None:
        resolver.set_value("finance.vat_percent", "10.00", scope=Scope.BRANCH, scope_id=branch.id)
        result = resolver.resolve("finance.vat_percent", context)
        assert result.value == Decimal("10.00")
        assert result.origin == "BRANCH"
        assert result.is_default is False

    def test_device_override_wins_over_branch(self, org, branch) -> None:
        device_id = uuid.uuid4()
        resolver.set_value("sync.push_batch_size", 50, scope=Scope.BRANCH, scope_id=branch.id)
        resolver.set_value("sync.push_batch_size", 10, scope=Scope.DEVICE, scope_id=device_id)
        context = resolver.ScopeContext(
            organization_id=org.id, branch_id=branch.id, device_id=device_id
        )
        result = resolver.resolve("sync.push_batch_size", context)
        assert result.value == 10
        assert result.origin == "DEVICE"

    def test_org_scope_resolves_without_a_branch(self, org) -> None:
        resolver.set_value("org.currency", "USD", scope=Scope.ORGANIZATION, scope_id=org.id)
        context = resolver.ScopeContext(organization_id=org.id)
        assert resolver.get("org.currency", context) == "USD"

    def test_writing_to_the_wrong_scope_is_rejected(self, branch) -> None:
        # org.currency is ORGANIZATION-scoped and declares no narrower override,
        # so a value written at BRANCH would be stored and never read.
        with pytest.raises(ValueError, match="may only be written at: ORGANIZATION"):
            resolver.set_value("org.currency", "USD", scope=Scope.BRANCH, scope_id=branch.id)

    def test_declared_narrower_scope_is_accepted(self, branch) -> None:
        """sync.push_batch_size is per-branch but overridable per device."""
        device_id = uuid.uuid4()
        resolver.set_value("sync.push_batch_size", 10, scope=Scope.DEVICE, scope_id=device_id)
        assert SettingValue.objects.filter(key="sync.push_batch_size", scope_type="DEVICE").exists()

    def test_invalid_value_is_rejected_before_storage(self, branch) -> None:
        with pytest.raises(ValidationFailed):
            resolver.set_value("finance.vat_percent", "500", scope=Scope.BRANCH, scope_id=branch.id)
        assert not SettingValue.objects.filter(key="finance.vat_percent").exists()

    def test_reset_falls_back_to_default(self, context, branch) -> None:
        resolver.set_value("finance.vat_percent", "10.00", scope=Scope.BRANCH, scope_id=branch.id)
        assert resolver.get("finance.vat_percent", context) == Decimal("10.00")

        resolver.reset("finance.vat_percent", scope=Scope.BRANCH, scope_id=branch.id)
        result = resolver.resolve("finance.vat_percent", context)
        assert result.value == Decimal("14.00")
        assert result.is_default is True

    def test_a_branch_that_changed_nothing_stores_nothing(self, context) -> None:
        resolver.resolve_all(context)
        assert SettingValue.objects.count() == 0

    def test_corrupt_stored_value_falls_back_instead_of_raising(self, context, branch) -> None:
        # Simulate a definition tightening after a value was written.
        SettingValue.objects.create(
            scope_type=Scope.BRANCH.value,
            scope_id=branch.id,
            key="finance.vat_percent",
            value="not-a-number",
        )
        result = resolver.resolve("finance.vat_percent", context)
        assert result.value == Decimal("14.00")
        assert result.is_default is True


class TestAudit:
    def test_change_is_logged_with_before_and_after(self, branch) -> None:
        resolver.set_value("finance.vat_percent", "14.00", scope=Scope.BRANCH, scope_id=branch.id)
        resolver.set_value("finance.vat_percent", "10.00", scope=Scope.BRANCH, scope_id=branch.id)

        entries = list(SettingChangeLog.objects.filter(key="finance.vat_percent"))
        assert len(entries) == 2
        latest = entries[0]  # ordering is -created_at
        assert latest.old_value == "14.00"
        assert latest.new_value == "10.00"

    def test_reset_is_logged(self, branch) -> None:
        resolver.set_value("finance.vat_percent", "10.00", scope=Scope.BRANCH, scope_id=branch.id)
        resolver.reset("finance.vat_percent", scope=Scope.BRANCH, scope_id=branch.id)

        latest = SettingChangeLog.objects.filter(key="finance.vat_percent").first()
        assert latest is not None
        assert latest.new_value is None


class TestDesktopPayload:
    def test_only_flagged_settings_are_pushed(self, context) -> None:
        payload = resolver.desktop_payload(context)
        assert "finance.vat_percent" in payload
        # Security settings stay server-side.
        assert "security.admin_ip_allowlist" not in payload

    def test_payload_is_json_serializable(self, context) -> None:
        import json

        json.dumps(resolver.desktop_payload(context))  # must not raise

    def test_decimals_and_times_survive_serialization(self, context) -> None:
        payload = resolver.desktop_payload(context)
        assert payload["finance.vat_percent"] == "14.00"
        assert payload["finance.business_day_start"] == "04:00"


class TestAnsweredQuestions:
    """The three architecture questions are settings, not constants."""

    def test_business_day_start_is_configurable(self, context, branch) -> None:
        assert resolver.get("finance.business_day_start", context) == time(4, 0)
        resolver.set_value(
            "finance.business_day_start", "06:30", scope=Scope.BRANCH, scope_id=branch.id
        )
        assert resolver.get("finance.business_day_start", context) == time(6, 30)

    @pytest.mark.parametrize("mode", ["CASHIER_ONLY", "WAITER_TERMINAL", "WAITER_DEVICE"])
    def test_all_three_service_modes_are_settable(self, context, branch, mode) -> None:
        resolver.set_value("floor.service_mode", mode, scope=Scope.BRANCH, scope_id=branch.id)
        assert resolver.get("floor.service_mode", context) == mode

    def test_mfa_defaults_to_admin_roles(self, context) -> None:
        assert resolver.get("security.require_mfa_for_roles", context) == [
            "SUPER_ADMIN",
            "BRANCH_MANAGER",
        ]


class TestKidsAreaDefaults:
    def test_child_photo_capture_is_off_by_default(self, context) -> None:
        assert resolver.get("kids.capture_child_photo", context) is False

    def test_guardian_verification_is_on_by_default(self, context) -> None:
        assert resolver.get("kids.require_guardian_verification", context) is True

    def test_age_limits_warn_rather_than_block(self, context) -> None:
        assert resolver.get("kids.enforce_age_limits", context) == "warn"

    def test_capacity_has_a_default(self, context) -> None:
        assert resolver.get("kids.max_capacity", context) == 25
