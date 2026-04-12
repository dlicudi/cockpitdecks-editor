"""Dataref Navigator tab.

Browse and search X-Plane datarefs and commands loaded from DataRefs.txt /
Commands.txt.  Click any row to copy the name to the clipboard.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from cockpitdecks_editor.services.xplane_refs import (
    CommandRecord,
    DatarefRecord,
    default_commands_path,
    default_datarefs_path,
    default_drt_commands_path,
    default_drt_datarefs_path,
    parse_commands,
    parse_datarefs,
    parse_drt_commands,
    parse_drt_datarefs,
)


# ── Column definitions ────────────────────────────────────────────────────────

_COL_KIND = 0    # Dataref / Command
_COL_NAME = 1
_COL_TYPE = 2
_COL_WRITE = 3
_COL_UNITS = 4
_COL_DESC = 5
_NUM_COLS = 6

_HEADERS = ["Kind", "Name", "Type", "Writable", "Units", "Description"]

_KIND_DATAREF = "Dataref"
_KIND_COMMAND = "Command"


# ── Table model ───────────────────────────────────────────────────────────────

class _Row:
    __slots__ = ("kind", "name", "dtype", "writable", "units", "description", "is_array", "source")

    def __init__(
        self,
        kind: str,
        name: str,
        dtype: str = "",
        writable: bool = False,
        units: str = "",
        description: str = "",
        is_array: bool = False,
        source: str = "xplane",   # "xplane" or "plugin"
    ) -> None:
        self.kind = kind
        self.name = name
        self.dtype = dtype
        self.writable = writable
        self.units = units
        self.description = description
        self.is_array = is_array
        self.source = source


class _RefsModel(QAbstractTableModel):
    def __init__(self, rows: list[_Row], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows = rows

    # ── QAbstractTableModel interface ─────────────────────────────────────────

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return _NUM_COLS

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return _HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == _COL_KIND:
                return row.kind
            if col == _COL_NAME:
                return row.name
            if col == _COL_TYPE:
                return row.dtype
            if col == _COL_WRITE:
                return "Yes" if row.writable else "No"
            if col == _COL_UNITS:
                return row.units
            if col == _COL_DESC:
                return row.description

        if role == Qt.ItemDataRole.ForegroundRole:
            if col == _COL_KIND:
                if row.source == "plugin":
                    if row.kind == _KIND_COMMAND:
                        return QColor("#9333ea")   # muted purple for plugin commands
                    return QColor("#b45309")       # amber for plugin datarefs
                if row.kind == _KIND_COMMAND:
                    return QColor("#7c3aed")   # purple for commands
                if row.is_array:
                    return QColor("#0369a1")   # blue for array datarefs
                return QColor("#166534")       # green for scalar datarefs
            if col == _COL_WRITE:
                return QColor("#16a34a") if row.writable else QColor("#94a3b8")

        if role == Qt.ItemDataRole.FontRole and col == _COL_NAME:
            f = QFont("Menlo, Courier New, monospace")
            f.setPointSize(11)
            return f

        if role == Qt.ItemDataRole.UserRole:
            # Return the name so the proxy can filter on it easily
            return row.name

        return None

    def row_at(self, proxy_row: int, proxy: QSortFilterProxyModel) -> _Row | None:
        src_index = proxy.mapToSource(proxy.index(proxy_row, 0))
        if src_index.isValid():
            return self._rows[src_index.row()]
        return None


# ── Custom proxy: multi-column filter + kind/array/writable toggles ───────────

class _FilterProxy(QSortFilterProxyModel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._show_datarefs = True
        self._show_commands = True
        self._arrays_only = False
        self._writable_only = False
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def set_show_datarefs(self, v: bool) -> None:
        self._show_datarefs = v
        self.invalidateFilter()

    def set_show_commands(self, v: bool) -> None:
        self._show_commands = v
        self.invalidateFilter()

    def set_arrays_only(self, v: bool) -> None:
        self._arrays_only = v
        self.invalidateFilter()

    def set_writable_only(self, v: bool) -> None:
        self._writable_only = v
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model: _RefsModel = self.sourceModel()  # type: ignore[assignment]
        row = model._rows[source_row]

        if row.kind == _KIND_DATAREF and not self._show_datarefs:
            return False
        if row.kind == _KIND_COMMAND and not self._show_commands:
            return False
        if self._arrays_only and not row.is_array:
            return False
        if self._writable_only and not row.writable:
            return False

        pattern = self.filterRegularExpression().pattern()
        if pattern:
            haystack = f"{row.name} {row.description} {row.units}".lower()
            return pattern.lower() in haystack
        return True


# ── Navigator tab ─────────────────────────────────────────────────────────────

class DatarefTab(QWidget):
    log_line = Signal(str)
    _load_done = Signal(list)  # emits list[_Row]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._all_rows: list[_Row] = []
        self._model: _RefsModel | None = None
        self._proxy: _FilterProxy | None = None

        self._load_done.connect(self._on_load_done)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # ── Toolbar ───────────────────────────────────────────────────────────
        toolbar = QFrame()
        toolbar.setObjectName("actionBar")
        tl = QHBoxLayout(toolbar)
        tl.setContentsMargins(10, 8, 10, 8)
        tl.setSpacing(8)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search names, descriptions, units…")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._on_search_changed)
        tl.addWidget(self.search_box, 1)

        self.chk_datarefs = QCheckBox("Datarefs")
        self.chk_datarefs.setChecked(True)
        self.chk_datarefs.toggled.connect(self._apply_filters)
        tl.addWidget(self.chk_datarefs)

        self.chk_commands = QCheckBox("Commands")
        self.chk_commands.setChecked(True)
        self.chk_commands.toggled.connect(self._apply_filters)
        tl.addWidget(self.chk_commands)

        self.chk_arrays = QCheckBox("Arrays only")
        self.chk_arrays.setChecked(False)
        self.chk_arrays.toggled.connect(self._apply_filters)
        tl.addWidget(self.chk_arrays)

        self.chk_writable = QCheckBox("Writable only")
        self.chk_writable.setChecked(False)
        self.chk_writable.toggled.connect(self._apply_filters)
        tl.addWidget(self.chk_writable)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: #cbd5e1;")
        tl.addWidget(sep)

        self.btn_load = QPushButton("Load…")
        self.btn_load.setFixedHeight(28)
        self.btn_load.setToolTip("Choose a DataRefs.txt or Commands.txt file")
        self.btn_load.clicked.connect(self._browse_and_load)
        tl.addWidget(self.btn_load)

        root.addWidget(toolbar)

        # ── Status label ──────────────────────────────────────────────────────
        self.status_lbl = QLabel("Loading X-Plane DataRefs & Commands…")
        self.status_lbl.setStyleSheet("font-size: 11px; color: #64748b;")
        root.addWidget(self.status_lbl)

        # ── Table ─────────────────────────────────────────────────────────────
        self.table = QTableView()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.clicked.connect(self._on_row_clicked)
        root.addWidget(self.table, 1)

        # ── Copy hint ─────────────────────────────────────────────────────────
        hint = QLabel("Click a row to copy its name to the clipboard.")
        hint.setStyleSheet("font-size: 10px; color: #94a3b8;")
        root.addWidget(hint)

        # Auto-load from default paths on a background thread
        threading.Thread(target=self._auto_load, daemon=True).start()

    # ── Loading ───────────────────────────────────────────────────────────────

    def _auto_load(self) -> None:
        rows: list[_Row] = []

        # ── X-Plane built-in datarefs (with full metadata) ────────────────────
        known_datarefs: set[str] = set()
        dr_path = default_datarefs_path()
        if dr_path:
            for rec in parse_datarefs(dr_path):
                rows.append(_Row(
                    kind=_KIND_DATAREF,
                    name=rec.name,
                    dtype=rec.dtype,
                    writable=rec.writable,
                    units=rec.units,
                    description=rec.description,
                    is_array=rec.is_array,
                    source="xplane",
                ))
                known_datarefs.add(rec.name)

        # ── Plugin/aircraft datarefs from DataRefTool last-run file ───────────
        drt_dr_path = default_drt_datarefs_path()
        if drt_dr_path:
            for name in parse_drt_datarefs(drt_dr_path):
                if name not in known_datarefs:
                    rows.append(_Row(
                        kind=_KIND_DATAREF,
                        name=name,
                        source="plugin",
                    ))

        # ── X-Plane built-in commands ─────────────────────────────────────────
        known_commands: set[str] = set()
        cmd_path = default_commands_path()
        if cmd_path:
            for rec in parse_commands(cmd_path):
                rows.append(_Row(
                    kind=_KIND_COMMAND,
                    name=rec.name,
                    description=rec.description,
                    source="xplane",
                ))
                known_commands.add(rec.name)

        # ── Plugin/aircraft commands from DataRefTool last-run file ───────────
        drt_cmd_path = default_drt_commands_path()
        if drt_cmd_path:
            for name in parse_drt_commands(drt_cmd_path):
                if name not in known_commands:
                    rows.append(_Row(
                        kind=_KIND_COMMAND,
                        name=name,
                        source="plugin",
                    ))

        self._load_done.emit(rows)

    def _browse_and_load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open DataRefs.txt or Commands.txt",
            str(Path.home()),
            "Text files (*.txt);;All files (*)",
        )
        if not path:
            return
        p = Path(path)
        self.status_lbl.setText(f"Loading {p.name}…")

        def _worker(fp=p):
            rows: list[_Row] = []
            name_lower = fp.name.lower()
            is_drt = "drt_last_run" in name_lower or "last_run" in name_lower

            if "dataref" in name_lower:
                if is_drt:
                    # Name-only format from DataRefTool
                    known = {r.name for r in self._all_rows if r.kind == _KIND_DATAREF and r.source == "xplane"}
                    new_rows = [
                        _Row(kind=_KIND_DATAREF, name=n, source="plugin")
                        for n in parse_drt_datarefs(fp) if n not in known
                    ]
                    existing_xplane = [r for r in self._all_rows if r.kind == _KIND_DATAREF and r.source == "xplane"]
                    existing_cmds = [r for r in self._all_rows if r.kind == _KIND_COMMAND]
                    rows = existing_xplane + new_rows + existing_cmds
                else:
                    for rec in parse_datarefs(fp):
                        rows.append(_Row(
                            kind=_KIND_DATAREF,
                            name=rec.name,
                            dtype=rec.dtype,
                            writable=rec.writable,
                            units=rec.units,
                            description=rec.description,
                            is_array=rec.is_array,
                            source="xplane",
                        ))
                    # Merge with existing commands
                    existing_commands = [r for r in self._all_rows if r.kind == _KIND_COMMAND]
                    rows = rows + existing_commands
            elif "command" in name_lower:
                if is_drt:
                    known = {r.name for r in self._all_rows if r.kind == _KIND_COMMAND and r.source == "xplane"}
                    new_rows = [
                        _Row(kind=_KIND_COMMAND, name=n, source="plugin")
                        for n in parse_drt_commands(fp) if n not in known
                    ]
                    existing_drs = [r for r in self._all_rows if r.kind == _KIND_DATAREF]
                    existing_xplane_cmds = [r for r in self._all_rows if r.kind == _KIND_COMMAND and r.source == "xplane"]
                    rows = existing_drs + existing_xplane_cmds + new_rows
                else:
                    for rec in parse_commands(fp):
                        rows.append(_Row(
                            kind=_KIND_COMMAND,
                            name=rec.name,
                            description=rec.description,
                            source="xplane",
                        ))
                    # Merge with existing datarefs
                    existing_datarefs = [r for r in self._all_rows if r.kind == _KIND_DATAREF]
                    rows = existing_datarefs + rows
            else:
                # Unknown — try both parsers
                rows = [
                    _Row(kind=_KIND_DATAREF, name=rec.name, dtype=rec.dtype,
                         writable=rec.writable, units=rec.units,
                         description=rec.description, is_array=rec.is_array,
                         source="xplane")
                    for rec in parse_datarefs(fp)
                ] or [
                    _Row(kind=_KIND_COMMAND, name=rec.name,
                         description=rec.description, source="xplane")
                    for rec in parse_commands(fp)
                ]
            self._load_done.emit(rows)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_load_done(self, rows: list[_Row]) -> None:
        self._all_rows = rows
        model = _RefsModel(rows, self)
        proxy = _FilterProxy(self)
        proxy.setSourceModel(model)
        self._model = model
        self._proxy = proxy
        self.table.setModel(proxy)

        hdr = self.table.horizontalHeader()
        hdr.resizeSection(_COL_KIND, 80)
        hdr.resizeSection(_COL_NAME, 360)
        hdr.resizeSection(_COL_TYPE, 90)
        hdr.resizeSection(_COL_WRITE, 70)
        hdr.resizeSection(_COL_UNITS, 80)

        n_dr = sum(1 for r in rows if r.kind == _KIND_DATAREF and r.source == "xplane")
        n_dr_plugin = sum(1 for r in rows if r.kind == _KIND_DATAREF and r.source == "plugin")
        n_cmd = sum(1 for r in rows if r.kind == _KIND_COMMAND and r.source == "xplane")
        n_cmd_plugin = sum(1 for r in rows if r.kind == _KIND_COMMAND and r.source == "plugin")
        parts = []
        if n_dr:
            dr_str = f"{n_dr:,} datarefs"
            if n_dr_plugin:
                dr_str += f" + {n_dr_plugin:,} plugin"
            parts.append(dr_str)
        elif n_dr_plugin:
            parts.append(f"{n_dr_plugin:,} plugin datarefs")
        if n_cmd:
            cmd_str = f"{n_cmd:,} commands"
            if n_cmd_plugin:
                cmd_str += f" + {n_cmd_plugin:,} plugin"
            parts.append(cmd_str)
        elif n_cmd_plugin:
            parts.append(f"{n_cmd_plugin:,} plugin commands")
        self.status_lbl.setText(("Loaded " + " · ".join(parts)) if parts else "No data loaded.")

    # ── Filtering ─────────────────────────────────────────────────────────────

    def _on_search_changed(self, text: str) -> None:
        if self._proxy is None:
            return
        self._proxy.setFilterFixedString(text)
        self._update_count_label()

    def _apply_filters(self) -> None:
        if self._proxy is None:
            return
        self._proxy.set_show_datarefs(self.chk_datarefs.isChecked())
        self._proxy.set_show_commands(self.chk_commands.isChecked())
        self._proxy.set_arrays_only(self.chk_arrays.isChecked())
        self._proxy.set_writable_only(self.chk_writable.isChecked())
        self._update_count_label()

    def _update_count_label(self) -> None:
        if self._proxy is None:
            return
        visible = self._proxy.rowCount()
        total = len(self._all_rows)
        if visible == total:
            n_dr = sum(1 for r in self._all_rows if r.kind == _KIND_DATAREF and r.source == "xplane")
            n_dr_plugin = sum(1 for r in self._all_rows if r.kind == _KIND_DATAREF and r.source == "plugin")
            n_cmd = sum(1 for r in self._all_rows if r.kind == _KIND_COMMAND and r.source == "xplane")
            n_cmd_plugin = sum(1 for r in self._all_rows if r.kind == _KIND_COMMAND and r.source == "plugin")
            parts = []
            if n_dr:
                dr_str = f"{n_dr:,} datarefs"
                if n_dr_plugin:
                    dr_str += f" + {n_dr_plugin:,} plugin"
                parts.append(dr_str)
            elif n_dr_plugin:
                parts.append(f"{n_dr_plugin:,} plugin datarefs")
            if n_cmd:
                cmd_str = f"{n_cmd:,} commands"
                if n_cmd_plugin:
                    cmd_str += f" + {n_cmd_plugin:,} plugin"
                parts.append(cmd_str)
            elif n_cmd_plugin:
                parts.append(f"{n_cmd_plugin:,} plugin commands")
            self.status_lbl.setText(("Loaded " + " · ".join(parts)) if parts else "No data loaded.")
        else:
            self.status_lbl.setText(f"Showing {visible:,} of {total:,}")

    # ── Interaction ───────────────────────────────────────────────────────────

    def _on_row_clicked(self, index: QModelIndex) -> None:
        if self._model is None or self._proxy is None:
            return
        row = self._model.row_at(index.row(), self._proxy)
        if row is None:
            return
        QApplication.clipboard().setText(row.name)
        self.log_line.emit(f"Copied: {row.name}")
