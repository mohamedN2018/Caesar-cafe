"""
Every table shape the model defines is drawn by the till.

`TableShape` has five members and the floor plan was written with four, so the
three BAR seats in the seeded room rendered as unstyled rectangles — present,
tappable, and not looking like anything in particular.

Nothing catches that: the enum is Python, the drawing is CSS, and no type system
spans the two. A sixth shape would land the same way, silently, and be noticed by
whoever was standing at the bar rather than by anyone reading a diff. So the two
are compared here.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from apps.floor.models import TableShape

PLAN = pathlib.Path(__file__).resolve().parents[2] / "frontend/src/views/pos/PosTablesView.vue"

if not PLAN.exists():
    pytest.skip(
        "The frontend tree is not reachable from here, so this guard cannot run.\n\n"
        "That is the dev container: docker-compose.yml mounts only `./backend:/app`,\n"
        "so there is no sibling `frontend/`. CI checks out the whole repo and runs\n"
        "the backend job with working-directory: backend, so the sibling exists there.\n\n"
        "A skip rather than a pass, deliberately: a guard that quietly reported\n"
        "success on a file it could not read would be worse than one that says it\n"
        "did not look.",
        allow_module_level=True,
    )


def drawn_shapes() -> set[str]:
    """The shapes the stylesheet actually has a rule for."""
    css = PLAN.read_text(encoding="utf-8")
    return {m.upper() for m in re.findall(r"\.shape-([a-z]+)\s+\.table-shape", css)}


def test_every_shape_the_model_defines_is_drawn() -> None:
    defined = {choice.value for choice in TableShape}
    missing = defined - drawn_shapes()

    assert not missing, (
        f"these table shapes have no rule in the floor plan and will render as "
        f"plain rectangles: {sorted(missing)}"
    )


def test_the_plan_does_not_draw_shapes_that_do_not_exist() -> None:
    """
    The other direction, which is cheaper to get wrong and harder to notice.

    A rule for a shape the model dropped is dead CSS that reads as support for
    something the product cannot produce.
    """
    defined = {choice.value for choice in TableShape}
    extra = drawn_shapes() - defined

    assert not extra, f"the floor plan styles shapes the model does not define: {sorted(extra)}"


def test_the_stylesheet_was_actually_parsed() -> None:
    # Guards that silently match nothing pass forever. If the class naming
    # changes, this fails rather than the two above quietly agreeing on nothing.
    assert len(drawn_shapes()) >= 4
