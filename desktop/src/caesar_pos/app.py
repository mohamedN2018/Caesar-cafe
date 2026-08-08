"""
Application entry point.

    python -m caesar_pos

Renders whatever `bootstrap.start()` decided, then owns exactly one thing the
rest of the code deliberately does not: the transitions between screens.

    activation ──▶ login ──▶ shell (POS · floor · kitchen · kids)
         ▲           ▲                      │
         └── blocked ┴──────── logout ──────┘

The order of those screens is not cosmetic. **The desktop does not open until it
is activated** (§2), and nothing behind the licence gate is even constructed
until the gate has passed — so a build with a broken gate fails closed, with no
window to fall back to.

The database is opened once, after activation, and handed to everything. One
connection per thread; the sync engine, the boards and the print queue all share
this one because they all run on the Qt thread.
"""

from __future__ import annotations

import logging
import os
import sys

from PySide6.QtWidgets import QApplication

from .api.client import ApiClient
from .bootstrap import Screen, Startup, start
from .config import APP_NAME, APP_VERSION, paths
from .local.db import Database, connect
from .printing.spooler import EscposPrinter
from .security.session import Authenticator, settings_from_mirror
from .sync.engine import SyncEngine
from .ui import theme
from .ui.activation import ActivationWindow
from .ui.blocked.window import BlockedWindow
from .ui.login.window import LoginWindow
from .ui.shell import Shell

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
        self.db: Database | None = None
        self.engine: SyncEngine | None = None
        self.startup: Startup | None = None

    def run(self) -> int:
        self.show_for(start(self.client))
        return self.qt.exec()

    def show_for(self, startup: Startup) -> None:
        self.startup = startup

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
            window = self._login_window(startup)

        self.window = window
        window.show()

    # ── behind the gate ──────────────────────────────────────────────────────

    def _login_window(self, startup: Startup) -> LoginWindow:
        """
        The first screen that touches local data.

        The database and the sync engine are created HERE, not in `__init__`:
        nothing behind the licence gate is constructed until the gate has passed,
        so a build with a broken gate fails closed rather than falling through to
        a usable till.
        """
        db = self._ensure_db()
        engine = self._ensure_engine(db)

        attempts, lockout = settings_from_mirror(db)
        window = LoginWindow(
            Authenticator(db, max_attempts=attempts, lockout_seconds=lockout),
            sync_label=str(engine.status()),
        )
        window.logged_in.connect(self._on_logged_in)

        if not startup.online:
            logger.info("Starting offline on the cached licence")

        return window

    def _ensure_db(self) -> Database:
        if self.db is None:
            self.db = Database(connect())
        return self.db

    def _ensure_engine(self, db: Database) -> SyncEngine:
        if self.engine is None:
            self.engine = SyncEngine(db=db, client=self.client)
        return self.engine

    def _on_logged_in(self, session) -> None:
        db = self._ensure_db()
        gate = self.startup.gate if self.startup else None

        shell = Shell(
            db,
            session,
            self._ensure_engine(db),
            printer=EscposPrinter(),
            # A RESTRICTED terminal opens and settles, but starts nothing new.
            can_open_new_orders=gate.can_open_new_orders if gate else True,
        )
        shell.logout_requested.connect(self._on_logout)

        if self.window is not None:
            self.window.close()
            self.window.deleteLater()

        self.window = shell
        shell.show()

    def _on_logout(self) -> None:
        """
        Back to the PIN pad on the SAME startup decision, not a restart.

        Re-running the startup sequence would hit the network on every shift
        change and could strand the next cashier behind a licence check during an
        outage. The licence was verified when the terminal opened; a change of
        person is not a change of device.

        The gate that is reused is the real one, including a RESTRICTED verdict —
        logging out must not be a way to upgrade a restricted terminal.
        """
        if self.startup is None:
            self._restart()
            return
        self.show_for(self.startup)

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
