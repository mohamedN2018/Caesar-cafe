"""
Guards that keep the architecture true six months from now.

These are cheap, run on every commit, and catch the drift that documentation
alone never prevents.
"""

from __future__ import annotations

import ast
import inspect
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


class TestVendoredModulesArePortable:
    """
    These modules are copied into the PySide6 Desktop client verbatim, so both
    sides run identical logic. A Django import in any of them breaks that — and
    the two implementations would then be free to drift, which is exactly what
    vendoring exists to prevent.
    """

    VENDORED = [
        ("core", "money.py", "order totals must agree to the piaster"),
        ("licensing", "offline_token.py", "the client verifies tokens locally"),
        ("licensing", "keys.py", "the client normalizes typed keys before sending"),
    ]

    @pytest.mark.parametrize(("app", "filename", "why"), VENDORED)
    def test_no_django_imports(self, app: str, filename: str, why: str) -> None:
        tree = ast.parse((APPS_DIR / app / filename).read_text(encoding="utf-8"))

        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)

        django_imports = [name for name in imported if name.split(".")[0] == "django"]
        assert not django_imports, (
            f"{app}/{filename} must stay Django-free ({why}); found: {django_imports}"
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


class TestNoReservedKeysInLogExtra:
    """
    `logger.info(..., extra={"filename": x})` raises KeyError at runtime.

    Python's logging refuses to let `extra` shadow a LogRecord attribute, so the
    call site does not log — it throws, from inside an exception handler, and
    takes down whatever it was trying to report on. Found the hard way in
    `apps/ops/backups.py`, where every backup crashed on its own success message.

    Cheap to check statically, and the failure is invisible until the code path
    runs — which for logging is usually during an incident.
    """

    #: The subset of LogRecord attributes a developer might plausibly reach for.
    RESERVED = frozenset(
        {
            "args",
            "asctime",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "message",
            "module",
            "msecs",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "thread",
            "threadName",
            "taskName",
        }
    )

    def _extra_keys(self, path: Path) -> list[tuple[int, str]]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found: list[tuple[int, str]] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "extra" or not isinstance(keyword.value, ast.Dict):
                    continue
                for key in keyword.value.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        found.append((key.lineno, key.value))
        return found

    def test_no_call_site_shadows_a_logrecord_attribute(self) -> None:
        offenders = []
        for path in _python_files():
            for lineno, key in self._extra_keys(path):
                if key in self.RESERVED:
                    offenders.append(f"{path.relative_to(APPS_DIR)}:{lineno}: extra={{'{key}': …}}")

        assert not offenders, (
            "These would raise KeyError instead of logging. Rename the key:\n  "
            + "\n  ".join(offenders)
        )

    def test_the_guard_actually_catches_a_violation(self, tmp_path: Path) -> None:
        """A guard that cannot fail is not a guard."""
        offender = tmp_path / "offender.py"
        offender.write_text('logger.info("x", extra={"filename": f, "ok": 1})\n', encoding="utf-8")
        keys = [key for _, key in self._extra_keys(offender)]

        assert "filename" in keys
        assert "ok" in keys, "the guard must see every key, not just the first"


class TestErrorConstructorsAcceptWhatCallersPass:
    """
    Every keyword handed to an `AppError` must be one it accepts.

    The same shape of bug as the reserved-`extra` class above: it type-checks
    fine, reads fine, and raises `TypeError` only on the path it guards. Found in
    four call sites at once — `BRANCH_REQUIRED` in three views and the
    `DEVICE_REVOKED` backstop in sync — none of which had a test that reached
    them. Each returned a 500 with no machine code in place of the 400 or 403 the
    client is written to branch on.
    """

    def _accepted_kwargs(self) -> set[str]:
        from apps.core.exceptions import AppError

        signature = inspect.signature(AppError.__init__)
        return {
            name
            for name, param in signature.parameters.items()
            if param.kind is inspect.Parameter.KEYWORD_ONLY
        }

    def _error_call_kwargs(self, path: Path) -> list[tuple[int, str, str]]:
        """(line, class, kwarg) for every `SomethingError(...)` call in a file."""
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found: list[tuple[int, str, str]] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if not node.func.id.endswith("Error"):
                continue
            for keyword in node.keywords:
                if keyword.arg:
                    found.append((node.lineno, node.func.id, keyword.arg))
        return found

    def test_no_call_site_passes_an_unaccepted_keyword(self) -> None:
        accepted = self._accepted_kwargs()
        offenders = []

        for path in _python_files():
            for lineno, name, kwarg in self._error_call_kwargs(path):
                if kwarg not in accepted:
                    offenders.append(f"{path.relative_to(APPS_DIR)}:{lineno}: {name}(…, {kwarg}=…)")

        assert not offenders, (
            "These raise TypeError instead of the error they describe. "
            f"AppError accepts {sorted(accepted)}:\n  " + "\n  ".join(offenders)
        )

    def test_the_guard_actually_catches_a_violation(self, tmp_path: Path) -> None:
        offender = tmp_path / "offender.py"
        offender.write_text('raise AppError("x", code="C", http=418)\n', encoding="utf-8")
        kwargs = [kwarg for _, _, kwarg in self._error_call_kwargs(offender)]

        assert kwargs == ["code", "http"]
        assert "http" not in self._accepted_kwargs()
