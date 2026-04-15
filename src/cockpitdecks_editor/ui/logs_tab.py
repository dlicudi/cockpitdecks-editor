"""Logs tab — captures all status/error messages emitted by other tabs."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class LogsTab(QWidget):
    """Read-only log viewer.  Feed messages via :meth:`append_line`."""

    log_line = Signal(str)  # kept for interface consistency; not used internally

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(8)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        lbl = QLabel("Application logs")
        lbl.setStyleSheet("font-weight: 600; font-size: 13px; color: #1c1c1e;")
        toolbar.addWidget(lbl)
        toolbar.addStretch(1)

        self._only_errors_btn = QPushButton("Errors only")
        self._only_errors_btn.setCheckable(True)
        self._only_errors_btn.setChecked(False)
        self._only_errors_btn.clicked.connect(self._apply_filter)
        toolbar.addWidget(self._only_errors_btn)

        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear)
        toolbar.addWidget(clear_btn)

        root.addLayout(toolbar)

        # Log display
        self._view = QPlainTextEdit()
        self._view.setReadOnly(True)
        self._view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._view.setStyleSheet(
            "font-family: 'Menlo', 'Consolas', monospace; font-size: 12px;"
            " background-color: #0f172a; color: #e2e8f0;"
            " border: 1px solid #334155; border-radius: 6px;"
            " padding: 6px;"
        )
        root.addWidget(self._view, 1)

        # In-memory log (list of (timestamp_str, text, is_error))
        self._entries: list[tuple[str, str, bool]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append_line(self, text: str) -> None:
        """Append *text* to the log, colouring error lines red."""
        ts = datetime.now().strftime("%H:%M:%S")
        is_error = "[error]" in text.lower() or "traceback" in text.lower() or "exception" in text.lower()
        self._entries.append((ts, text, is_error))
        if not self._only_errors_btn.isChecked() or is_error:
            self._insert_entry(ts, text, is_error)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _insert_entry(self, ts: str, text: str, is_error: bool) -> None:
        cursor = self._view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        # Timestamp
        ts_fmt = QTextCharFormat()
        ts_fmt.setForeground(QColor("#94a3b8"))
        cursor.insertText(f"[{ts}] ", ts_fmt)

        # Message
        msg_fmt = QTextCharFormat()
        if is_error:
            msg_fmt.setForeground(QColor("#f87171"))
        else:
            msg_fmt.setForeground(QColor("#e2e8f0"))
        cursor.insertText(text, msg_fmt)
        cursor.insertText("\n", QTextCharFormat())

        # Auto-scroll only when already at the bottom
        scrollbar = self._view.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 4
        if at_bottom:
            self._view.setTextCursor(cursor)
            self._view.ensureCursorVisible()

    def _clear(self) -> None:
        self._entries.clear()
        self._view.clear()

    def _apply_filter(self) -> None:
        """Rebuild the visible log when the errors-only toggle changes."""
        self._view.clear()
        only_errors = self._only_errors_btn.isChecked()
        for ts, text, is_error in self._entries:
            if not only_errors or is_error:
                self._insert_entry(ts, text, is_error)
        # Scroll to end after rebuild
        self._view.moveCursor(QTextCursor.MoveOperation.End)
