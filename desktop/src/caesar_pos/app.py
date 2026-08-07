"""
Application entry point.

    python -m caesar_pos

Renders whatever `bootstrap.start()` decided. The decision logic lives there,
UI-free, so it can be tested directly rather than through a window.
"""

from __future__ import annotations

import logging
import os
import sys

from PySide6.QtWidgets import QApplication

from .api.client import ApiClient
from .bootstrap import Screen, Startup, start
from .config import APP_NAME, APP_VERSION, paths
from .ui import theme
from .ui.activation import ActivationWindow
from .ui.blocked.window import BlockedWindow

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("CAESAR_LOG_LEVEL", "INFO"),
        format="%(levelname)-8s %(asctime)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stderr),
            logging.FileHandler(paths().log_file, encoding="utf-8"),
        ],
    )


def server_url() -> str:
    return os.environ.get("CAESAR_SERVER_URL", "")


class Application:
    """Owns the window lifecycle and the transitions between screens."""

    def __init__(self, qt_app: QApplication) -> None:
        self.qt = qt_app
        self.client = ApiClient(base_url=server_url() or "http://localhost:8000")
        self.window = None

    def run(self) -> int:
        self.show_for(start(self.client))
        return self.qt.exec()

    def show_for(self, startup: Startup) -> None:
        if self.window is not None:
            self.window.close()
            self.window.deleteLater()

        if startup.screen is Screen.ACTIVATION:
            window = ActivationWindow(default_server=server_url())
            window.activated.connect(self._on_activated)
            if startup.gate.message_ar:
                window._show_message(startup.gate.message_ar, level="Warning")

        elif startup.screen is Screen.BLOCKED:
            window = BlockedWindow(startup.gate)
            window.retry_requested.connect(self._restart)
            window.reactivate_requested.connect(self._force_activation)

        else:
            # Phase 5 replaces this with the PIN pad and the POS shell. The
            # licence gate above is what Phase 3 delivers.
            from PySide6.QtWidgets import QLabel

            window = QLabel(
                "✅ الترخيص صالح\n\n"
                f"الحالة: {startup.gate.reason_code or 'ACTIVE'}\n"
                f"{'متصل' if startup.online else 'غير متصل — يعمل بالترخيص المحفوظ'}\n\n"
                "شاشة تسجيل الدخول تأتي في المرحلة ٥."
            )
            window.setMinimumSize(520, 320)
            window.setWindowTitle(f"{APP_NAME} {APP_VERSION}")

        self.window = window
        window.show()

    # ── transitions ──────────────────────────────────────────────────────────

    def _on_activated(self, _result) -> None:
        self._restart()

    def _restart(self) -> None:
        self.show_for(start(self.client))

    def _force_activation(self) -> None:
        from .security import credentials

        credentials.clear()
        self._restart()


def main() -> int:
    configure_logging()
    logger.info("Starting %s %s", APP_NAME, APP_VERSION)

    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName(APP_NAME)
    qt_app.setApplicationVersion(APP_VERSION)
    theme.apply(qt_app)

    return Application(qt_app).run()


if __name__ == "__main__":
    sys.exit(main())
