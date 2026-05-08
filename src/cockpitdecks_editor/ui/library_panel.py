"""Button Library panel — curated, tagged, persistent building blocks."""
from __future__ import annotations

import json

import yaml
from PySide6.QtCore import QMimeData, QPoint, Qt, Signal
from PySide6.QtGui import QDrag, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from cockpitdecks_editor.services.library_storage import CuratedEntry, load_library, remove_entry

_BUTTON_MIME = "application/x-cockpitdecks-button"
_LIB_DRAG_PREFIX = "__lib__:"

_CATEGORY_COLORS: dict[str, str] = {
    "Radio": "#1e40af",
    "Engine": "#92400e",
    "Autopilot": "#065f46",
    "Electrical": "#1e3a5f",
    "Lights": "#4a1d96",
    "Anti-Ice": "#1e4a6e",
    "FMS/Nav": "#064e3b",
    "Weather": "#0c4a6e",
    "Navigation": "#374151",
    "Ground": "#451a03",
    "Displays": "#1a1a3e",
    "Encoders": "#3b1f5e",
    "Other": "#1f2937",
}

_PORTABILITY_COLOR = {
    "universal": "#16a34a",
    "aircraft-specific": "#d97706",
}


class _LibraryCard(QFrame):
    remove_requested = Signal(str)   # entry id
    edit_requested = Signal(str)     # entry id — open in button designer

    def __init__(self, entry: CuratedEntry, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.entry = entry
        self._drag_start: QPoint | None = None

        self.setFixedHeight(72)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setStyleSheet(
            "QFrame { background: #1e293b; border: 1px solid #334155; border-radius: 6px; }"
            "QFrame:hover { border-color: #60a5fa; background: #1e3a5f; }"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        # Category badge
        cat_color = _CATEGORY_COLORS.get(entry.category, "#1f2937")
        cat_badge = QLabel(entry.category)
        cat_badge.setFixedWidth(70)
        cat_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cat_badge.setStyleSheet(
            f"background: {cat_color}; color: white; font-size: 9px; font-weight: 700;"
            " border-radius: 3px; padding: 2px 4px;"
        )
        layout.addWidget(cat_badge)

        # Info column
        info = QVBoxLayout()
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(2)

        name_lbl = QLabel(entry.name)
        name_lbl.setStyleSheet(
            "color: #f1f5f9; font-size: 12px; font-weight: 600;"
            " background: transparent; border: none;"
        )
        info.addWidget(name_lbl)

        src_lbl = QLabel(f"{entry.source_aircraft}  ·  {entry.source_page}")
        src_lbl.setStyleSheet("color: #64748b; font-size: 10px; background: transparent; border: none;")
        info.addWidget(src_lbl)

        if entry.tags:
            tags_lbl = QLabel("  ".join(f"#{t}" for t in entry.tags))
            tags_lbl.setStyleSheet("color: #475569; font-size: 9px; background: transparent; border: none;")
            info.addWidget(tags_lbl)

        layout.addLayout(info, 1)

        # Right column: portability + copy
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(4)

        port_color = _PORTABILITY_COLOR.get(entry.portability, "#6b7280")
        port_dot = QLabel("●")
        port_dot.setToolTip(entry.portability)
        port_dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        port_dot.setStyleSheet(f"color: {port_color}; font-size: 12px; background: transparent; border: none;")
        right.addWidget(port_dot)

        copy_btn = QPushButton("Copy")
        copy_btn.setFixedSize(40, 22)
        copy_btn.setToolTip("Copy to clipboard — then paste into any grid slot")
        copy_btn.setStyleSheet(
            "QPushButton { background: #334155; color: #94a3b8; font-size: 9px;"
            " border: none; border-radius: 3px; }"
            "QPushButton:hover { background: #475569; color: white; }"
        )
        copy_btn.clicked.connect(self._copy_to_clipboard)
        right.addWidget(copy_btn)

        layout.addLayout(right)

    def _copy_to_clipboard(self) -> None:
        mime = QMimeData()
        try:
            payload = json.dumps(self.entry.button_data, ensure_ascii=True).encode("utf-8")
            mime.setData(_BUTTON_MIME, payload)
            mime.setText(yaml.safe_dump(self.entry.button_data, sort_keys=False, allow_unicode=False))
        except Exception:
            return
        QApplication.clipboard().setMimeData(mime)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: #1e293b; border: 1px solid #475569;"
            " border-radius: 6px; padding: 4px; color: #f1f5f9; }"
            "QMenu::item { padding: 6px 24px 6px 12px; border-radius: 3px; font-size: 13px; }"
            "QMenu::item:selected { background: #3b82f6; color: white; }"
            "QMenu::separator { height: 1px; background: #334155; margin: 4px 8px; }"
        )
        edit_action = menu.addAction("Edit Button…")
        copy_action = menu.addAction("Copy to Clipboard")
        menu.addSeparator()
        remove_action = menu.addAction("Remove from Library")
        chosen = menu.exec(event.globalPos())
        if chosen is edit_action:
            self.edit_requested.emit(self.entry.id)
        elif chosen is copy_action:
            self._copy_to_clipboard()
        elif chosen is remove_action:
            self.remove_requested.emit(self.entry.id)

    # ── Drag support ──────────────────────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_start is None:
            return
        if (event.pos() - self._drag_start).manhattanLength() < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return
        self._drag_start = None
        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(_LIB_DRAG_PREFIX + self.entry.id)
        try:
            payload = json.dumps(self.entry.button_data, ensure_ascii=True).encode("utf-8")
            mime.setData(_BUTTON_MIME, payload)
        except Exception:
            pass
        drag.setMimeData(mime)
        drag.setPixmap(self.grab())
        drag.setHotSpot(event.pos())
        drag.exec(Qt.DropAction.CopyAction)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_start = None
        super().mouseReleaseEvent(event)


class LibraryPanel(QWidget):
    """Curated button library panel."""

    edit_requested = Signal(str)   # entry id — open in button designer

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entries: list[CuratedEntry] = []
        self._filtered: list[CuratedEntry] = []
        self._active_category = "All"
        self._cards: dict[str, _LibraryCard] = {}

        self.setMinimumWidth(260)
        self.setMaximumWidth(380)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ── Header ────────────────────────────────────────────────────────────
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Button Library")
        title.setStyleSheet("font-size: 13px; font-weight: 700; color: #f1f5f9;")
        header.addWidget(title, 1)
        root.addLayout(header)

        # ── Search ────────────────────────────────────────────────────────────
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search name, tag, aircraft…")
        self.search_edit.setStyleSheet(
            "QLineEdit { background: #0f172a; color: #f1f5f9; border: 1px solid #334155;"
            " border-radius: 4px; padding: 4px 8px; font-size: 12px; }"
            "QLineEdit:focus { border-color: #60a5fa; }"
        )
        self.search_edit.textChanged.connect(self._refilter)
        root.addWidget(self.search_edit)

        # ── Category filter ───────────────────────────────────────────────────
        self._cat_scroll = QScrollArea()
        self._cat_scroll.setFixedHeight(30)
        self._cat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._cat_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._cat_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._cat_scroll.setWidgetResizable(True)
        cat_host = QWidget()
        self._cat_layout = QHBoxLayout(cat_host)
        self._cat_layout.setContentsMargins(0, 0, 0, 0)
        self._cat_layout.setSpacing(4)
        self._cat_layout.addStretch(1)
        self._cat_scroll.setWidget(cat_host)
        self._cat_buttons: dict[str, QPushButton] = {}
        root.addWidget(self._cat_scroll)

        # ── Count ─────────────────────────────────────────────────────────────
        self.count_label = QLabel("Library is empty — right-click a button in the grid to add it")
        self.count_label.setWordWrap(True)
        self.count_label.setStyleSheet("color: #64748b; font-size: 10px;")
        root.addWidget(self.count_label)

        # ── Card list ─────────────────────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self._card_host = QWidget()
        self._card_host.setStyleSheet("background: transparent;")
        self._card_layout = QVBoxLayout(self._card_host)
        self._card_layout.setContentsMargins(0, 0, 0, 0)
        self._card_layout.setSpacing(4)
        self._card_layout.addStretch(1)
        self._scroll.setWidget(self._card_host)
        root.addWidget(self._scroll, 1)

        self.setStyleSheet("LibraryPanel { background: #0f172a; }")
        self.reload()

    # ── Public API ────────────────────────────────────────────────────────────

    def reload(self) -> None:
        """Reload from persistent storage."""
        self._entries = load_library()
        self._rebuild_category_bar()
        self._refilter()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _rebuild_category_bar(self) -> None:
        while self._cat_layout.count() > 1:
            item = self._cat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._cat_buttons.clear()

        categories = ["All"] + sorted({e.category for e in self._entries})
        for cat in categories:
            btn = QPushButton(cat)
            btn.setCheckable(True)
            btn.setFixedHeight(22)
            color = _CATEGORY_COLORS.get(cat, "#374151")
            btn.setStyleSheet(
                f"QPushButton {{ background: #1e293b; color: #94a3b8; font-size: 10px;"
                f" border: 1px solid #334155; border-radius: 3px; padding: 0 6px; }}"
                f"QPushButton:checked {{ background: {color}; color: white; border-color: {color}; }}"
                f"QPushButton:hover {{ background: #334155; }}"
            )
            btn.clicked.connect(lambda checked, c=cat: self._set_category(c))
            self._cat_buttons[cat] = btn
            self._cat_layout.insertWidget(self._cat_layout.count() - 1, btn)

        if "All" in self._cat_buttons:
            self._cat_buttons["All"].setChecked(True)
        self._active_category = "All"

    def _set_category(self, cat: str) -> None:
        self._active_category = cat
        for key, btn in self._cat_buttons.items():
            btn.setChecked(key == cat)
        self._refilter()

    def _refilter(self, *_args) -> None:
        query = self.search_edit.text().strip().lower()
        cat = self._active_category
        self._filtered = [
            e for e in self._entries
            if (cat == "All" or e.category == cat)
            and (
                not query
                or query in e.name.lower()
                or query in e.source_aircraft.lower()
                or query in e.source_page.lower()
                or any(query in t for t in e.tags)
            )
        ]
        self._rebuild_cards()
        if self._entries:
            self.count_label.setText(f"{len(self._filtered)} of {len(self._entries)} buttons")
        else:
            self.count_label.setText("Library is empty — right-click a button in the grid to add it")

    def _rebuild_cards(self) -> None:
        while self._card_layout.count() > 1:
            item = self._card_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._cards.clear()

        for entry in self._filtered:
            card = _LibraryCard(entry)
            card.remove_requested.connect(self._remove_entry)
            card.edit_requested.connect(self.edit_requested)
            self._card_layout.insertWidget(self._card_layout.count() - 1, card)
            self._cards[entry.id] = card

    def _remove_entry(self, entry_id: str) -> None:
        remove_entry(entry_id)
        self.reload()
