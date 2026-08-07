"""
The licence key field.

Four segments that auto-advance, accept a pasted whole key, and fold Crockford
confusables as you type — because this key arrives as a photo on a phone or a
WhatsApp message, activation is rate-limited to five attempts an hour, and a
cashier at 7am should not lose one to a mistyped I-versus-1.
"""

from __future__ import annotations

from PySide6.QtCore import QRegularExpression, Qt, Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QWidget

from ...vendored import keys

SEGMENTS = 4
SEGMENT_LEN = 4


def fold(text: str) -> str:
    """
    Fold typed input using the SAME rules the server applies.

    Shared rather than reimplemented: if the client folded `I`→`1` differently
    from the server, a key would look right here and be rejected there.
    """
    return "".join(c for c in keys.fold_input(text) if c in keys.ALPHABET)


class _Segment(QLineEdit):
    """
    One four-character group.

    Deliberately NO `maxLength`. Qt applies it before `textChanged` fires, so a
    pasted or typed full key would be truncated to four characters before this
    widget ever saw it — and pasting is how most people will enter the key.
    Length is enforced in `_on_text`, which can redistribute the overflow.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("KeySegment")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setPlaceholderText("____")
        # The key is Latin-alphanumeric even inside an RTL application.
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)

        # Permissive on purpose: lower case and the confusables (I, L, O) must
        # reach `fold`, which converts them. A strict validator would silently
        # swallow the keystroke and leave the user retyping a correct key.
        self.setValidator(QRegularExpressionValidator(QRegularExpression("[0-9A-Za-z\\-\\s]*")))


class LicenseKeyInput(QWidget):
    """Emits `changed(is_complete)` so the submit button can follow along."""

    changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fields: list[_Segment] = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        prefix = QLabel(keys.PREFIX)
        prefix.setObjectName("KeySegment")
        layout.addWidget(prefix)

        for index in range(SEGMENTS):
            field = _Segment()
            field.textChanged.connect(lambda text, i=index: self._on_text(i, text))
            field.installEventFilter(self)

            self._fields.append(field)
            layout.addWidget(field)
            if index < SEGMENTS - 1:
                layout.addWidget(QLabel("-"))

    # ── behaviour ────────────────────────────────────────────────────────────

    def _on_text(self, index: int, text: str) -> None:
        folded = fold(text)

        # More than one segment's worth arrived — a paste, or a key typed
        # straight into one box. Spread it from here onwards.
        if len(folded) > SEGMENT_LEN:
            self._distribute(index, folded)
            return

        if folded != text:
            field = self._fields[index]
            cursor = field.cursorPosition()
            field.blockSignals(True)
            field.setText(folded)
            field.blockSignals(False)
            field.setCursorPosition(min(cursor, len(folded)))

        if len(folded) == SEGMENT_LEN and index < SEGMENTS - 1:
            nxt = self._fields[index + 1]
            nxt.setFocus()
            nxt.selectAll()

        self.changed.emit(self.is_complete())

    def _distribute(self, start: int, body: str) -> None:
        """Lay `body` across the segments from `start`, discarding any excess."""
        # A full key pasted anywhere fills the whole field, not just the tail.
        if len(body) >= keys.KEY_LENGTH:
            start = 0

        for offset, field_index in enumerate(range(start, SEGMENTS)):
            chunk = body[offset * SEGMENT_LEN : (offset + 1) * SEGMENT_LEN]
            field = self._fields[field_index]
            field.blockSignals(True)
            field.setText(chunk)
            field.blockSignals(False)

        filled = min(start + (len(body) + SEGMENT_LEN - 1) // SEGMENT_LEN, SEGMENTS) - 1
        self._fields[max(filled, 0)].setFocus()
        self.changed.emit(self.is_complete())

    def eventFilter(self, watched, event) -> bool:  # Qt API naming
        """Backspace in an empty segment steps back to the previous one."""
        if event.type() == event.Type.KeyPress and event.key() == Qt.Key.Key_Backspace:
            for index, field in enumerate(self._fields):
                if field is watched and not field.text() and index > 0:
                    previous = self._fields[index - 1]
                    previous.setFocus()
                    previous.setText(previous.text()[:-1])
                    return True
        return super().eventFilter(watched, event)

    # ── value ────────────────────────────────────────────────────────────────

    def set_key(self, raw: str) -> None:
        body = fold(raw)

        for index, field in enumerate(self._fields):
            chunk = body[index * SEGMENT_LEN : (index + 1) * SEGMENT_LEN]
            field.blockSignals(True)
            field.setText(chunk)
            field.blockSignals(False)

        focus_index = min(len(body) // SEGMENT_LEN, SEGMENTS - 1)
        self._fields[focus_index].setFocus()
        self.changed.emit(self.is_complete())

    def key(self) -> str:
        return f"{keys.PREFIX}-" + "-".join(f.text() for f in self._fields)

    def is_complete(self) -> bool:
        return all(len(f.text()) == SEGMENT_LEN for f in self._fields)

    def is_valid(self) -> bool:
        try:
            keys.normalize(self.key())
        except ValueError:
            return False
        return True

    def clear(self) -> None:
        for field in self._fields:
            field.blockSignals(True)
            field.clear()
            field.blockSignals(False)
        self._fields[0].setFocus()
        self.changed.emit(False)

    def focus_first(self) -> None:
        self._fields[0].setFocus()
