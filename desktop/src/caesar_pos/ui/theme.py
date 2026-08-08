"""
Shared Qt styling.

RTL and large touch targets are set here rather than per-window, so a new screen
is correct by default instead of by remembering.

Colours come from `palette.py`, which mirrors the Web's `brand.css` and is
checked against it in CI. A hex literal in this file would be the fourth place
the brand lives and the first one to drift.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from . import palette as p

#: Minimum touch target. Assume a fingertip on a glossy screen, not a mouse.
TOUCH_TARGET_PX = 64

STYLESHEET = f"""
QWidget {{
    background: {p.SURFACE_MUTED};
    color: {p.INK};
    font-size: 15px;
}}
QLabel#Title {{
    font-size: 26px;
    font-weight: 700;
    color: {p.BRAND_700};
}}
QLabel#Subtitle {{
    color: {p.INK_MUTED};
    font-size: 14px;
}}
QLabel#Error {{
    color: {p.DANGER};
    background: {p.DANGER_BG};
    border: 1px solid {p.DANGER};
    border-radius: 8px;
    padding: 10px 12px;
}}
QLabel#Warning {{
    color: {p.WARNING};
    background: {p.WARNING_BG};
    border: 1px solid {p.WARNING};
    border-radius: 8px;
    padding: 10px 12px;
}}
QLineEdit {{
    background: {p.SURFACE};
    border: 1px solid {p.BORDER_STRONG};
    border-radius: 8px;
    padding: 12px 14px;
    font-size: 16px;
    min-height: 24px;
}}
QLineEdit:focus {{
    border: 2px solid {p.BRAND_700};
}}
QLineEdit#KeySegment {{
    font-family: "Consolas", "Courier New", monospace;
    font-size: 20px;
    letter-spacing: 3px;
    text-align: center;
}}
QPushButton {{
    background: {p.BRAND_700};
    color: {p.FG_ON_BRAND};
    border: none;
    border-radius: 8px;
    padding: 14px 22px;
    font-size: 16px;
    font-weight: 600;
    min-height: 28px;
}}
QPushButton:hover  {{ background: {p.BRAND_800}; }}
QPushButton:pressed{{ background: {p.BRAND_900}; }}
QPushButton:disabled {{ background: {p.BORDER_STRONG}; color: {p.INK_FAINT}; }}
QPushButton#Secondary {{
    background: {p.SURFACE_SUNKEN};
    color: {p.INK};
}}
/* Gold is emphasis, never a whole surface — and it carries dark text, because
   gold on white is unreadable at any weight. */
QPushButton#Accent {{
    background: {p.GOLD_500};
    color: {p.FG_ON_GOLD};
}}
QPushButton#Accent:hover {{ background: {p.GOLD_600}; }}
QPushButton#Keypad {{
    background: {p.SURFACE};
    color: {p.INK};
    border: 1px solid {p.BORDER_STRONG};
    font-size: 24px;
}}
QPushButton#Keypad:pressed {{ background: {p.SURFACE_SUNKEN}; }}
QComboBox {{
    background: {p.SURFACE};
    border: 1px solid {p.BORDER_STRONG};
    border-radius: 8px;
    padding: 12px 14px;
    min-height: 24px;
}}
"""


def apply(app: QApplication) -> None:
    """RTL-first, Arabic-capable font, consistent styling."""
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

    # Cairo and Tajawal shape Arabic properly; the default sans stack often
    # falls back to a font with poor or absent shaping.
    font = QFont()
    font.setFamilies(["Cairo", "Tajawal", "Segoe UI", "sans-serif"])
    font.setPointSize(11)
    app.setFont(font)

    app.setStyleSheet(STYLESHEET)
