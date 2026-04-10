from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from cockpitdecks_editor.services.desktop_settings import load as load_settings
from cockpitdecks_editor.services.targets import shorten_filesystem_path
from cockpitdecks_editor.ui.app_style import MAIN_WINDOW_QSS
from cockpitdecks_editor.ui.editor_tab import EditorTab


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Cockpitdecks Editor")
        self.resize(1600, 980)
        self.setStyleSheet(MAIN_WINDOW_QSS)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        header = QFrame()
        header.setObjectName("actionBar")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(14, 12, 14, 12)
        hl.setSpacing(10)

        title = QLabel("Cockpitdecks Editor")
        title.setStyleSheet("font-size: 18px; font-weight: 700; color: #0f172a;")
        hl.addWidget(title)

        self.target_summary = QLabel("No root open")
        self.target_summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.target_summary.setStyleSheet("font-size: 12px; color: #475569;")
        hl.addWidget(self.target_summary, 1)

        root.addWidget(header)

        self.tabs = QTabWidget()
        self.editor_tab = EditorTab()
        self.editor_tab.log_line.connect(self._append_status)
        self.editor_tab.root_path_changed.connect(self._sync_root_summary)
        self.tabs.addTab(self.editor_tab, "Editor")

        placeholder = QWidget()
        pl = QVBoxLayout(placeholder)
        pl.setContentsMargins(16, 16, 16, 16)
        hint = QLabel("Future home for config-oriented tools such as validation, search/replace, templates, and page operations.")
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: 13px; color: #475569;")
        pl.addWidget(hint)
        pl.addStretch(1)
        self.tabs.addTab(placeholder, "Tools")

        root.addWidget(self.tabs, 1)

        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready")

        self._open_initial_root()

    def _append_status(self, text: str) -> None:
        self.statusBar().showMessage(text, 8000)

    def _open_initial_root(self) -> None:
        selected = (load_settings().get("COCKPITDECKS_TARGET") or "").strip()
        if selected:
            self.editor_tab.open_root_path(selected)
        else:
            self._sync_root_summary("")

    def _sync_root_summary(self, path: str) -> None:
        if path:
            self.target_summary.setText(shorten_filesystem_path(path, max_len=110))
            return
        self.target_summary.setText("No root open")
