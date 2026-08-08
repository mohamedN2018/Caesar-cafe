"""
The shell: one window, several boards, three background timers.

Everything up to now has been a piece — a fold, an outbox, a grid, a queue. This
is where they become an application, and the decisions worth stating are about
what runs on a timer and what does not.

**Three timers, three different reasons.**

  * *Sync* — every few seconds, because the outbox is what costs money to delay.
  * *Printing* — separately, because a jammed printer must not slow the till, and
    a busy sync must not delay a receipt. Coupling them means the customer waits
    for the network.
  * *Boards* — the floor map, the kitchen and the kids area re-read local state
    on their own beat. They read the mirror, so a tick is cheap and an outage
    only makes them show this terminal's own view — which the header says.

**Nothing blocks on the network.** Every timer callback either reads SQLite or
calls the engine, which is itself offline-tolerant. A tick that raises is logged
and swallowed: a POS that dies because one poll failed is worse than a POS that
is briefly stale.

**The header is permanent and never optimistic.** Who is on the till, the sync
state, and the print backlog if there is one. A terminal that has been queueing
since Tuesday says so on every screen, including the login screen.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..api.client import ApiError, NetworkUnavailable
from ..local.db import Database
from ..orders import service
from ..printing import spooler
from ..security.session import Session
from ..sync.engine import SyncEngine
from .floor.window import FloorWindow
from .kids.window import KidsWindow
from .kitchen.window import KitchenWindow
from .pos.window import PosWindow

logger = logging.getLogger(__name__)

#: Milliseconds. The outbox drains often because an unsent payment is money the
#: owner cannot see; the boards refresh less often because they read local state
#: that only this terminal changed.
SYNC_INTERVAL_MS = 5_000
PRINT_INTERVAL_MS = 3_000
BOARD_INTERVAL_MS = 10_000

STYLESHEET = """
QWidget#ShellHeader { background: #ffffff; border-bottom: 1px solid #e2e8f0; }
QLabel#ShellUser  { font-size: 15px; font-weight: 600; }
QLabel#ShellSync  { font-size: 14px; color: #475569; }
QLabel#ShellPrint { font-size: 14px; color: #b45309; font-weight: 600; }
QPushButton#Tab       { background: #e2e8f0; color: #0f172a; padding: 10px 18px; }
QPushButton#TabActive { background: #1d4e89; color: #ffffff; padding: 10px 18px; }
QPushButton#Logout    { background: #e2e8f0; color: #0f172a; padding: 8px 14px; }
"""

#: Board → the permission that reveals its tab. A cook has no business on the
#: till, and the tab is hidden rather than disabled so the screen stays readable.
#: This is a UI decision only: the server authorises every operation again.
BOARD_PERMISSIONS = {
    "pos": "orders.create",
    "floor": "floor.view",
    "kitchen": "kitchen.view",
    "kids": "kids.view",
}

#: The board thinks in states; the API takes verbs (`/tickets/<id>/start/`).
#: Translated here, once, rather than teaching the board a URL vocabulary — and
#: an unmapped state is refused locally rather than sent as a guessed path.
TICKET_ACTIONS = {
    "ACCEPTED": "accept",
    "PREPARING": "start",
    "READY": "ready",
    "SERVED": "served",
    "CANCELLED": "cancel",
}

BOARD_LABELS = {
    "pos": "نقطة البيع",
    "floor": "الطاولات",
    "kitchen": "المطبخ",
    "kids": "صالة الأطفال",
}


class NotSyncedBoard(QWidget):
    """
    Stands in for a board whose data has not arrived.

    Carries the same small surface the shell calls on every board, so a missing
    catalog produces one honest sentence rather than a stream of AttributeErrors
    in the log and a blank rectangle on the screen.
    """

    def __init__(self, reason: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.reason = reason
        self.order_id = None

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        message = QLabel("لم تكتمل المزامنة بعد — لا يمكن فتح نقطة البيع.")
        message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        message.setWordWrap(True)
        layout.addWidget(message)

    def refresh_catalog(self) -> None:
        return

    def set_sync_label(self, text: str) -> None:
        return


class Shell(QWidget):
    """
    Emits `logout_requested`. The app owns what happens after.

    Constructed with the boards the session may actually see. A shell that built
    all four and hid three would still have four live timers refreshing screens
    nobody can open.
    """

    logout_requested = Signal()

    def __init__(
        self,
        db: Database,
        session: Session,
        engine: SyncEngine,
        *,
        printer=None,
        boards: list[str] | None = None,
        can_open_new_orders: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.session = session
        self.engine = engine
        self.printer = printer
        self.can_open_new_orders = can_open_new_orders

        self.setStyleSheet(STYLESHEET)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setWindowTitle("كافيه القيصر")
        self.setMinimumSize(1100, 720)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.stack = QStackedWidget()
        self.boards: dict[str, QWidget] = {}
        self.tabs: dict[str, QPushButton] = {}

        layout.addWidget(self._header())
        layout.addWidget(self.stack, stretch=1)

        for name in boards if boards is not None else available_boards(session):
            self._add_board(name)

        if self.boards:
            self.show_board(next(iter(self.boards)))

        self.refresh_status()
        self._start_timers()

    # ── construction ─────────────────────────────────────────────────────────

    def _header(self) -> QWidget:
        header = QWidget(objectName="ShellHeader")
        row = QHBoxLayout(header)
        row.setContentsMargins(16, 10, 16, 10)
        row.setSpacing(10)

        self.tab_row = QHBoxLayout()
        holder = QWidget()
        holder.setLayout(self.tab_row)
        row.addWidget(holder)
        row.addStretch(1)

        # Only shown when there IS a backlog. A permanent "printer OK" is a label
        # staff stop reading, and then the one time it says otherwise nobody sees.
        self.print_label = QLabel("", objectName="ShellPrint")
        self.print_label.hide()
        row.addWidget(self.print_label)

        self.sync_label = QLabel("", objectName="ShellSync")
        row.addWidget(self.sync_label)

        row.addWidget(QLabel(self.session.full_name_ar, objectName="ShellUser"))

        logout = QPushButton("خروج", objectName="Logout")
        logout.clicked.connect(self.logout_requested.emit)
        row.addWidget(logout)

        return header

    def _add_board(self, name: str) -> None:
        widget = self._build_board(name)
        if widget is None:
            return

        self.boards[name] = widget
        self.stack.addWidget(widget)

        tab = QPushButton(BOARD_LABELS[name], objectName="Tab")
        tab.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        tab.clicked.connect(lambda _=False, n=name: self.show_board(n))
        self.tab_row.addWidget(tab)
        self.tabs[name] = tab

    def _build_board(self, name: str) -> QWidget | None:
        if name == "pos":
            try:
                settings = service.settings_from_mirror(self.db)
            except RuntimeError as exc:
                # A terminal whose config has not pulled yet cannot price an
                # order, and `settings_from_mirror` rightly refuses to guess. The
                # shell still opens: the kitchen and the kids area work from
                # local state, and the till says what is missing instead of the
                # whole application failing to start.
                logger.error("Cannot build the till", extra={"reason": str(exc)})
                return NotSyncedBoard(str(exc))

            window = PosWindow(
                self.db,
                self.session,
                settings,
                can_open_new_orders=self.can_open_new_orders,
            )
            # The POS carries its own header for standalone use; inside the shell
            # the shell's header is the one that stays, so its logout is routed
            # here rather than leaving two different ways out.
            window.logout_requested.connect(self.logout_requested.emit)
            return window

        if name == "floor":
            window = FloorWindow(self.db)
            window.table_chosen.connect(self._open_table)
            return window

        if name == "kitchen":
            window = KitchenWindow(can_advance=self.session.can("kitchen.update_status"))
            window.advance.connect(self._advance_ticket)
            return window

        if name == "kids":
            return KidsWindow(self.db)

        return None

    def _start_timers(self) -> None:
        self.sync_timer = _timer(self, SYNC_INTERVAL_MS, self.tick_sync)
        self.print_timer = _timer(self, PRINT_INTERVAL_MS, self.tick_print)
        self.board_timer = _timer(self, BOARD_INTERVAL_MS, self.tick_boards)

    # ── navigation ───────────────────────────────────────────────────────────

    def show_board(self, name: str) -> None:
        widget = self.boards.get(name)
        if widget is None:
            return

        self.stack.setCurrentWidget(widget)
        for board_name, tab in self.tabs.items():
            tab.setObjectName("TabActive" if board_name == name else "Tab")
            # Qt does not restyle on an objectName change on its own.
            tab.style().polish(tab)

        self.refresh_board(name)

    @property
    def current_board(self) -> str:
        for name, widget in self.boards.items():
            if widget is self.stack.currentWidget():
                return name
        return ""

    def _open_table(self, table_id: str, order_id) -> None:
        """A tap on the floor map lands on the till with that table's order."""
        pos = self.boards.get("pos")
        if pos is None:
            return

        if order_id:
            pos.order_id = order_id
            pos.panel.show_order(service.load(self.db, order_id))
        elif not self.can_open_new_orders:
            # A restricted licence still settles what is already on the tables;
            # seating a new one is the thing it refuses (C5).
            QMessageBox.warning(
                self,
                "الترخيص مقيّد",
                "لا يمكن فتح طلب جديد — يمكن إنهاء الطلبات المفتوحة فقط.",
            )
            return
        else:
            order = service.open_order(
                self.db, settings=service.settings_from_mirror(self.db), table_id=table_id
            )
            pos.order_id = order.order_id
            pos.panel.show_order(order)

        self.show_board("pos")

    def _advance_ticket(self, ticket_id: str, status: str) -> None:
        """
        The state change goes to the server, which owns kitchen state.

        Unlike an order, a ticket transition is NOT queued offline: two cooks on
        two terminals advancing the same ticket while disconnected would produce
        a conflict with no sensible resolution, and the board is only useful when
        it agrees with the other screens. Offline, the tap is refused out loud.
        """
        action = TICKET_ACTIONS.get(status)
        if action is None:
            logger.error("No API action for ticket state", extra={"state": status})
            return

        try:
            self.engine.client.post(f"/kitchen/tickets/{ticket_id}/{action}/")
        except NetworkUnavailable:
            QMessageBox.warning(
                self, "غير متصل", "شاشة المطبخ تحتاج اتصالاً بالخادم لتحديث حالة التذكرة."
            )
        except ApiError as exc:
            QMessageBox.warning(self, "تعذّر التنفيذ", str(exc))
        else:
            self.tick_boards()

    # ── the timers ───────────────────────────────────────────────────────────

    def tick_sync(self) -> None:
        try:
            self.engine.tick()
        except Exception:
            logger.exception("Sync tick failed")
        self.refresh_status()

    def tick_print(self) -> None:
        if self.printer is None:
            return
        try:
            spooler.drain(self.db, self.printer)
        except Exception:
            logger.exception("Print drain failed")
        self.refresh_status()

    def tick_boards(self) -> None:
        """
        Only the visible board is refreshed.

        Rebuilding four boards on a ten-second timer is work nobody sees, and on
        the hardware these run on it is visible as jitter on the one board that
        is open.
        """
        self.refresh_board(self.current_board)

    def refresh_board(self, name: str) -> None:
        widget = self.boards.get(name)
        if widget is None:
            return

        try:
            if name == "floor":
                widget.refresh()
            elif name == "kids":
                widget.refresh()
            elif name == "kitchen":
                self._load_tickets(widget)
            elif name == "pos":
                widget.refresh_catalog()
        except Exception:
            logger.exception("Board refresh failed", extra={"board": name})

    def _load_tickets(self, board: KitchenWindow) -> None:
        try:
            data = self.engine.client.get("/kitchen/tickets/", params={"status": "OPEN"})
        except (NetworkUnavailable, ApiError):
            # The board keeps what it has and says the link is down. Blanking it
            # would lose tickets a cook is still working from.
            board.set_connection(live=False)
            return

        board.set_connection(live=True)
        board.show_tickets(data.get("results", data) if isinstance(data, dict) else data)

    def refresh_status(self) -> None:
        status = self.engine.status()
        self.sync_label.setText(str(status))

        pos = self.boards.get("pos")
        if pos is not None:
            pos.set_sync_label(str(status))

        counts = spooler.counts(self.db)
        backlog = counts["pending"] + counts["failed"]
        self.print_label.setVisible(bool(backlog))
        if backlog:
            self.print_label.setText(f"🖨 في انتظار الطباعة ({backlog})")

    def closeEvent(self, event) -> None:
        for timer in (self.sync_timer, self.print_timer, self.board_timer):
            timer.stop()
        super().closeEvent(event)


def available_boards(session: Session) -> list[str]:
    """
    Which tabs this person gets.

    A UI decision, and only a UI decision — §62's rule is that the desktop is
    never trusted for authorisation. Hiding the kitchen tab from a cashier is
    about a readable screen; the server refuses the operation regardless.
    """
    return [name for name, permission in BOARD_PERMISSIONS.items() if session.can(permission)]


def _timer(parent: QWidget, interval_ms: int, callback) -> QTimer:
    timer = QTimer(parent)
    timer.setInterval(interval_ms)
    timer.timeout.connect(callback)
    timer.start()
    return timer


def started_at_label(session: Session, *, now: datetime | None = None) -> str:
    """How long this person has been on the till — a shift-change prompt."""
    minutes = int(((now or datetime.now(UTC)) - session.started_at).total_seconds() // 60)
    return f"{minutes // 60}:{minutes % 60:02d}"
