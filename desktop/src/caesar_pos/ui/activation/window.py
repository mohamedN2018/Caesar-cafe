"""
The activation screen — the only thing reachable before a device is licensed.

Failure messages name the remedy, never just "activation failed". A cashier at
7am needs to know whether to call the manager or check the wifi.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...api.client import ApiClient, ApiError
from ...api.licensing import ActivationResult, activate
from ...bootstrap import complete_activation
from ..theme import TOUCH_TARGET_PX
from .key_input import LicenseKeyInput

logger = logging.getLogger(__name__)

MODES = [("نقطة بيع", "POS"), ("شاشة مطبخ", "KDS"), ("الاثنان معاً", "BOTH")]


class ActivationWorker(QThread):
    """
    Network call off the UI thread.

    Activation can take seconds on a slow cafe connection; blocking the event
    loop would freeze the window and invite the user to click again.
    """

    succeeded = Signal(object)
    failed = Signal(str, str)  # code, message

    def __init__(self, base_url, license_key, email, device_name, mode) -> None:
        super().__init__()
        self._args = (base_url, license_key, email, device_name, mode)

    def run(self) -> None:
        base_url, license_key, email, device_name, mode = self._args
        client = ApiClient(base_url=base_url)
        try:
            result = activate(
                client,
                license_key=license_key,
                email=email,
                device_name=device_name,
                mode=mode,
            )
            self.succeeded.emit(result)
        except ApiError as exc:
            logger.warning("Activation failed", extra={"code": exc.code})
            self.failed.emit(exc.code, exc.message)
        except Exception as exc:  # must never kill the worker thread silently
            logger.exception("Unexpected activation error")
            self.failed.emit("UNEXPECTED", str(exc))
        finally:
            client.close()


class ActivationWindow(QWidget):
    activated = Signal(object)

    def __init__(self, *, default_server: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("القيصر — تفعيل النظام")
        self.setMinimumWidth(560)
        self._worker: ActivationWorker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(40, 36, 40, 36)
        root.setSpacing(18)

        title = QLabel("☕ القيصر")
        title.setObjectName("Title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(title)

        subtitle = QLabel("تفعيل النظام لأول مرة")
        subtitle.setObjectName("Subtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(subtitle)

        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.hide()
        root.addWidget(self.message)

        form = QFormLayout()
        form.setSpacing(14)

        self.server = QLineEdit(default_server)
        self.server.setPlaceholderText("https://api.example.com")
        self.server.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        form.addRow("عنوان الخادم", self.server)

        self.email = QLineEdit()
        self.email.setPlaceholderText("owner@example.com")
        self.email.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        form.addRow("البريد الإلكتروني", self.email)

        self.key_input = LicenseKeyInput()
        form.addRow("مفتاح الترخيص", self.key_input)

        self.device_name = QLineEdit()
        self.device_name.setPlaceholderText("كاشير-١")
        form.addRow("اسم الجهاز", self.device_name)

        self.mode = QComboBox()
        for label, value in MODES:
            self.mode.addItem(label, value)
        form.addRow("وضع التشغيل", self.mode)

        root.addLayout(form)

        self.submit = QPushButton("تفعيل الجهاز")
        self.submit.setMinimumHeight(TOUCH_TARGET_PX)
        self.submit.setEnabled(False)
        self.submit.clicked.connect(self._on_submit)
        root.addWidget(self.submit)

        hint = QLabel("للحصول على مفتاح، تواصل مع مدير النظام.")
        hint.setObjectName("Subtitle")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(hint)

        for widget in (self.server, self.email, self.device_name):
            widget.textChanged.connect(self._revalidate)
        self.key_input.changed.connect(self._revalidate)

        self.key_input.focus_first()

    # ── validation ───────────────────────────────────────────────────────────

    def _is_ready(self) -> bool:
        return bool(
            self.server.text().strip()
            and "@" in self.email.text()
            and self.device_name.text().strip()
            and self.key_input.is_valid()
        )

    def _revalidate(self, *_) -> None:
        self.submit.setEnabled(self._is_ready())

    # ── submission ───────────────────────────────────────────────────────────

    def _on_submit(self) -> None:
        self._show_message("جارٍ التفعيل…", level="Subtitle")
        self._set_busy(True)

        self._worker = ActivationWorker(
            self.server.text().strip(),
            self.key_input.key(),
            self.email.text().strip(),
            self.device_name.text().strip(),
            self.mode.currentData(),
        )
        self._worker.succeeded.connect(self._on_success)
        self._worker.failed.connect(self._on_failure)
        self._worker.start()

    def _on_success(self, result: ActivationResult) -> None:
        try:
            complete_activation(
                device_id=result.device_id,
                device_secret=result.device_secret,
                offline_token=result.offline_token,
            )
        except Exception as exc:  # any storage failure must still be shown
            logger.exception("Could not persist the activation")
            self._set_busy(False)
            self._show_message(f"تم التفعيل لكن تعذّر حفظ البيانات على الجهاز: {exc}", level="Error")
            return

        self._show_message(f"✅ تم التفعيل — {result.branch_name}", level="Subtitle")
        self.activated.emit(result)

    def _on_failure(self, code: str, message: str) -> None:
        self._set_busy(False)
        self._show_message(message, level="Error")

        # Put the cursor where the fix is.
        if code in {"LICENSE_NOT_FOUND", "LICENSE_KEY_MALFORMED"}:
            self.key_input.focus_first()
        elif code == "LICENSE_EMAIL_MISMATCH":
            self.email.setFocus()
            self.email.selectAll()
        elif code == "NETWORK_UNAVAILABLE":
            self.server.setFocus()

    def _set_busy(self, busy: bool) -> None:
        self.submit.setEnabled(not busy and self._is_ready())
        self.submit.setText("جارٍ التفعيل…" if busy else "تفعيل الجهاز")

    def _show_message(self, text: str, *, level: str) -> None:
        self.message.setText(text)
        self.message.setObjectName(level)
        # Re-polish so the new objectName picks up its stylesheet rule.
        self.message.style().unpolish(self.message)
        self.message.style().polish(self.message)
        self.message.show()
