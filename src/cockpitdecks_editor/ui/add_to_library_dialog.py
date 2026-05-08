"""Dialog for tagging and saving a button to the curated library."""
from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cockpitdecks_editor.services.library_scanner import _button_label, _extract_datarefs, _portability
from cockpitdecks_editor.services.library_storage import CuratedEntry

_CATEGORIES = [
    "Radio", "Engine", "Autopilot", "Electrical", "Lights",
    "Anti-Ice", "FMS/Nav", "Weather", "Navigation", "Ground",
    "Displays", "Encoders", "Other",
]


class _TagWidget(QWidget):
    """Editable tag chips row."""

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tags: list[str] = []
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Add tag, press Enter…")
        self._input.setStyleSheet("font-size: 11px;")
        self._input.returnPressed.connect(self._add_current)
        self._layout.addWidget(self._input, 1)

        self._chip_row = QHBoxLayout()
        self._chip_row.setContentsMargins(0, 0, 0, 0)
        self._chip_row.setSpacing(4)
        self._layout.insertLayout(0, self._chip_row)

    def tags(self) -> list[str]:
        return list(self._tags)

    def set_tags(self, tags: list[str]) -> None:
        self._tags = []
        while self._chip_row.count():
            item = self._chip_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for tag in tags:
            self._add_chip(tag)

    def _add_current(self) -> None:
        text = self._input.text().strip().lower()
        text = re.sub(r"[^a-z0-9_-]", "-", text).strip("-")
        if text and text not in self._tags:
            self._add_chip(text)
        self._input.clear()

    def _add_chip(self, tag: str) -> None:
        self._tags.append(tag)
        chip = QFrame()
        chip.setStyleSheet(
            "QFrame { background: #334155; border-radius: 3px; }"
        )
        row = QHBoxLayout(chip)
        row.setContentsMargins(6, 2, 4, 2)
        row.setSpacing(4)
        lbl = QLabel(tag)
        lbl.setStyleSheet("color: #f1f5f9; font-size: 10px; background: transparent; border: none;")
        row.addWidget(lbl)
        x_btn = QPushButton("×")
        x_btn.setFixedSize(14, 14)
        x_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #94a3b8; border: none; font-size: 12px; }"
            "QPushButton:hover { color: #f87171; }"
        )
        x_btn.clicked.connect(lambda checked=False, t=tag, c=chip: self._remove(t, c))
        row.addWidget(x_btn)
        self._chip_row.addWidget(chip)

    def _remove(self, tag: str, chip: QFrame) -> None:
        if tag in self._tags:
            self._tags.remove(tag)
        chip.deleteLater()


class AddToLibraryDialog(QDialog):
    def __init__(
        self,
        button_data: dict,
        *,
        source_aircraft: str = "",
        source_page: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add to Library")
        self.setMinimumWidth(440)
        self._button_data = button_data
        self._source_aircraft = source_aircraft
        self._source_page = source_page
        self._entry: CuratedEntry | None = None

        root = QVBoxLayout(self)
        root.setSpacing(12)

        # Info strip
        info = QLabel(f"From  <b>{source_aircraft}</b>  ·  {source_page}")
        info.setStyleSheet("color: #64748b; font-size: 11px;")
        root.addWidget(info)

        form = QFormLayout()
        form.setSpacing(8)

        # Name
        suggested = _button_label(button_data) or button_data.get("name", "")
        self._name_edit = QLineEdit(suggested)
        self._name_edit.setPlaceholderText("Button name…")
        form.addRow("Name", self._name_edit)

        # Category
        self._cat_combo = QComboBox()
        for cat in _CATEGORIES:
            self._cat_combo.addItem(cat)
        form.addRow("Category", self._cat_combo)

        # Tags
        self._tag_widget = _TagWidget()
        initial_tags: list[str] = []
        rep = button_data.get("representation") or {}
        if isinstance(rep, dict):
            rep_type = str(rep.get("type") or "")
            if rep_type:
                initial_tags.append(rep_type.replace("-", "_"))
        act = button_data.get("activation") or {}
        if isinstance(act, dict):
            act_type = str(act.get("type") or "")
            if act_type:
                initial_tags.append(act_type.replace("-", "_"))
        self._tag_widget.set_tags(initial_tags)
        form.addRow("Tags", self._tag_widget)

        root.addLayout(form)

        # Portability note
        datarefs = _extract_datarefs(button_data)
        port = _portability(datarefs)
        port_color = "#16a34a" if port == "universal" else "#d97706"
        port_lbl = QLabel(f"● {port.capitalize()}")
        port_lbl.setStyleSheet(f"color: {port_color}; font-size: 10px;")
        root.addWidget(port_lbl)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _accept(self) -> None:
        name = self._name_edit.text().strip()
        if not name:
            self._name_edit.setFocus()
            return
        datarefs = _extract_datarefs(self._button_data)
        self._entry = CuratedEntry(
            id=CuratedEntry.make_id(self._button_data, self._source_aircraft),
            name=name,
            category=self._cat_combo.currentText(),
            tags=self._tag_widget.tags(),
            source_aircraft=self._source_aircraft,
            source_page=self._source_page,
            button_data=self._button_data,
            portability=_portability(datarefs),
        )
        self.accept()

    def entry(self) -> CuratedEntry | None:
        return self._entry
