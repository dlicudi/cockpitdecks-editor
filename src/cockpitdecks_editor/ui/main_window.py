from __future__ import annotations

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
from cockpitdecks_editor.ui.dataref_tab import DatarefTab
from cockpitdecks_editor.ui.designer_tab import DesignerTab
from cockpitdecks_editor.ui.editor_tab import EditorTab
from cockpitdecks_editor.ui.logs_tab import LogsTab


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

        self.logs_tab = LogsTab()

        self.editor_tab = EditorTab()
        self.editor_tab.log_line.connect(self._append_status)
        self.editor_tab.log_line.connect(self.logs_tab.append_line)
        self.editor_tab.root_path_changed.connect(self._sync_root_summary)
        self.editor_tab.root_path_changed.connect(self._on_root_changed)
        self.editor_tab.open_in_designer.connect(self._open_button_in_designer)
        self.tabs.addTab(self.editor_tab, "Editor")

        self.designer_tab = DesignerTab()
        self.designer_tab.log_line.connect(self._append_status)
        self.designer_tab.log_line.connect(self.logs_tab.append_line)
        self.designer_tab.save_to_page.connect(self._save_button_to_page)
        self.tabs.addTab(self.designer_tab, "Button Designer")

        self.dataref_tab = DatarefTab()
        self.dataref_tab.log_line.connect(self._append_status)
        self.dataref_tab.log_line.connect(self.logs_tab.append_line)
        self.tabs.addTab(self.dataref_tab, "Dataref Navigator")

        self.tabs.addTab(self.logs_tab, "Logs")

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

    def _on_root_changed(self, path: str) -> None:
        self.designer_tab.set_target(path if path else None)

    def _open_button_in_designer(self, button_yaml: str, deck_name: str, root_path: str, button_id: str, file_path: str) -> None:
        self.designer_tab.load_button(
            button_yaml,
            deck_name=deck_name,
            root_path=root_path or None,
            button_id=button_id,
            file_path=file_path,
        )
        self.tabs.setCurrentWidget(self.designer_tab)

    def _save_button_to_page(self, button_yaml: str, button_id: str, file_path: str) -> None:
        self.editor_tab.save_button_from_designer(button_yaml, button_id)
        self.tabs.setCurrentWidget(self.editor_tab)

    def _sync_root_summary(self, path: str) -> None:
        if path:
            self.target_summary.setText(shorten_filesystem_path(path, max_len=110))
            return
        self.target_summary.setText("No root open")
