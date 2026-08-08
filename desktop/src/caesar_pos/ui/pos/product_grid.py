"""
The product grid.

The most-tapped surface in the product, so the decisions are about speed under
pressure rather than looks:

  * **Tiles carry the price.** A cashier answering "how much is that?" should not
    have to add it to the order to find out.
  * **Category tabs, not a dropdown.** One tap instead of two, and the whole set
    is visible — a dropdown hides how many categories there are.
  * **Search is there but not first.** During a rush nobody types; the grid is
    the fast path. Search exists for the long tail on a 200-item menu.
  * **The admin's `sort_order` is honoured, never re-sorted alphabetically.** The
    layout is a decision the manager made about what staff reach for, and muscle
    memory is what makes a busy hour survivable.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .catalog import Category, Tile

TILE_MIN_WIDTH = 150
TILE_MIN_HEIGHT = 96
COLUMNS = 4

STYLESHEET = f"""
QPushButton#Tile {{
    background: #ffffff;
    color: #0f172a;
    border: 1px solid #cbd5e1;
    border-radius: 12px;
    padding: 10px;
    font-size: 15px;
    font-weight: 600;
    text-align: center;
    min-width: {TILE_MIN_WIDTH}px;
    min-height: {TILE_MIN_HEIGHT}px;
}}
QPushButton#Tile:pressed {{ background: #dbeafe; border-color: #1d4e89; }}
QPushButton#CategoryTab {{
    background: #e2e8f0;
    color: #0f172a;
    border: none;
    border-radius: 8px;
    padding: 10px 16px;
    font-size: 15px;
}}
QPushButton#CategoryTabActive {{
    background: #1d4e89;
    color: #ffffff;
    border: none;
    border-radius: 8px;
    padding: 10px 16px;
    font-size: 15px;
}}
QLabel#GridEmpty {{ color: #64748b; font-size: 15px; }}
"""


class ProductGrid(QWidget):
    """Emits `chosen(Tile)` — the caller decides whether a variant chooser opens."""

    chosen = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(STYLESHEET)

        self._categories: list[Category] = []
        self._tiles: list[Tile] = []
        self.active_category: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.search = QLineEdit(placeholderText="ابحث عن صنف…")
        self.search.textChanged.connect(lambda _: self._render())
        layout.addWidget(self.search)

        self.tabs = QHBoxLayout()
        self.tabs.setSpacing(8)
        tab_holder = QWidget()
        tab_holder.setLayout(self.tabs)
        layout.addWidget(tab_holder)

        self.empty = QLabel("لا توجد أصناف", objectName="GridEmpty")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.hide()
        layout.addWidget(self.empty)

        self.grid = QGridLayout()
        self.grid.setSpacing(10)
        holder = QWidget()
        holder.setLayout(self.grid)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(holder)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        layout.addWidget(scroll, stretch=1)

    # ── data ─────────────────────────────────────────────────────────────────

    def load(self, categories: list[Category], tiles: list[Tile]) -> None:
        self._categories = categories
        self._tiles = tiles
        self._render_tabs()
        self._render()

    def select_category(self, category_id: str | None) -> None:
        self.active_category = category_id
        self._render_tabs()
        self._render()

    @property
    def visible_tiles(self) -> list[Tile]:
        term = self.search.text().strip()
        tiles = self._tiles

        if self.active_category:
            tiles = [t for t in tiles if t.category_id == self.active_category]
        if term:
            # Search spans every category. Someone typing a name is looking for
            # a product, not for a product within the tab they happen to be on.
            tiles = [t for t in self._tiles if term in t.name_ar]

        return tiles

    # ── rendering ────────────────────────────────────────────────────────────

    def _render_tabs(self) -> None:
        _clear(self.tabs)

        entries = [(None, "الكل"), *[(c.id, c.name_ar) for c in self._categories]]
        for category_id, label in entries:
            button = QPushButton(label)
            button.setObjectName(
                "CategoryTabActive" if category_id == self.active_category else "CategoryTab"
            )
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _=False, cid=category_id: self.select_category(cid))
            self.tabs.addWidget(button)

        self.tabs.addStretch(1)

    def _render(self) -> None:
        _clear(self.grid)
        tiles = self.visible_tiles

        self.empty.setVisible(not tiles)

        for index, tile in enumerate(tiles):
            # Price on the tile: "how much is that?" is answered without adding
            # the item to find out.
            button = QPushButton(f"{tile.name_ar}\n{tile.price} ج.م")
            button.setObjectName("Tile")
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _=False, t=tile: self.chosen.emit(t))
            self.grid.addWidget(button, index // COLUMNS, index % COLUMNS)


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if (widget := item.widget()) is not None:
            widget.deleteLater()
