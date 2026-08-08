"""
The Desktop and the Web paint the same cafe.

Qt stylesheets have no CSS variables, so the brand exists twice: as custom
properties in `frontend/src/assets/brand.css` and as constants in
`ui/palette.py`. Two definitions of one fact eventually disagree, and the one
that disagrees is always the one nobody is looking at — a cashier staring at a
terminal does not notice that the header is a slightly different red from the
owner's dashboard, but a customer looking at both does.

Same discipline as the vendored money modules, enforced the same way: read the
other side's file across the monorepo and compare.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from caesar_pos.ui import palette

BRAND_CSS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "assets" / "brand.css"

#: `--name: #value;`, ignoring anything that is not a plain hex colour (the
#: shadow token is an rgba() and is not mirrored into Qt).
DECLARATION = re.compile(r"--([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*;")


@pytest.fixture(scope="module")
def css_tokens() -> dict[str, str]:
    assert BRAND_CSS.exists(), f"the Web brand file has moved: {BRAND_CSS}"
    return {
        name: value.lower()
        for name, value in DECLARATION.findall(BRAND_CSS.read_text(encoding="utf-8"))
    }


def test_the_css_was_actually_parsed(css_tokens: dict[str, str]) -> None:
    """Guard the guard: an empty dict would make everything below pass."""
    assert len(css_tokens) >= 30
    assert css_tokens["brand-700"] == "#7b1e28"


def test_every_desktop_token_matches_the_web(css_tokens: dict[str, str]) -> None:
    drifted = [
        f"{name}: desktop {value} vs web {css_tokens[name]}"
        for name, value in palette.TOKENS.items()
        if name in css_tokens and css_tokens[name] != value.lower()
    ]
    assert not drifted, "The two halves are painting different cafes:\n  " + "\n  ".join(drifted)


def test_every_desktop_token_exists_on_the_web(css_tokens: dict[str, str]) -> None:
    """A Desktop-only colour is a colour the owner cannot change from one place."""
    orphans = sorted(set(palette.TOKENS) - set(css_tokens))
    assert not orphans, f"declared in palette.py but not in brand.css: {orphans}"


def test_gold_is_never_used_as_a_light_background_with_light_text() -> None:
    """
    Gold carries dark text. Gold on white — or white on gold — fails contrast at
    every weight, and this is the one palette mistake that looks fine to whoever
    made it and is unreadable to everybody else.
    """
    assert palette.FG_ON_GOLD == palette.INK
    assert palette.FG_ON_GOLD != palette.SURFACE


def test_the_state_colours_are_not_brand_derived() -> None:
    """
    A burgundy "danger" beside a burgundy header is a warning nobody sees. These
    three have to be unmistakable from across a kitchen.
    """
    brand_family = {
        value.lower()
        for name, value in palette.TOKENS.items()
        if name.startswith(("brand-", "gold-"))
    }
    for state in (palette.DANGER, palette.WARNING, palette.SUCCESS, palette.INFO):
        assert state.lower() not in brand_family


def test_the_theme_stylesheet_holds_no_hex_literals() -> None:
    """
    Every colour in the Desktop resolves through `palette.py`. A literal in
    `theme.py` would be a fourth place the brand lives and the first to drift.
    """
    from caesar_pos.ui import theme

    source = Path(theme.__file__).read_text(encoding="utf-8")
    body = source.split('STYLESHEET = f"""', 1)[1].split('"""', 1)[0]

    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", body), (
        "hardcoded colours in theme.py — use a palette token"
    )
