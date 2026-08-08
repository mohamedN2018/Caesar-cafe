"""
The PIN pad.

A cashier enters this dozens of times a shift, one-handed, on a glossy screen,
often while holding something. Every decision here follows from that:

  * **Big targets.** 88px keys — larger than the 64px baseline, because this is
    the most-pressed control in the product and a mis-hit costs an attempt
    against a five-try lockout.
  * **Dots, not digits.** The screen faces the room.
  * **It submits itself** at the configured length. Making someone reach for a
    separate "enter" after four taps is one interaction too many, forty times a
    day.
  * **Wrong PIN clears the field and says how many tries remain.** A cashier who
    does not know they are on their last attempt is a cashier who locks the
    terminal during a queue.

The pad emits a PIN and knows nothing about who it belongs to. Verification is
`security.session`, so this widget is testable without a database.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGridLayout, QLabel, QPushButton, QVBoxLayout, QWidget

KEY_SIZE_PX = 88
DEFAULT_LENGTH = 4

#: Row-major, with the zero centred under 8 — the layout on every phone and ATM.
#: Deviating would be a small novelty with a real cost in mis-taps.
KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "⌫", "0", "✓"]

STYLESHEET = f"""
QPushButton#PinKey {{
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 12px;
    font-size: 28px;
    font-weight: 600;
    min-width: {KEY_SIZE_PX}px;
    min-height: {KEY_SIZE_PX}px;
}}
QPushButton#PinKey:pressed {{ background: #e2e8f0; }}
QPushButton#PinKeyConfirm {{
    background: #1d4e89;
    color: #ffffff;
    border: none;
    border-radius: 12px;
    font-size: 28px;
    min-width: {KEY_SIZE_PX}px;
    min-height: {KEY_SIZE_PX}px;
}}
QPushButton#PinKeyConfirm:pressed {{ background: #163a68; }}
QLabel#PinDots {{
    font-size: 40px;
    letter-spacing: 14px;
    color: #0f172a;
    min-height: 56px;
}}
"""


class PinPad(QWidget):
    """Emits `submitted(pin)` when the length is reached or ✓ is pressed."""

    submitted = Signal(str)
    changed = Signal(int)

    def __init__(self, *, length: int = DEFAULT_LENGTH, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.length = length
        self._digits: list[str] = []

        self.setStyleSheet(STYLESHEET)
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        self.dots = QLabel("", objectName="PinDots")
        self.dots.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.dots)

        grid = QGridLayout()
        grid.setSpacing(12)
        for index, key in enumerate(KEYS):
            button = QPushButton(key)
            button.setObjectName("PinKeyConfirm" if key == "✓" else "PinKey")
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _=False, k=key: self.press(k))
            grid.addWidget(button, index // 3, index % 3)

        layout.addLayout(grid)
        self._render()

    # ── input ────────────────────────────────────────────────────────────────

    def press(self, key: str) -> None:
        if key == "⌫":
            if self._digits:
                self._digits.pop()
        elif key == "✓":
            self._submit()
            return
        elif len(self._digits) < self.length:
            self._digits.append(key)

        self._render()
        self.changed.emit(len(self._digits))

        # Auto-submit: after four taps the intent is unambiguous, and asking for
        # a fifth is one interaction too many forty times a day.
        if len(self._digits) == self.length:
            self._submit()

    def clear(self) -> None:
        self._digits.clear()
        self._render()
        self.changed.emit(0)

    @property
    def value(self) -> str:
        return "".join(self._digits)

    def keyPressEvent(self, event) -> None:
        """
        A physical numpad works too.

        Many counters have a USB keypad next to the terminal, and a cashier who
        can touch-type it is faster than one tapping glass.
        """
        text = event.text()
        if text.isdigit():
            self.press(text)
        elif event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            self.press("⌫")
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.press("✓")
        elif event.key() == Qt.Key.Key_Escape:
            self.clear()
        else:
            super().keyPressEvent(event)

    # ── internals ────────────────────────────────────────────────────────────

    def _submit(self) -> None:
        if not self._digits:
            return
        pin = self.value
        self.clear()
        self.submitted.emit(pin)

    def _render(self) -> None:
        # Dots, never digits. The screen faces the room.
        self.dots.setText("●" * len(self._digits) + "○" * (self.length - len(self._digits)))
