"""
Shown when the licence will not permit startup.

Deliberately not a dead end: it always offers a retry and names who to contact,
because the most common cause is a lapsed renewal or a dropped connection —
neither of which the person standing at the till can fix by staring at it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from ...security.license_gate import GateResult
from ..theme import TOUCH_TARGET_PX


class BlockedWindow(QWidget):
    retry_requested = Signal()
    reactivate_requested = Signal()

    def __init__(self, gate: GateResult, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("القيصر — الترخيص")
        self.setMinimumWidth(520)

        root = QVBoxLayout(self)
        root.setContentsMargins(40, 40, 40, 40)
        root.setSpacing(18)

        icon = QLabel("🔒")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("font-size: 48px;")
        root.addWidget(icon)

        title = QLabel("لا يمكن تشغيل النظام")
        title.setObjectName("Title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(title)

        message = QLabel(gate.message_ar or "الترخيص غير صالح.")
        message.setObjectName("Error")
        message.setWordWrap(True)
        message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(message)

        retry = QPushButton("إعادة المحاولة")
        retry.setMinimumHeight(TOUCH_TARGET_PX)
        retry.clicked.connect(self.retry_requested.emit)
        root.addWidget(retry)

        # A tampered or missing token is only fixable by activating again.
        if gate.reason_code in {"TOKEN_TAMPERED", "TOKEN_MALFORMED", "NO_TOKEN"}:
            reactivate = QPushButton("إعادة تفعيل الجهاز")
            reactivate.setObjectName("Secondary")
            reactivate.setMinimumHeight(TOUCH_TARGET_PX)
            reactivate.clicked.connect(self.reactivate_requested.emit)
            root.addWidget(reactivate)

        code = QLabel(f"رمز: {gate.reason_code}")
        code.setObjectName("Subtitle")
        code.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(code)
