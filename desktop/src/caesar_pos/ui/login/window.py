"""
The login screen.

Shows the sync indicator even before anyone has logged in, deliberately: a
cashier arriving at 7am to a terminal that has been offline since Tuesday should
learn that here, not after taking three orders.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ...security.session import Authenticator, DeviceLocked, PinRejected, Session
from ..theme import TOUCH_TARGET_PX  # noqa: F401 — imported for stylesheet parity
from .pin_pad import PinPad

logger = logging.getLogger(__name__)


class LoginWindow(QWidget):
    """Emits `logged_in(Session)`."""

    logged_in = Signal(object)

    def __init__(
        self,
        authenticator: Authenticator,
        *,
        pin_length: int = 4,
        sync_label: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.authenticator = authenticator

        self.setWindowTitle("كافيه القيصر — تسجيل الدخول")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setMinimumSize(480, 720)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(20)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("أدخل رمز الدخول", objectName="Title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Visible before login on purpose: a terminal that has been queueing
        # since Tuesday should say so before the first order, not after three.
        self.sync = QLabel(sync_label, objectName="Subtitle")
        self.sync.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.sync)

        self.message = QLabel("", objectName="Error")
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setWordWrap(True)
        self.message.hide()
        layout.addWidget(self.message)

        self.pad = PinPad(length=pin_length)
        self.pad.submitted.connect(self._attempt)
        self.pad.changed.connect(lambda _: self.message.hide())
        layout.addWidget(self.pad, alignment=Qt.AlignmentFlag.AlignCenter)

        self.pad.setFocus()

    # ── attempts ─────────────────────────────────────────────────────────────

    def _attempt(self, pin: str) -> None:
        try:
            session = self.authenticator.login(pin)
        except DeviceLocked as exc:
            self._show(str(exc))
            return
        except PinRejected as exc:
            remaining = self.authenticator.attempts_remaining
            # Naming the count is what stops a cashier locking the terminal
            # mid-queue without realising how close they were.
            suffix = f" — باقي {remaining} محاولات" if remaining else ""
            self._show(f"{exc}{suffix}")
            return

        self.message.hide()
        self.logged_in.emit(session)

    def set_sync_label(self, text: str) -> None:
        self.sync.setText(text)

    def _show(self, text: str) -> None:
        self.message.setText(text)
        self.message.show()


def session_summary(session: Session) -> str:
    """One line for the shell header: who is on the till, and as what."""
    roles = "، ".join(session.roles) if session.roles else "بدون دور"
    return f"{session.full_name_ar} · {roles}"
