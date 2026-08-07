"""
Guards that keep the architecture true six months from now.

These are cheap, run on every commit, and catch the drift that documentation
alone never prevents.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

APPS_DIR = Path(__file__).resolve().parents[1] / "apps"


def _python_files() -> list[Path]:
    return [
        path
        for path in APPS_DIR.rglob("*.py")
        if "migrations" not in path.parts and "tests" not in path.parts
    ]


class TestNoFloatNearMoney:
    """docs/02: `float` appears nowhere near money."""

    def test_no_float_field_in_models(self) -> None:
        offenders = []
        for path in _python_files():
            source = path.read_text(encoding="utf-8")
            if re.search(r"\bFloatField\b", source):
                offenders.append(str(path.relative_to(APPS_DIR)))
        assert not offenders, (
            "FloatField found — money and quantities must use DecimalField:\n  "
            + "\n  ".join(offenders)
        )

    def test_money_module_never_imports_float_helpers(self) -> None:
        source = (APPS_DIR / "core" / "money.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "float", "float() called in money.py"


class TestMoneyModuleIsPortable:
    """
    apps/core/money.py is vendored into the PySide6 Desktop client verbatim.

    It must therefore have no Django dependency — otherwise the two sides cannot
    share one algorithm, and they will drift.
    """

    def test_no_django_imports(self) -> None:
        source = (APPS_DIR / "core" / "money.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)

        django_imports = [name for name in imported if name.split(".")[0] == "django"]
        assert not django_imports, (
            f"money.py must stay Django-free so the Desktop can vendor it; found: {django_imports}"
        )


class TestSettingsRegistryIsTheSourceOfBusinessValues:
    """Commitment C10 — no business value is a code constant."""

    # A *numeric literal* bound to a money-rule name. Passing the value along
    # (`vat_percent=rules.effective_vat`) is correct and must not be flagged;
    # writing the number (`vat_percent = 14`) is the mistake C10 prevents.
    HARDCODED_RATE = re.compile(
        r"\b(vat[_ ]?(?:percent|rate)|service[_ ]?percent|rounding[_ ]?step)"
        r"\s*(?::[^=\n]+)?=\s*(?:Decimal\s*\(\s*)?['\"]?\d",
        re.IGNORECASE,
    )

    def test_no_money_rate_is_written_as_a_literal(self) -> None:
        offenders = []
        for path in _python_files():
            # definitions.py IS the registry — the one place defaults belong.
            if path.name == "definitions.py":
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if self.HARDCODED_RATE.search(line):
                    offenders.append(f"{path.relative_to(APPS_DIR)}:{lineno}: {line.strip()}")
        assert not offenders, (
            "Hardcoded money rate outside the settings registry:\n  " + "\n  ".join(offenders)
        )

    def test_the_guard_actually_catches_a_violation(self) -> None:
        """A guard that cannot fail is not a guard."""
        assert self.HARDCODED_RATE.search('vat_percent = Decimal("14.00")')
        assert self.HARDCODED_RATE.search("service_percent = 12")
        assert self.HARDCODED_RATE.search('vat_percent: Decimal = Decimal("14")')
        # ...and does not fire on legitimate value passing.
        assert not self.HARDCODED_RATE.search("vat_percent=rules.effective_vat")
        assert not self.HARDCODED_RATE.search("vat_percent: Decimal")


class TestRegistryCoversTheDocumentedCatalog:
    @pytest.mark.parametrize(
        "key",
        [
            # The three answered architecture questions.
            "finance.business_day_start",
            "floor.service_mode",
            "security.require_mfa_for_roles",
            # Kids area safety-critical settings.
            "kids.max_capacity",
            "kids.require_guardian_verification",
            "kids.capture_child_photo",
            # Money rules the Desktop must receive.
            "finance.vat_percent",
            "finance.rounding_step",
        ],
    )
    def test_key_is_registered(self, key: str) -> None:
        from apps.configuration.registry import registry

        assert key in registry, f"{key} is documented but not registered"
