"""
The vendored modules must be byte-identical to the backend's.

Order totals, offline-token verification and key normalization have to give the
same answer on both sides. Reimplementing them in the client is how a server and
a client quietly start disagreeing about a number, so the client copies them —
and this test is what stops the copies from drifting.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "vendor_shared.py"
VENDORED = ROOT / "desktop" / "src" / "caesar_pos" / "vendored"
BACKEND = ROOT / "backend" / "apps"

MODULES = [
    ("money.py", "core/money.py"),
    ("offline_token.py", "licensing/offline_token.py"),
    ("keys.py", "licensing/keys.py"),
]


@pytest.mark.skipif(not SCRIPT.exists(), reason="running outside the monorepo")
def test_vendored_copies_are_in_sync() -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, (
        "Vendored modules have drifted from the backend.\n"
        f"{result.stdout}{result.stderr}\n"
        "Run: python scripts/vendor_shared.py"
    )


@pytest.mark.skipif(not BACKEND.exists(), reason="running outside the monorepo")
@pytest.mark.parametrize(("vendored_name", "backend_path"), MODULES)
def test_body_matches_the_backend_exactly(vendored_name: str, backend_path: str) -> None:
    """Compare bodies, ignoring only the generated DO-NOT-EDIT header."""
    vendored = (VENDORED / vendored_name).read_text(encoding="utf-8")
    backend = (BACKEND / backend_path).read_text(encoding="utf-8")

    marker = "# ─────"
    body = vendored.split("\n\n", 1)[1] if vendored.startswith(marker) else vendored
    assert body == backend, f"{vendored_name} differs from backend/apps/{backend_path}"


def test_vendored_files_warn_against_editing() -> None:
    for name, _ in MODULES:
        header = (VENDORED / name).read_text(encoding="utf-8")[:400]
        assert "DO NOT EDIT" in header
        assert "vendor_shared.py" in header


class TestSharedBehaviour:
    """A few spot checks that the vendored code actually works when imported."""

    def test_money_rounds_half_up(self) -> None:
        from decimal import Decimal

        from caesar_pos.vendored.money import quantize_money

        assert quantize_money(Decimal("0.125")) == Decimal("0.13")

    def test_keys_fold_confusables(self) -> None:
        from caesar_pos.vendored import keys

        assert keys.normalize("QSR-7X29-K8P4-3FIA-9WYZ") == "QSR-7X29-K8P4-3F1A-9WYZ"
        assert keys.normalize("qsr 7x29 k8p4 3f1a 9wyz") == "QSR-7X29-K8P4-3F1A-9WYZ"

    def test_order_totals_match_the_documented_example(self) -> None:
        """2× cappuccino + 1× turkish, 12% service, 14% VAT = 204.29 (docs/04)."""
        from decimal import Decimal

        from caesar_pos.vendored.money import OrderLine, TaxRules, compute_order

        totals = compute_order(
            [
                OrderLine(unit_price=Decimal("60.00"), quantity=Decimal("2")),
                OrderLine(unit_price=Decimal("40.00"), quantity=Decimal("1")),
            ],
            TaxRules(
                vat_percent=Decimal("14.00"),
                vat_enabled=True,
                vat_inclusive=False,
                service_percent=Decimal("12.00"),
                service_enabled=True,
                rounding_step=Decimal("0.01"),
            ),
        )
        assert totals.grand_total == Decimal("204.29")
