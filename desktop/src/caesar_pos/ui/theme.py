"""
Shared Qt styling.

RTL and large touch targets are set here rather than per-window, so a new screen
is correct by default instead of by remembering.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

#: Minimum touch target. Assume a fingertip on a glossy screen, not a mouse.
TOUCH_TARGET_PX = 64

STYLESHEET = """
QWidget {
    background: #f8fafc;
    color: #0f172a;
    font-size: 15px;
}
QLabel#Title {
    font-size: 26px;
    font-weight: 700;
}
QLabel#Subtitle {
    color: #475569;
    font-size: 14px;
}
QLabel#Error {
    color: #b91c1c;
    background: #fef2f2;
    border: 1px solid #fecaca;
    border-radius: 8px;
    padding: 10px 12px;
}
QLabel#Warning {
    color: #92400e;
    background: #fffbeb;
    border: 1px solid #fde68a;
    border-radius: 8px;
    padding: 10px 12px;
}
QLineEdit {
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    padding: 12px 14px;
    font-size: 16px;
    min-height: 24px;
}
QLineEdit:focus {
    border: 2px solid #1d4e89;
}
QLineEdit#KeySegment {
    font-family: "Consolas", "Courier New", monospace;
    font-size: 20px;
    letter-spacing: 3px;
    text-align: center;
}
QPushButton {
    background: #1d4e89;
    color: #ffffff;
    border: none;
    border-radius: 8px;
    padding: 14px 22px;
    font-size: 16px;
    font-weight: 600;
    min-height: 28px;
}
QPushButton:hover  { background: #17406f; }
QPushButton:pressed{ background: #123456; }
QPushButton:disabled { background: #94a3b8; }
QPushButton#Secondary {
    background: #e2e8f0;
    color: #0f172a;
}
QPushButton#Keypad {
    background: #ffffff;
    color: #0f172a;
    border: 1px solid #cbd5e1;
    font-size: 24px;
}
QPushButton#Keypad:pressed { background: #e2e8f0; }
QComboBox {
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    padding: 12px 14px;
    min-height: 24px;
}
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
