"""
The settings registry — commitment C10, "no business value is a code constant".

Definitions live here (typed, validated, permission-gated, documented). Values
live in the database. That split is what makes "everything is configurable"
survivable: adding a setting is ONE registry entry — no migration, no API
change, no frontend work, because the settings UI renders itself from this
registry.

See docs/11-configuration.md for the full catalog and the rationale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from decimal import Decimal, InvalidOperation
from enum import Enum, StrEnum
from typing import Any


class Scope(StrEnum):
    ORGANIZATION = "ORGANIZATION"
    BRANCH = "BRANCH"
    DEVICE = "DEVICE"
    ROLE = "ROLE"


# Most specific wins. Resolution walks this list from the front.
SCOPE_PRECEDENCE = [Scope.DEVICE, Scope.ROLE, Scope.BRANCH, Scope.ORGANIZATION]


class SettingType(StrEnum):
    STRING = "string"
    TEXT = "text"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    TIME = "time"
    ENUM = "enum"
    LIST = "list"
    JSON = "json"


class ValidationFailed(ValueError):
    """Raised when a submitted value fails a registry validator."""


# ── validators ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Range:
    minimum: Decimal | int | None = None
    maximum: Decimal | int | None = None

    def __call__(self, value: Any) -> None:
        if self.minimum is not None and value < type(value)(str(self.minimum)):
            raise ValidationFailed(f"القيمة يجب ألا تقل عن {self.minimum}")
        if self.maximum is not None and value > type(value)(str(self.maximum)):
            raise ValidationFailed(f"القيمة يجب ألا تزيد عن {self.maximum}")


@dataclass(frozen=True)
class OneOf:
    choices: tuple[str, ...]

    def __call__(self, value: Any) -> None:
        if value not in self.choices:
            raise ValidationFailed(f"القيمة يجب أن تكون إحدى: {', '.join(self.choices)}")


@dataclass(frozen=True)
class SubsetOf:
    choices: tuple[str, ...]

    def __call__(self, value: Any) -> None:
        if not isinstance(value, list):
            raise ValidationFailed("القيمة يجب أن تكون قائمة")
        invalid = [v for v in value if v not in self.choices]
        if invalid:
            raise ValidationFailed(f"قيم غير صحيحة: {', '.join(map(str, invalid))}")


# ── definition ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SettingDefinition:
    key: str
    type: SettingType
    default: Any
    scope: Scope
    """The primary scope — where this setting is normally configured."""
    group: str
    label_ar: str
    label_en: str = ""
    help_ar: str = ""
    permission: str = "system.settings"
    validators: tuple = ()
    choices: tuple[str, ...] = ()
    overridable_at: tuple[Scope, ...] = ()
    """
    Additional, narrower scopes that may override the primary one.

    Example: `sync.push_batch_size` is configured per branch but one slow
    terminal may need its own value. Resolution already walks every scope; this
    declares which writes are legitimate, so a value cannot be stored at a level
    that will never be read for it.
    """
    pushes_to_desktop: bool = False
    high_impact: bool = False
    """Financial, security or licensing — the UI shows a confirmation step."""
    affects_open_orders: bool = False
    """Applies only to orders opened after the change (see docs/11)."""

    @property
    def allowed_scopes(self) -> tuple[Scope, ...]:
        return (self.scope, *self.overridable_at)

    def parse(self, raw: Any) -> Any:
        """Coerce an incoming JSON value to the declared type."""
        try:
            match self.type:
                case SettingType.INTEGER:
                    return int(raw)
                case SettingType.DECIMAL:
                    return Decimal(str(raw))
                case SettingType.BOOLEAN:
                    if isinstance(raw, bool):
                        return raw
                    return str(raw).strip().lower() in {"1", "true", "yes", "on"}
                case SettingType.TIME:
                    if isinstance(raw, time):
                        return raw
                    hours, _, minutes = str(raw).partition(":")
                    return time(int(hours), int(minutes or 0))
                case SettingType.LIST:
                    if isinstance(raw, list):
                        return raw
                    raise ValidationFailed("القيمة يجب أن تكون قائمة")
                case SettingType.ENUM | SettingType.STRING | SettingType.TEXT:
                    return str(raw)
                case _:
                    return raw
        except ValidationFailed:
            raise
        except (TypeError, ValueError, InvalidOperation) as exc:
            raise ValidationFailed(f"القيمة غير صالحة للنوع {self.type.value}") from exc

    def validate(self, value: Any) -> None:
        if self.type is SettingType.ENUM and self.choices:
            OneOf(self.choices)(value)
        for validator in self.validators:
            validator(value)

    def clean(self, raw: Any) -> Any:
        value = self.parse(raw)
        self.validate(value)
        return value

    def serialize(self, value: Any) -> Any:
        """Convert a Python value to something JSON can hold."""
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, time):
            return value.strftime("%H:%M")
        if isinstance(value, Enum):
            return value.value
        return value


# ── registry ─────────────────────────────────────────────────────────────────


@dataclass
class _Registry:
    _definitions: dict[str, SettingDefinition] = field(default_factory=dict)

    def register(self, definition: SettingDefinition) -> SettingDefinition:
        if definition.key in self._definitions:
            raise RuntimeError(f"Setting '{definition.key}' is already registered")
        self._definitions[definition.key] = definition
        return definition

    def get(self, key: str) -> SettingDefinition:
        try:
            return self._definitions[key]
        except KeyError:
            raise KeyError(
                f"Unknown setting '{key}'. Register it in apps/configuration/definitions.py."
            ) from None

    def __contains__(self, key: object) -> bool:
        return key in self._definitions

    def all(self) -> dict[str, SettingDefinition]:
        return dict(self._definitions)

    def by_group(self) -> dict[str, list[SettingDefinition]]:
        grouped: dict[str, list[SettingDefinition]] = {}
        for definition in self._definitions.values():
            grouped.setdefault(definition.group, []).append(definition)
        return grouped

    def for_scope(self, scope: Scope) -> list[SettingDefinition]:
        return [d for d in self._definitions.values() if d.scope is scope]

    def desktop_keys(self) -> list[str]:
        return [k for k, d in self._definitions.items() if d.pushes_to_desktop]


registry = _Registry()


def register(**kwargs: Any) -> SettingDefinition:
    """Convenience wrapper: `register(key=..., type=..., default=...)`."""
    return registry.register(SettingDefinition(**kwargs))
