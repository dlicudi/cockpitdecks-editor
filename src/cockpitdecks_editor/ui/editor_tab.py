from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import yaml
from PySide6.QtCore import QPoint, QMimeData, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QDrag, QFont, QKeySequence, QMouseEvent, QPixmap, QTextOption, QPainter, QPainterPath, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

try:
    import shiboken6 as shiboken
except ImportError:
    try:
        import shiboken
    except ImportError:
        shiboken = None


from cockpitdecks_editor.services.live_apis import render_button_preview
from cockpitdecks_editor.services.native_preview import describe_slot_native, list_preview_fonts, render_button_preview_native, warm_preview_pool
from cockpitdecks_editor.services.desktop_settings import load as load_settings, save as save_settings


def _short_path(path: Path | str, *, max_len: int = 96) -> str:
    p = Path(path).expanduser()
    try:
        text = str(p.resolve())
    except OSError:
        text = str(p)
    home = str(Path.home())
    if text.startswith(home):
        text = "~" + text[len(home):]
    if len(text) <= max_len:
        return text
    head = max_len // 2 - 2
    tail = max_len - head - 3
    return text[:head] + "…" + text[-tail:]


def _render_preview_with_fallback(
    target_root: Path | None,
    deck_name: str,
    button_yaml: str,
) -> tuple[bytes | None, dict | None, str | None]:
    if target_root is None:
        return None, None, "no preview target"
    image_bytes, meta, error = render_button_preview(deck_name, button_yaml)
    if image_bytes:
        return image_bytes, meta, error
    return render_button_preview_native(target_root, deck_name, button_yaml)


class _ButtonEditDocument:
    def __init__(self) -> None:
        self.original_text = ""
        self.current_text = ""
        self.current_data: dict = {}

    def load_text(self, text: str) -> tuple[bool, str]:
        try:
            data = yaml.safe_load(text.strip() or "{}") or {}
        except Exception as exc:
            return False, str(exc)
        if not isinstance(data, dict):
            return False, "Button config must parse to a YAML mapping."
        self.original_text = text
        self.current_text = text
        self.current_data = dict(data)
        return True, ""

    def update_from_yaml_text(self, text: str) -> tuple[bool, str]:
        try:
            data = yaml.safe_load(text.strip() or "{}") or {}
        except Exception as exc:
            return False, str(exc)
        if not isinstance(data, dict):
            return False, "Button config must parse to a YAML mapping."
        self.current_text = text
        self.current_data = dict(data)
        return True, ""

    def set_current_data(self, data: dict) -> None:
        self.current_data = dict(data)
        self.current_text = yaml.safe_dump(self.current_data, sort_keys=False, allow_unicode=False)

    def to_yaml(self) -> str:
        self.current_text = yaml.safe_dump(self.current_data, sort_keys=False, allow_unicode=False)
        return self.current_text


class _NoWheelComboBox(QComboBox):
    def wheelEvent(self, event) -> None:
        event.ignore()


class _NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event) -> None:
        event.ignore()


_ACTIVATION_SCHEMA: dict[str, list[tuple[str, str]]] = {
    "Push Button": [
        ("push", "Momentary Command"),
        ("begin-end-command", "Begin / End Command"),
        ("encoder-toggle", "Toggle On/Off"),
        ("short-or-long-press", "Short / Long Press"),
    ],
    "Page": [
        ("page", "Load Page"),
        ("page-cycle", "Cycle Pages"),
    ],
    "System": [
        ("reload", "Reload"),
        ("theme", "Theme"),
        ("inspect", "Inspect"),
        ("stop", "Stop"),
    ],
    "Passive": [
        ("none", "No Activation"),
    ],
}

_REPRESENTATION_SCHEMA: dict[str, list[tuple[str, str]]] = {
    "Basic": [
        ("standard", "Standard Text / Icon"),
        ("text", "Text Tile"),
    ],
    "Annunciator": [
        ("annunciator", "Annunciator"),
    ],
    "Data": [
        ("data", "Data Tile"),
    ],
    "Switch": [
        ("switch", "Switch"),
        ("push-switch", "Push Switch"),
    ],
    "Gauge": [
        ("gauge", "Gauge"),
    ],
}

_ANNUNCIATOR_PART_IDS: dict[str, list[str]] = {
    "A": ["A0"],
    "B": ["B0", "B1"],
    "C": ["C0", "C1"],
    "D": ["D0", "D1", "D2"],
    "E": ["E0", "E1", "E2"],
    "F": ["F0", "F1", "F2", "F3"],
}

_BUTTON_CLIPBOARD_MIME = "application/x-cockpitdecks-button"


def _command_block(command: str) -> dict:
    return {"command": command}


def _field_with_button(edit: QLineEdit, button: QPushButton) -> QWidget:
    host = QWidget()
    layout = QHBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    layout.addWidget(edit, 1)
    layout.addWidget(button, 0)
    return host


def _insert_dataref_formula(edit: QLineEdit, dataref: str) -> None:
    token = f"${{{dataref}}}"
    existing = edit.text()
    if not existing:
        edit.setText(token)
        return
    pos = edit.cursorPosition()
    edit.setText(existing[:pos] + token + existing[pos:])
    edit.setCursorPosition(pos + len(token))


def _set_form_row_visible(layout: QFormLayout, field: QWidget, visible: bool) -> None:
    if hasattr(layout, "setRowVisible"):
        layout.setRowVisible(field, visible)
        return
    label = layout.labelForField(field)
    if label is not None:
        label.setVisible(visible)
    field.setVisible(visible)


def _two_command_fields(action_type: str) -> tuple[str, str] | None:
    """Return the two sub-keys under 'commands' dict for dual-command activations."""
    if action_type == "encoder-toggle":
        return ("toggle-on", "toggle-off")
    if action_type == "short-or-long-press":
        return ("press", "long-press")
    return None


def _button_preview_validation_error(data: dict) -> str | None:
    action_type = str(data.get("activation") or "push").strip()
    commands = data.get("commands") or {}
    if action_type == "begin-end-command" and not str(commands.get("press") or "").strip():
        return "Begin / End Command needs a command."
    if action_type == "encoder-toggle":
        fields = _two_command_fields(action_type)
        has_dataref = bool(str(data.get("set-dataref") or "").strip() or str(data.get("dataref") or "").strip())
        has_pair = fields is not None and all(str(commands.get(field) or "").strip() for field in fields)
        if not has_pair and not has_dataref:
            return f"{action_type} needs two named commands or a dataref."
    if action_type == "short-or-long-press":
        fields = _two_command_fields(action_type)
        if fields is None or not all(str(commands.get(field) or "").strip() for field in fields):
            return "Short / Long Press needs short and long commands."
    if action_type == "page" and not str(data.get("page") or "").strip():
        return "Load Page needs a page name."
    if action_type == "page-cycle":
        pages = data.get("pages")
        if not isinstance(pages, list) or len(pages) < 2:
            return "Cycle Pages needs at least two pages."
    return None


class _VisualButtonCard(QFrame):
    selected = Signal(str)
    edit_requested = Signal(str)
    context_requested = Signal(str, QPoint)

    def __init__(
        self,
        button_id: str,
        button_data: dict,
        *,
        dark: bool,
        scale: float = 1.0,
        preview: QPixmap | None = None,
        preview_status: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.button_id = button_id
        self.button_data = button_data
        self._drag_start: QPoint | None = None
        self._dragging = False
        self._dark = dark
        self._scale = max(0.6, min(2.0, scale))
        self._size = int(118 * self._scale)
        self._render_width = self._size
        self._render_height = self._size

        # Native sizing: 1x1 cards in slots are fixed, spanned cards are flexible
        span = button_data.get("span")
        is_span = isinstance(span, (list, tuple, str)) and any(x in str(span) for x in "23456789")
        if not is_span:
            self.setFixedSize(self._size, self._size)
        else:
            self.setMinimumSize(self._size, self._size)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self._selection_effect = None
        
        # Persistence for dynamic resizing
        self._last_pixmap = preview
        self._last_status = preview_status
        self._rendering = False
        
        self._apply_theme()

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._render_content(preview=preview, preview_status=preview_status)

    def _clear_layout(self) -> bool:
        try:
            if not hasattr(self, "_layout") or self._layout is None:
                return False
            if shiboken is not None and not shiboken.isValid(self._layout):
                return False
            while self._layout.count():
                if shiboken is not None and not shiboken.isValid(self._layout):
                    return False
                item = self._layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            return True
        except (RuntimeError, AttributeError): # C++ object deleted or None
            return False

    def _render_content(self, *, preview: QPixmap | None = None, preview_status: str | None = None) -> None:
        if self._rendering:
            return
        if shiboken is not None and not shiboken.isValid(self):
            return
        self._rendering = True
        try:
            # Persistent state
            if preview is not None: self._last_pixmap = preview
            if preview_status is not None: self._last_status = preview_status
            
            preview = self._last_pixmap
            preview_status = self._last_status

            if not self._clear_layout():
                return
            if not hasattr(self, "_layout") or self._layout is None or (shiboken is not None and not shiboken.isValid(self._layout)):
                return

            if preview is not None and not preview.isNull():
                preview_label = QLabel()
                preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                preview_label.setStyleSheet("border: none;")
                target_w = max(1, self.width())
                target_h = max(1, self.height())
                preview_label.setFixedSize(target_w, target_h)
                pixmap = QPixmap(preview)
                src_w = max(1, pixmap.width())
                src_h = max(1, pixmap.height())

                # Scale up if the rendered preview is smaller than the card's logical size.
                if src_w < target_w or src_h < target_h:
                    scale = max(target_w / src_w, target_h / src_h)
                    pixmap = pixmap.scaled(
                        int(src_w * scale), int(src_h * scale),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    src_w = max(1, pixmap.width())
                    src_h = max(1, pixmap.height())

                # DPR trick for downscaling on HiDPI: keep hi-res pixels, tell Qt to
                # render them at the logical target size.
                dpr = max(1.0, min(src_w / target_w, src_h / target_h))

                rounded = QPixmap(pixmap.size())
                rounded.fill(Qt.GlobalColor.transparent)
                painter = QPainter(rounded)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                path = QPainterPath()
                # Match the grid slot corner treatment with a small logical radius,
                # instead of scaling corner roundness with the full spanned image size.
                logical_radius = 8.0
                radius_scale = min(src_w / max(1, target_w), src_h / max(1, target_h))
                radius = max(1.0, logical_radius * radius_scale)
                path.addRoundedRect(0, 0, src_w, src_h, radius, radius)
                painter.setClipPath(path)
                painter.drawPixmap(0, 0, pixmap)
                painter.end()
                pixmap = rounded

                pixmap.setDevicePixelRatio(dpr)
                preview_label.setPixmap(pixmap)
                if shiboken is not None and shiboken.isValid(self._layout):
                    self._layout.addWidget(preview_label, 1)
                return

            title = str(self.button_data.get("label") or self.button_data.get("text") or self.button_data.get("name") or self.button_data.get("activation") or "Button")
            _cmds = self.button_data.get("commands") or {}
            subtitle = str(self.button_data.get("text") or _cmds.get("press") or self.button_data.get("page") or self.button_data.get("activation") or "").strip()
            if len(subtitle) > 26:
                subtitle = subtitle[:23] + "…"

            title_label = QLabel(title)
            title_label.setWordWrap(True)
            title_label.setStyleSheet(f"font-size: {max(9, int(12 * self._scale))}px; font-weight: 700; color: {self._fg_primary};")
            
            if shiboken is not None and shiboken.isValid(self._layout):
                self._layout.addWidget(title_label)

                if subtitle and subtitle != title:
                    subtitle_label = QLabel(subtitle)
                    subtitle_label.setWordWrap(True)
                    subtitle_label.setStyleSheet(f"font-size: {max(8, int(10 * self._scale))}px; color: {self._fg_secondary};")
                    self._layout.addWidget(subtitle_label)

                if preview_status:
                    status_label = QLabel(preview_status)
                    status_label.setWordWrap(True)
                    status_label.setStyleSheet(f"font-size: {max(7, int(9 * self._scale))}px; color: {self._fg_secondary};")
                    self._layout.addWidget(status_label)
                else:
                    self._layout.addStretch(1)
                self._layout.addStretch(1)
        except (RuntimeError, AttributeError):
            pass
        finally:
            self._rendering = False

    def update_preview(self, preview: QPixmap | None, preview_status: str | None = None) -> None:
        self._render_content(preview=preview, preview_status=preview_status)

    def resize_to_span(self, w: int, h: int, preview: QPixmap | None = None, preview_status: str | None = None) -> None:
        """DEPRECATED: Now handled by native resizeEvent and QGridLayout spanning."""
        pass

    def resizeEvent(self, event) -> None:
        if event.size() == event.oldSize():
            super().resizeEvent(event)
            return
        super().resizeEvent(event)
        # Re-render content at the new allocated size (for spanned buttons)
        # We use QTimer to avoid recursive render calls in a single layout pass
        QTimer.singleShot(0, self._render_content)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._apply_theme()

    def _apply_theme(self) -> None:
        if shiboken is not None and not shiboken.isValid(self):
            return
        selected = getattr(self, "_selected", False)
        pad = 2 if selected else 0
        if hasattr(self, "_layout") and self._layout is not None:
            try:
                if shiboken is not None and shiboken.isValid(self._layout):
                    self._layout.setContentsMargins(pad, pad, pad, pad)
            except RuntimeError:
                pass
        if self._dark:
            self._fg_primary = "#e2e8f0"
            self._fg_secondary = "#94a3b8"
            sel_border = "#fb923c"
            sel_bg = "rgba(251, 146, 60, 0.18)"
        else:
            self._fg_primary = "#0f172a"
            self._fg_secondary = "#64748b"
            sel_border = "#f97316"
            sel_bg = "rgba(249, 115, 22, 0.10)"
        if selected:
            try:
                if self._selection_effect is None:
                    self._selection_effect = QGraphicsDropShadowEffect(self)
                    self._selection_effect.setBlurRadius(20)
                    self._selection_effect.setColor(QColor(sel_border))
                    self._selection_effect.setOffset(0)
                self.setGraphicsEffect(self._selection_effect)
            except (RuntimeError, AttributeError):
                self._selection_effect = QGraphicsDropShadowEffect(self)
                self._selection_effect.setBlurRadius(20)
                self._selection_effect.setColor(QColor(sel_border))
                self._selection_effect.setOffset(0)
                self.setGraphicsEffect(self._selection_effect)
            self.setStyleSheet(f"QFrame {{ background: {sel_bg}; border: 1px solid {sel_border}; border-radius: 8px; }}")
        else:
            try:
                self.setGraphicsEffect(None)
            except RuntimeError:
                pass
            self.setStyleSheet("QFrame { background: transparent; border: none; }")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.pos()
            self._dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_start is None:
            return
        if (event.pos() - self._drag_start).manhattanLength() < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return
        self._dragging = True
        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(self.button_id)
        try:
            payload = json.dumps(self.button_data, ensure_ascii=True).encode("utf-8")
            mime.setData(_BUTTON_CLIPBOARD_MIME, payload)
        except Exception:
            pass
        drag.setMimeData(mime)
        pixmap = self.grab()
        drag.setPixmap(pixmap)
        drag.setHotSpot(event.pos())
        drag.exec(Qt.DropAction.MoveAction)
        self._drag_start = None
        return

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            # Right-click should only trigger context menu, not selection
            self.context_requested.emit(self.button_id, event.globalPosition().toPoint())
            self._drag_start = None
            self._dragging = False
            try:
                super().mouseReleaseEvent(event)
            except RuntimeError:
                return
            return
        should_select = event.button() == Qt.MouseButton.LeftButton and not self._dragging and self._drag_start is not None
        self._drag_start = None
        self._dragging = False
        if should_select:
            self.selected.emit(self.button_id)
        try:
            super().mouseReleaseEvent(event)
        except RuntimeError:
            return

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        # Prevent accidental edit on double click
        event.accept()


class _SuggestionPickerDialog(QDialog):
    def __init__(
        self,
        title: str,
        suggestions: list[tuple[str, str]],
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 520)
        self._all = list(suggestions)
        self._value = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filter suggestions...")
        root.addWidget(self.search_edit)

        self.count_label = QLabel("")
        root.addWidget(self.count_label)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._accept_item)
        root.addWidget(self.list_widget, 1)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        self.source_hint = QLabel("Suggestions only. Runtime/plugin datarefs may exist beyond this list.")
        self.source_hint.setWordWrap(True)
        actions.addWidget(self.source_hint, 1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        use = QPushButton("Use Selected")
        use.clicked.connect(self._accept_current)
        actions.addWidget(use)
        root.addLayout(actions)

        self.search_edit.textChanged.connect(self._refilter)
        self._refilter("")

    def selected_value(self) -> str:
        return self._value

    def _refilter(self, query: str) -> None:
        self.list_widget.clear()
        q = query.strip().lower()
        shown = 0
        for value, detail in self._all:
            hay = f"{value}\n{detail}".lower()
            if q and q not in hay:
                continue
            item = QListWidgetItem(value)
            if detail:
                item.setToolTip(detail)
                item.setText(f"{value}\n{detail}")
            item.setData(Qt.ItemDataRole.UserRole, value)
            self.list_widget.addItem(item)
            shown += 1
        self.count_label.setText(f"{shown} suggestion{'s' if shown != 1 else ''}")
        if shown > 0:
            self.list_widget.setCurrentRow(0)

    def _accept_item(self, item: QListWidgetItem) -> None:
        self._value = str(item.data(Qt.ItemDataRole.UserRole) or "")
        self.accept()

    def _accept_current(self) -> None:
        item = self.list_widget.currentItem()
        if item is None:
            return
        self._accept_item(item)


class _MultiSuggestionPickerDialog(QDialog):
    def __init__(
        self,
        title: str,
        suggestions: list[tuple[str, str]],
        *,
        selected: list[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 560)
        self._all = list(suggestions)
        self._selected = {item.strip() for item in (selected or []) if item.strip()}

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filter suggestions...")
        root.addWidget(self.search_edit)

        self.count_label = QLabel("")
        root.addWidget(self.count_label)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        root.addWidget(self.list_widget, 1)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        hint = QLabel("Suggestions only. Choose one or more pages from the current layout.")
        hint.setWordWrap(True)
        actions.addWidget(hint, 1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        use = QPushButton("Use Selected")
        use.clicked.connect(self.accept)
        actions.addWidget(use)
        root.addLayout(actions)

        self.search_edit.textChanged.connect(self._refilter)
        self._refilter("")

    def selected_values(self) -> list[str]:
        values: list[str] = []
        for item in self.list_widget.selectedItems():
            value = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
            if value:
                values.append(value)
        return values

    def _refilter(self, query: str) -> None:
        self.list_widget.clear()
        q = query.strip().lower()
        shown = 0
        for value, detail in self._all:
            hay = f"{value}\n{detail}".lower()
            if q and q not in hay:
                continue
            item = QListWidgetItem(value)
            if detail:
                item.setToolTip(detail)
                item.setText(f"{value}\n{detail}")
            item.setData(Qt.ItemDataRole.UserRole, value)
            self.list_widget.addItem(item)
            if value in self._selected:
                item.setSelected(True)
            shown += 1
        self.count_label.setText(f"{shown} page{'s' if shown != 1 else ''}")
        if shown > 0:
            self.list_widget.setCurrentRow(0)


class _VisualGridHost(QWidget):
    resized = Signal()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.resized.emit()


class _GridSlot(QFrame):
    dropped = Signal(str, int)
    create_requested = Signal(int)
    context_requested = Signal(int, QPoint)
    deselect_requested = Signal()

    def __init__(self, index: int, *, dark: bool, scale: float = 1.0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.index = index
        self._dark = dark
        self._scale = max(0.6, min(2.0, scale))
        self._occupied = False
        self._selected = False
        self._force_hidden = False
        self.setAcceptDrops(True)
        self._selection_effect = None
        self.setFixedSize(int(128 * self._scale), int(128 * self._scale))
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._placeholder = QLabel(str(index))
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.addWidget(self._placeholder, 1)
        self._apply_theme()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._apply_theme()

    def set_force_hidden(self, hidden: bool) -> None:
        if self._force_hidden != hidden:
            self._force_hidden = hidden
            self._apply_theme()

    def _apply_theme(self) -> None:
        if shiboken is not None and not shiboken.isValid(self):
            return
        if self._dark:
            empty_bg = "#0f172a"
            border = "#334155"
            fg = "#64748b"
            sel_bg = "rgba(251, 146, 60, 0.15)"
            sel_border = "#fb923c"
        else:
            empty_bg = "#ffffff"
            border = "#cbd5e1"
            fg = "#94a3b8"
            sel_bg = "rgba(249, 115, 22, 0.08)"
            sel_border = "#f97316"
        if self._force_hidden:
            self.setStyleSheet("QFrame { background: transparent; border: none; }")
            self._placeholder.setStyleSheet("color: transparent; border: none;")
            return
        if self._selected and self._occupied:
            try:
                if self._selection_effect is None:
                    self._selection_effect = QGraphicsDropShadowEffect(self)
                    self._selection_effect.setBlurRadius(20)
                    self._selection_effect.setColor(QColor(sel_border))
                    self._selection_effect.setOffset(0)
                self.setGraphicsEffect(self._selection_effect)
            except (RuntimeError, AttributeError):
                self._selection_effect = QGraphicsDropShadowEffect(self)
                self._selection_effect.setBlurRadius(20)
                self._selection_effect.setColor(QColor(sel_border))
                self._selection_effect.setOffset(0)
                self.setGraphicsEffect(self._selection_effect)
            self.setStyleSheet(
                f"QFrame {{ background: {sel_bg}; border: 1px solid {sel_border}; border-radius: 8px; }}"
            )
        elif self._occupied:
            try:
                self.setGraphicsEffect(None)
            except RuntimeError:
                pass
            self.setStyleSheet("QFrame { background: transparent; border: none; }")
        else:
            try:
                self.setGraphicsEffect(None)
            except RuntimeError:
                pass
            self.setStyleSheet(
                f"QFrame {{ background: {empty_bg}; border: 1px dashed {border}; border-radius: 8px; }}"
            )
        self._placeholder.setStyleSheet(f"font-size: {max(8, int(11 * self._scale))}px; color: {fg}; border: none;")

    def set_card(self, card: QWidget | None) -> None:
        if shiboken is not None and not shiboken.isValid(self):
            return
        try:
            if not hasattr(self, "_layout") or self._layout is None or (shiboken is not None and not shiboken.isValid(self._layout)):
                return
            while self._layout.count():
                item = self._layout.takeAt(0)
                if item.widget():
                    item.widget().setParent(None)
            if card is None:
                self._occupied = False
                self._layout.addWidget(self._placeholder, 1)
                self._placeholder.show()
            else:
                self._occupied = True
                self._layout.addWidget(card, 0, Qt.AlignmentFlag.AlignCenter)
            self._apply_theme()
        except RuntimeError:
            pass

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        if event.mimeData().hasText():
            self.dropped.emit(event.mimeData().text(), self.index)
            event.acceptProposedAction()
        else:
            event.ignore()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.create_requested.emit(self.index)
        try:
            super().mouseDoubleClickEvent(event)
        except RuntimeError:
            return

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton and not self._occupied:
            self.context_requested.emit(self.index, event.globalPosition().toPoint())
        elif event.button() == Qt.MouseButton.LeftButton and not self._occupied:
            self.deselect_requested.emit()
        try:
            super().mouseReleaseEvent(event)
        except RuntimeError:
            return


class _PageDropTree(QTreeWidget):
    page_drop_requested = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(_BUTTON_CLIPBOARD_MIME):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        if not event.mimeData().hasFormat(_BUTTON_CLIPBOARD_MIME):
            event.ignore()
            return
        item = self.itemAt(event.position().toPoint())
        raw_path = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        path = str(raw_path or "")
        if path and path.endswith((".yaml", ".yml")) and not path.endswith("/config.yaml") and Path(path).name != "config.yaml":
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event) -> None:
        if not event.mimeData().hasFormat(_BUTTON_CLIPBOARD_MIME):
            event.ignore()
            return
        item = self.itemAt(event.position().toPoint())
        raw_path = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        path = str(raw_path or "")
        if not path or not path.endswith((".yaml", ".yml")) or path.endswith("/config.yaml") or Path(path).name == "config.yaml":
            event.ignore()
            return
        payload = bytes(event.mimeData().data(_BUTTON_CLIPBOARD_MIME)).decode("utf-8", errors="ignore")
        self.page_drop_requested.emit(path, payload)
        event.acceptProposedAction()


class EditorTab(QWidget):
    log_line = Signal(str)
    reload_requested = Signal()
    root_path_changed = Signal(str)
    preview_ready = Signal(str, object, object)
    button_edit_preview_ready = Signal(object, object)
    preview_warm_ready = Signal(str, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current_target_path: Path | None = None
        self._current_file_path: Path | None = None
        self._loading_file = False
        self._dark_mode = False
        self._preferred_mode = "visual"
        self._visual_zoom = 1.0
        self._selected_button_id: str | None = None
        self._selected_button_ids: set[str] = set()

        self._visual_enabled = False
        self._visual_yaml_data: dict | None = None
        self._config_form_enabled = False
        self._config_yaml_data: dict | None = None
        self._config_form_loading = False
        self._visual_button_order: list[str] = []
        self._visual_buttons: dict[str, dict] = {}
        self._visual_cols = 0
        self._visual_rows = 0
        self._visual_deck_name: str | None = None
        self._preview_cache: dict[str, QPixmap | None] = {}
        self._preview_errors: dict[str, str] = {}
        self._preview_inflight: set[str] = set()
        self._preview_queue: list[tuple[str, str, str, int]] = []
        self._suggestion_cache: dict[tuple[str, str], list[tuple[str, str]]] = {}
        self._preview_queue_keys: set[str] = set()
        self._preview_max_inflight = 8
        self._preview_generation = 0
        self._preview_warm_targets: set[str] = set()
        self._preview_ready_targets: set[str] = set()
        self._visible_cards: dict[str, _VisualButtonCard] = {}
        self._visible_slots: dict[str, _GridSlot] = {}
        self._visible_cell_slots: dict[tuple[int, int], _GridSlot] = {}
        self._visible_named_cards: dict[str, _VisualButtonCard] = {}
        self._span_card_specs: dict[str, tuple[int, int, int, int]] = {}
        self._effective_page_attrs_cache: dict = {}
        self._selected_slot_info: dict = {}
        self._button_edit_id: str | None = None
        self._button_edit_on_apply = None
        self._button_edit_base_text = ""
        self._button_doc = _ButtonEditDocument()
        self._button_visual_syncing = False
        self.preview_ready.connect(self._on_preview_ready)
        self.button_edit_preview_ready.connect(self._on_button_edit_preview_ready)
        self.preview_warm_ready.connect(self._on_preview_warm_ready)
        self._preview_refresh_timer = QTimer(self)
        self._preview_refresh_timer.setSingleShot(True)
        self._preview_refresh_timer.timeout.connect(self._refresh_preview_results)
        self._button_edit_preview_generation = 0
        self._button_edit_preview_timer = QTimer(self)
        self._button_edit_preview_timer.setSingleShot(True)
        self._button_edit_preview_timer.timeout.connect(self._render_button_edit_preview)

        self._page_autosave_timer = QTimer(self)
        self._page_autosave_timer.setSingleShot(True)
        self._page_autosave_timer.timeout.connect(self._autosave_page)

        self._button_autosave_timer = QTimer(self)
        self._button_autosave_timer.setSingleShot(True)
        self._button_autosave_timer.timeout.connect(self._autosave_button)

        self._save_clear_timer = QTimer(self)
        self._save_clear_timer.setSingleShot(True)
        self._save_clear_timer.timeout.connect(lambda: self.modified_label.setText(""))

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        toolbar = QFrame()
        self._toolbar = toolbar
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(12, 10, 12, 10)
        toolbar_layout.setSpacing(8)

        label = QLabel("Root")
        label.setStyleSheet("font-size: 11px; font-weight: 700; color: #334155;")
        toolbar_layout.addWidget(label)

        self.root_path_edit = QLineEdit()
        self.root_path_edit.setMinimumWidth(420)
        self.root_path_edit.setPlaceholderText("Paste an aircraft path or a folder inside it, then press Enter.")
        self.root_path_edit.returnPressed.connect(self._open_root_from_edit)
        toolbar_layout.addWidget(self.root_path_edit, 1)

        self.btn_open_root = QPushButton("Open")
        self.btn_open_root.clicked.connect(self._open_root_from_edit)
        toolbar_layout.addWidget(self.btn_open_root)

        self.btn_browse_root = QPushButton("Browse…")
        self.btn_browse_root.clicked.connect(self._browse_root)
        toolbar_layout.addWidget(self.btn_browse_root)

        self.path_label = QLabel("No root open")
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        toolbar_layout.addWidget(self.path_label, 2)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh_tree)
        toolbar_layout.addWidget(self.btn_refresh)

        self.btn_reveal_target = QPushButton("Reveal Root")
        self.btn_reveal_target.clicked.connect(self._reveal_target)
        toolbar_layout.addWidget(self.btn_reveal_target)

        root.addWidget(toolbar)

        body = QSplitter(Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)

        left = QFrame()
        self._left_panel = left
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        left_title = QLabel("Files")
        left_title.setStyleSheet("font-size: 12px; font-weight: 700; color: #334155; padding: 12px 12px 8px 12px;")
        left_layout.addWidget(left_title)

        self.file_tree = _PageDropTree()
        self.file_tree.setHeaderHidden(True)
        self.file_tree.setMinimumWidth(240)
        self.file_tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        self.file_tree.itemClicked.connect(self._on_tree_item_clicked)
        self.file_tree.page_drop_requested.connect(self._drop_button_on_page)
        left_layout.addWidget(self.file_tree, 1)
        body.addWidget(left)

        center_split = QSplitter(Qt.Orientation.Horizontal)
        center_split.setChildrenCollapsible(False)

        right = QFrame()
        self._right_panel = right
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        self.file_label = QLabel("Select a config file")
        self.file_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.file_label.setOpenExternalLinks(False)
        self.file_label.linkActivated.connect(lambda _: self._close_button_editor_workspace())
        header.addWidget(self.file_label, 1)

        self.modified_label = QLabel("")
        header.addWidget(self.modified_label)

        self._view_zoom_bar = QWidget()
        _vzb = QHBoxLayout(self._view_zoom_bar)
        _vzb.setContentsMargins(0, 0, 0, 0)
        _vzb.setSpacing(8)

        self.btn_text_view = QPushButton("Text")
        self.btn_text_view.setCheckable(True)
        self.btn_text_view.clicked.connect(lambda: self._switch_mode("text"))
        _vzb.addWidget(self.btn_text_view)

        self.btn_visual_view = QPushButton("Visual")
        self.btn_visual_view.setCheckable(True)
        self.btn_visual_view.clicked.connect(lambda: self._switch_mode("visual"))
        _vzb.addWidget(self.btn_visual_view)

        self._view_group = QButtonGroup(self)
        self._view_group.setExclusive(True)
        self._view_group.addButton(self.btn_text_view)
        self._view_group.addButton(self.btn_visual_view)
        self.btn_text_view.setChecked(True)

        self.btn_zoom_out = QPushButton("−")
        self.btn_zoom_out.clicked.connect(lambda: self._set_visual_zoom(self._visual_zoom - 0.1))
        _vzb.addWidget(self.btn_zoom_out)

        self.btn_zoom_fit = QPushButton("Fit")
        self.btn_zoom_fit.clicked.connect(self._fit_visual_zoom)
        _vzb.addWidget(self.btn_zoom_fit)

        self.btn_zoom_in = QPushButton("+")
        self.btn_zoom_in.clicked.connect(lambda: self._set_visual_zoom(self._visual_zoom + 0.1))
        _vzb.addWidget(self.btn_zoom_in)

        self.zoom_label = QLabel("100%")
        _vzb.addWidget(self.zoom_label)

        header.addWidget(self._view_zoom_bar)

        right_layout.addLayout(header)

        self.stack = QStackedWidget()

        self.editor = QPlainTextEdit()
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.setWordWrapMode(QTextOption.WrapMode.NoWrap)
        self.editor.setPlaceholderText("Select a YAML or text file from the tree to edit it here.")
        self.editor.document().modificationChanged.connect(self._on_modification_changed)
        self.stack.addWidget(self.editor)

        self.visual_scroll = QScrollArea()
        self.visual_scroll.setWidgetResizable(True)
        self.visual_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.visual_root = QWidget()
        self.visual_layout = QVBoxLayout(self.visual_root)
        self.visual_layout.setContentsMargins(0, 0, 0, 0)
        self.visual_layout.setSpacing(10)

        self.visual_hint = QLabel("Visual mode is available for YAML page files with a `buttons:` list.")
        self.visual_hint.setWordWrap(True)
        self.visual_layout.addWidget(self.visual_hint)

        self.grid_host = _VisualGridHost()
        self.grid_host.resized.connect(self._position_span_cards)
        self.grid_layout = QGridLayout(self.grid_host)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        self.grid_layout.setSpacing(8)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.visual_layout.addWidget(self.grid_host)
        self.visual_layout.addStretch(1)

        self.visual_scroll.setWidget(self.visual_root)
        self.visual_scroll.verticalScrollBar().valueChanged.connect(lambda _value: self._queue_visible_previews())
        self.stack.addWidget(self.visual_scroll)

        self.config_form_scroll = QScrollArea()
        self.config_form_scroll.setWidgetResizable(True)
        self.config_form_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.config_form_host = QWidget()
        self.config_form_scroll.setWidget(self.config_form_host)
        config_form_wrap = QVBoxLayout(self.config_form_host)
        config_form_wrap.setContentsMargins(0, 0, 0, 0)
        config_form_wrap.setSpacing(8)
        self.config_form_hint = QLabel("Visual mode for layout config files.")
        self.config_form_hint.setWordWrap(True)
        config_form_wrap.addWidget(self.config_form_hint)
        self.config_form_section = QFrame()
        config_section_layout = QVBoxLayout(self.config_form_section)
        config_section_layout.setContentsMargins(10, 10, 10, 10)
        config_section_layout.setSpacing(8)
        self.config_form_title = QLabel("Layout Config")
        config_section_layout.addWidget(self.config_form_title)
        self.config_form = QFormLayout()
        self.config_form.setContentsMargins(0, 0, 0, 0)
        self.config_form.setSpacing(8)

        self.config_home_page_edit = QLineEdit()
        self.btn_pick_home_page = QPushButton("Find…")
        self.btn_pick_home_page.clicked.connect(lambda: self._open_layout_page_picker_for_line_edit(self.config_home_page_edit))
        self.config_form.addRow("Home Page", _field_with_button(self.config_home_page_edit, self.btn_pick_home_page))

        self.config_label_font_edit = QLineEdit()
        self.config_form.addRow("Label Font", self.config_label_font_edit)

        self.config_label_size = _NoWheelSpinBox()
        self.config_label_size.setRange(0, 256)
        self.config_label_size.setSpecialValueText("Unset")
        self.config_form.addRow("Label Size", self.config_label_size)

        self.config_text_size = _NoWheelSpinBox()
        self.config_text_size.setRange(0, 256)
        self.config_text_size.setSpecialValueText("Unset")
        self.config_form.addRow("Text Size", self.config_text_size)

        self.config_label_color_edit = QLineEdit()
        self.config_form.addRow("Label Color", self.config_label_color_edit)

        self.config_label_position = _NoWheelComboBox()
        self.config_label_position.setEditable(True)
        for value in ["", "ct", "cb", "lt", "lc", "lb", "rt", "rc", "rb"]:
            self.config_label_position.addItem(value or "Default", value)
        self.config_form.addRow("Label Position", self.config_label_position)

        self.config_vibrate = _NoWheelComboBox()
        self.config_vibrate.setEditable(True)
        for value in ["", "SHORT", "LONG"]:
            self.config_vibrate.addItem(value or "Default", value)
        self.config_form.addRow("Default Vibrate", self.config_vibrate)

        self.config_icon_color_edit = QLineEdit()
        self.config_form.addRow("Icon Color", self.config_icon_color_edit)

        self.config_ann_style = _NoWheelComboBox()
        self.config_ann_style.addItem("Default", "")
        self.config_ann_style.addItem("Korry", "k")
        self.config_ann_style.addItem("Vivisun", "v")
        self.config_form.addRow("Annun Style", self.config_ann_style)

        self.config_light_off_intensity = _NoWheelSpinBox()
        self.config_light_off_intensity.setRange(0, 100)
        self.config_light_off_intensity.setSpecialValueText("Unset")
        self.config_form.addRow("Light Off Intensity", self.config_light_off_intensity)

        self.config_fill_empty_keys = QCheckBox("Fill empty keys")
        self.config_form.addRow("Grid Fill", self.config_fill_empty_keys)

        config_section_layout.addLayout(self.config_form)
        config_section_layout.addStretch(1)
        config_form_wrap.addWidget(self.config_form_section, 0)
        config_form_wrap.addStretch(1)
        self.stack.addWidget(self.config_form_scroll)

        self.button_edit_page = QWidget()
        button_edit_layout = QVBoxLayout(self.button_edit_page)
        button_edit_layout.setContentsMargins(0, 0, 0, 0)
        button_edit_layout.setSpacing(0)

        self.button_edit_tabs = QTabWidget()

        self.button_visual_tab = QScrollArea()
        self.button_visual_tab.setWidgetResizable(True)
        self.button_visual_tab.setFrameShape(QFrame.Shape.NoFrame)
        self.button_visual_tab_host = QWidget()
        self.button_visual_tab.setWidget(self.button_visual_tab_host)
        visual_form_wrap = QVBoxLayout(self.button_visual_tab_host)
        visual_form_wrap.setContentsMargins(0, 0, 0, 0)
        visual_form_wrap.setSpacing(8)

        self.button_preview_label = QLabel()
        self.button_preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.button_preview_label.setMinimumSize(120, 120)
        self.button_preview_label.setMaximumSize(160, 160)
        self.button_preview_label.setWordWrap(True)
        self.button_preview_label.setStyleSheet("border: none;")
        self.button_preview_status = QLabel("Preview will appear here.")
        self.button_preview_status.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.visual_activation_section = QFrame()
        activation_layout = QVBoxLayout(self.visual_activation_section)
        activation_layout.setContentsMargins(10, 10, 10, 10)
        activation_layout.setSpacing(8)
        self.visual_activation_title = QLabel("Activation")
        activation_layout.addWidget(self.visual_activation_title)
        activation_form = QFormLayout()
        self.visual_activation_form = activation_form
        activation_form.setContentsMargins(0, 0, 0, 0)
        activation_form.setSpacing(8)

        self.visual_activation_family_combo = _NoWheelComboBox()
        for family in _ACTIVATION_SCHEMA:
            self.visual_activation_family_combo.addItem(family, family)
        activation_form.addRow("Family", self.visual_activation_family_combo)

        self.visual_type_combo = _NoWheelComboBox()
        activation_form.addRow("Subtype", self.visual_type_combo)

        self.visual_command_edit = QLineEdit()
        self.visual_command_edit.setPlaceholderText("sim/...")
        self.btn_pick_command = QPushButton("Find…")
        self.btn_pick_command.clicked.connect(lambda: self._open_suggestion_picker_for_line_edit(self.visual_command_edit, "command"))
        self.visual_command_row = _field_with_button(self.visual_command_edit, self.btn_pick_command)
        activation_form.addRow("Command", self.visual_command_row)

        self.visual_command_pair_host = QWidget()
        self.visual_command_pair_layout = QFormLayout(self.visual_command_pair_host)
        self.visual_command_pair_layout.setContentsMargins(0, 0, 0, 0)
        self.visual_command_pair_layout.setSpacing(6)
        self.visual_command1_label = QLabel("Primary")
        self.visual_command1_edit = QLineEdit()
        self.visual_command1_edit.setPlaceholderText("sim/...")
        self.btn_pick_command1 = QPushButton("Find…")
        self.btn_pick_command1.clicked.connect(lambda: self._open_suggestion_picker_for_line_edit(self.visual_command1_edit, "command"))
        self.visual_command_pair_layout.addRow(self.visual_command1_label, _field_with_button(self.visual_command1_edit, self.btn_pick_command1))
        self.visual_command2_label = QLabel("Secondary")
        self.visual_command2_edit = QLineEdit()
        self.visual_command2_edit.setPlaceholderText("sim/...")
        self.btn_pick_command2 = QPushButton("Find…")
        self.btn_pick_command2.clicked.connect(lambda: self._open_suggestion_picker_for_line_edit(self.visual_command2_edit, "command"))
        self.visual_command_pair_layout.addRow(self.visual_command2_label, _field_with_button(self.visual_command2_edit, self.btn_pick_command2))
        activation_form.addRow("Commands", self.visual_command_pair_host)

        self.visual_page_edit = QLineEdit()
        self.btn_pick_page = QPushButton("Find…")
        self.btn_pick_page.clicked.connect(lambda: self._open_layout_page_picker_for_line_edit(self.visual_page_edit))
        self.visual_page_row = _field_with_button(self.visual_page_edit, self.btn_pick_page)
        activation_form.addRow("Page", self.visual_page_row)

        self.visual_pages_edit = QLineEdit()
        self.visual_pages_edit.setPlaceholderText("index, page2")
        self.btn_pick_pages = QPushButton("Choose…")
        self.btn_pick_pages.clicked.connect(lambda: self._open_layout_pages_picker_for_line_edit(self.visual_pages_edit))
        self.visual_pages_row = _field_with_button(self.visual_pages_edit, self.btn_pick_pages)
        activation_form.addRow("Pages", self.visual_pages_row)

        self.visual_deck_edit = QLineEdit()
        self.visual_deck_row = self.visual_deck_edit
        activation_form.addRow("Remote Deck", self.visual_deck_row)
        activation_layout.addLayout(activation_form)

        _activation_col = QWidget()
        _activation_col_layout = QVBoxLayout(_activation_col)
        _activation_col_layout.setContentsMargins(0, 0, 0, 0)
        _activation_col_layout.setSpacing(4)
        _activation_col_layout.addWidget(self.button_preview_label, 0, Qt.AlignmentFlag.AlignCenter)
        _activation_col_layout.addWidget(self.button_preview_status, 0, Qt.AlignmentFlag.AlignCenter)
        _activation_col_layout.addWidget(self.visual_activation_section, 0)
        _activation_col_layout.addStretch(1)

        _visual_columns = QWidget()
        _visual_columns_layout = QHBoxLayout(_visual_columns)
        _visual_columns_layout.setContentsMargins(0, 0, 0, 0)
        _visual_columns_layout.setSpacing(8)
        _visual_columns_layout.addWidget(_activation_col, 1)

        self.visual_representation_section = QFrame()
        representation_layout = QVBoxLayout(self.visual_representation_section)
        representation_layout.setContentsMargins(10, 10, 10, 10)
        representation_layout.setSpacing(8)
        self.visual_representation_title = QLabel("Representation")
        representation_layout.addWidget(self.visual_representation_title)
        representation_form = QFormLayout()
        self.visual_representation_form = representation_form
        representation_form.setContentsMargins(0, 0, 0, 0)
        representation_form.setSpacing(8)

        self.visual_representation_family_combo = _NoWheelComboBox()
        for family in _REPRESENTATION_SCHEMA:
            self.visual_representation_family_combo.addItem(family, family)
        representation_form.addRow("Family", self.visual_representation_family_combo)

        self.visual_style_combo = _NoWheelComboBox()
        representation_form.addRow("Subtype", self.visual_style_combo)

        span_row_widget = QWidget()
        span_row_layout = QHBoxLayout(span_row_widget)
        span_row_layout.setContentsMargins(0, 0, 0, 0)
        span_row_layout.setSpacing(6)
        self.visual_span_cols = _NoWheelSpinBox()
        self.visual_span_cols.setRange(1, 12)
        self.visual_span_cols.setSpecialValueText("1")
        self.visual_span_cols.setToolTip("Columns this button spans")
        self.visual_span_rows = _NoWheelSpinBox()
        self.visual_span_rows.setRange(1, 9)
        self.visual_span_rows.setSpecialValueText("1")
        self.visual_span_rows.setToolTip("Rows this button spans")
        span_row_layout.addWidget(QLabel("Cols"))
        span_row_layout.addWidget(self.visual_span_cols)
        span_row_layout.addWidget(QLabel("×  Rows"))
        span_row_layout.addWidget(self.visual_span_rows)
        span_row_layout.addStretch(1)
        self.visual_span_row = span_row_widget
        representation_form.addRow("Span", self.visual_span_row)

        self.visual_label_edit = QLineEdit()
        self.visual_label_row = self.visual_label_edit
        representation_form.addRow("Label", self.visual_label_row)

        self.visual_label_size = _NoWheelSpinBox()
        self.visual_label_size.setRange(0, 256)
        self.visual_label_size.setSpecialValueText("Default")
        self.visual_label_size_row = self.visual_label_size
        representation_form.addRow("Label Size", self.visual_label_size_row)

        self.visual_label_color_edit = QLineEdit()
        self.visual_label_color_edit.setPlaceholderText("white")
        self.visual_label_color_row = self.visual_label_color_edit
        representation_form.addRow("Label Color", self.visual_label_color_row)

        self.visual_text_edit = QLineEdit()
        self.visual_text_row = self.visual_text_edit
        representation_form.addRow("Text", self.visual_text_row)

        self.visual_text_size = _NoWheelSpinBox()
        self.visual_text_size.setRange(0, 256)
        self.visual_text_size.setSpecialValueText("Default")
        self.visual_text_size_row = self.visual_text_size
        representation_form.addRow("Text Size", self.visual_text_size_row)

        self.visual_text_color_edit = QLineEdit()
        self.visual_text_color_row = self.visual_text_color_edit
        representation_form.addRow("Text Color", self.visual_text_color_row)

        self.visual_ann_model = _NoWheelComboBox()
        for value in ["A", "B", "C", "D", "E", "F"]:
            self.visual_ann_model.addItem(value, value)
        self.visual_ann_model_row = self.visual_ann_model
        representation_form.addRow("Annun Model", self.visual_ann_model_row)

        self.visual_ann_style = _NoWheelComboBox()
        self.visual_ann_style.addItem("Default", "")
        self.visual_ann_style.addItem("Korry", "k")
        self.visual_ann_style.addItem("Vivisun", "v")
        self.visual_ann_style_row = self.visual_ann_style
        representation_form.addRow("Annun Style", self.visual_ann_style_row)

        self.visual_ann_size = _NoWheelComboBox()
        for value, label in [("full", "Full"), ("large", "Large"), ("medium", "Medium"), ("small", "Small")]:
            self.visual_ann_size.addItem(label, value)
        self.visual_ann_size_row = self.visual_ann_size
        representation_form.addRow("Annun Size", self.visual_ann_size_row)

        self.visual_ann_parts_host = QWidget()
        self.visual_ann_parts_layout = QFormLayout(self.visual_ann_parts_host)
        self.visual_ann_parts_layout.setContentsMargins(0, 0, 0, 0)
        self.visual_ann_parts_layout.setSpacing(6)
        self.visual_ann_part_rows: list[dict] = []
        for idx in range(4):
            part_label = QLabel(f"Part {idx + 1}")
            part_host = QWidget()
            part_host_layout = QVBoxLayout(part_host)
            part_host_layout.setContentsMargins(0, 0, 0, 0)
            part_host_layout.setSpacing(6)

            text_edit = QLineEdit()
            text_edit.setPlaceholderText("Part text")
            part_host_layout.addWidget(text_edit)

            part_font_row = QWidget()
            part_font_layout = QHBoxLayout(part_font_row)
            part_font_layout.setContentsMargins(0, 0, 0, 0)
            part_font_layout.setSpacing(6)
            font_combo = _NoWheelComboBox()
            font_combo.setEditable(True)
            font_combo.addItem("(default)", "")
            font_combo.setCurrentIndex(0)
            font_combo.lineEdit().setPlaceholderText("font name (default)")
            part_font_layout.addWidget(QLabel("Font"))
            part_font_layout.addWidget(font_combo, 1)
            part_host_layout.addWidget(part_font_row)

            part_size_color_row = QWidget()
            part_size_color_layout = QHBoxLayout(part_size_color_row)
            part_size_color_layout.setContentsMargins(0, 0, 0, 0)
            part_size_color_layout.setSpacing(6)
            text_size = _NoWheelSpinBox()
            text_size.setRange(0, 256)
            text_size.setSpecialValueText("Default")
            text_size.setFixedWidth(80)
            part_size_color_layout.addWidget(QLabel("Size"))
            part_size_color_layout.addWidget(text_size)
            color_edit = QLineEdit()
            color_edit.setPlaceholderText("color")
            part_size_color_layout.addWidget(QLabel("Color"))
            part_size_color_layout.addWidget(color_edit, 1)
            part_host_layout.addWidget(part_size_color_row)

            formula_edit = QLineEdit()
            formula_edit.setPlaceholderText("${sim/...}")
            btn_pick_formula = QPushButton("Find DataRef…")
            btn_pick_formula.clicked.connect(lambda _=False, edit=formula_edit: self._open_suggestion_picker_for_formula_edit(edit))
            part_host_layout.addWidget(_field_with_button(formula_edit, btn_pick_formula))

            led_row = QWidget()
            led_row_layout = QHBoxLayout(led_row)
            led_row_layout.setContentsMargins(0, 0, 0, 0)
            led_row_layout.setSpacing(6)
            led_row_layout.addWidget(QLabel("LED"))
            led_combo = _NoWheelComboBox()
            for _led_val, _led_label in (("", "None (text only)"), ("bar", "bar"), ("bars", "bars"), ("block", "block"), ("dot", "dot"), ("lgear", "lgear"), ("led", "led")):
                led_combo.addItem(_led_label, _led_val)
            led_row_layout.addWidget(led_combo, 1)
            part_host_layout.addWidget(led_row)

            self.visual_ann_parts_layout.addRow(part_label, part_host)
            self.visual_ann_part_rows.append(
                {
                    "label": part_label,
                    "host": part_host,
                    "text_edit": text_edit,
                    "font_combo": font_combo,
                    "text_size": text_size,
                    "color_edit": color_edit,
                    "formula_edit": formula_edit,
                    "pick_button": btn_pick_formula,
                    "led_combo": led_combo,
                }
            )
        self.visual_ann_parts_row = self.visual_ann_parts_host
        representation_form.addRow("Parts", self.visual_ann_parts_row)

        # ── Gauge fields ────────────────────────────────────────────────────────
        gauge_tick_range_row = QWidget()
        gauge_tick_range_layout = QHBoxLayout(gauge_tick_range_row)
        gauge_tick_range_layout.setContentsMargins(0, 0, 0, 0)
        gauge_tick_range_layout.setSpacing(6)
        self.visual_gauge_tick_from = _NoWheelSpinBox()
        self.visual_gauge_tick_from.setRange(-360, 360)
        self.visual_gauge_tick_from.setValue(-120)
        self.visual_gauge_tick_to = _NoWheelSpinBox()
        self.visual_gauge_tick_to.setRange(-360, 360)
        self.visual_gauge_tick_to.setValue(120)
        self.visual_gauge_ticks = _NoWheelSpinBox()
        self.visual_gauge_ticks.setRange(1, 24)
        self.visual_gauge_ticks.setValue(9)
        self.visual_gauge_offset = _NoWheelSpinBox()
        self.visual_gauge_offset.setRange(-200, 200)
        self.visual_gauge_offset.setValue(20)
        gauge_tick_range_layout.addWidget(QLabel("From"))
        gauge_tick_range_layout.addWidget(self.visual_gauge_tick_from)
        gauge_tick_range_layout.addWidget(QLabel("To"))
        gauge_tick_range_layout.addWidget(self.visual_gauge_tick_to)
        gauge_tick_range_layout.addWidget(QLabel("Ticks"))
        gauge_tick_range_layout.addWidget(self.visual_gauge_ticks)
        gauge_tick_range_layout.addWidget(QLabel("Offset"))
        gauge_tick_range_layout.addWidget(self.visual_gauge_offset)
        self.visual_gauge_tick_range_row = gauge_tick_range_row
        representation_form.addRow("Tick Range", self.visual_gauge_tick_range_row)

        gauge_needle_row = QWidget()
        gauge_needle_layout = QHBoxLayout(gauge_needle_row)
        gauge_needle_layout.setContentsMargins(0, 0, 0, 0)
        gauge_needle_layout.setSpacing(6)
        self.visual_gauge_needle_color = QLineEdit()
        self.visual_gauge_needle_color.setPlaceholderText("white")
        self.visual_gauge_needle_width = _NoWheelSpinBox()
        self.visual_gauge_needle_width.setRange(0, 32)
        self.visual_gauge_needle_width.setSpecialValueText("Default")
        self.visual_gauge_needle_width.setFixedWidth(70)
        self.visual_gauge_needle_length = _NoWheelSpinBox()
        self.visual_gauge_needle_length.setRange(0, 200)
        self.visual_gauge_needle_length.setSpecialValueText("Default")
        self.visual_gauge_needle_length.setFixedWidth(70)
        gauge_needle_layout.addWidget(QLabel("Color"))
        gauge_needle_layout.addWidget(self.visual_gauge_needle_color, 1)
        gauge_needle_layout.addWidget(QLabel("W"))
        gauge_needle_layout.addWidget(self.visual_gauge_needle_width)
        gauge_needle_layout.addWidget(QLabel("Len"))
        gauge_needle_layout.addWidget(self.visual_gauge_needle_length)
        self.visual_gauge_needle_row = gauge_needle_row
        representation_form.addRow("Needle", self.visual_gauge_needle_row)

        gauge_ticks_style_row = QWidget()
        gauge_ticks_style_layout = QHBoxLayout(gauge_ticks_style_row)
        gauge_ticks_style_layout.setContentsMargins(0, 0, 0, 0)
        gauge_ticks_style_layout.setSpacing(6)
        self.visual_gauge_tick_color = QLineEdit()
        self.visual_gauge_tick_color.setPlaceholderText("white")
        self.visual_gauge_tick_width = _NoWheelSpinBox()
        self.visual_gauge_tick_width.setRange(0, 32)
        self.visual_gauge_tick_width.setSpecialValueText("Default")
        self.visual_gauge_tick_width.setFixedWidth(70)
        self.visual_gauge_tick_label_size = _NoWheelSpinBox()
        self.visual_gauge_tick_label_size.setRange(0, 64)
        self.visual_gauge_tick_label_size.setSpecialValueText("Default")
        self.visual_gauge_tick_label_size.setFixedWidth(70)
        gauge_ticks_style_layout.addWidget(QLabel("Color"))
        gauge_ticks_style_layout.addWidget(self.visual_gauge_tick_color, 1)
        gauge_ticks_style_layout.addWidget(QLabel("W"))
        gauge_ticks_style_layout.addWidget(self.visual_gauge_tick_width)
        gauge_ticks_style_layout.addWidget(QLabel("Label Sz"))
        gauge_ticks_style_layout.addWidget(self.visual_gauge_tick_label_size)
        self.visual_gauge_ticks_style_row = gauge_ticks_style_row
        representation_form.addRow("Tick Style", self.visual_gauge_ticks_style_row)

        self.visual_gauge_formula_edit = QLineEdit()
        self.visual_gauge_formula_edit.setPlaceholderText("${sim/dataref} ticks * max_val /")
        btn_pick_gauge_formula = QPushButton("Find…")
        btn_pick_gauge_formula.clicked.connect(lambda: self._open_suggestion_picker_for_formula_edit(self.visual_gauge_formula_edit))
        self.visual_gauge_formula_row = _field_with_button(self.visual_gauge_formula_edit, btn_pick_gauge_formula)
        representation_form.addRow("Formula", self.visual_gauge_formula_row)

        self.visual_gauge_tick_labels = QPlainTextEdit()
        self.visual_gauge_tick_labels.setPlaceholderText("One label per line\n(e.g. 0\n5\n10\n...)")
        self.visual_gauge_tick_labels.setFixedHeight(80)
        self.visual_gauge_tick_labels_row = self.visual_gauge_tick_labels
        representation_form.addRow("Tick Labels", self.visual_gauge_tick_labels_row)
        # ── end gauge fields ─────────────────────────────────────────────────────

        representation_layout.addLayout(representation_form)
        _visual_columns_layout.addWidget(self.visual_representation_section, 1, Qt.AlignmentFlag.AlignTop)
        visual_form_wrap.addWidget(_visual_columns, 1)

        self.button_advanced_tab = QWidget()
        advanced_layout = QVBoxLayout(self.button_advanced_tab)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(8)
        self.button_advanced_status = QLabel("Unsupported or untouched fields are preserved and shown here for reference.")
        self.button_advanced_status.setWordWrap(True)
        advanced_layout.addWidget(self.button_advanced_status)
        self.button_advanced_preview = QPlainTextEdit()
        self.button_advanced_preview.setReadOnly(True)
        self.button_advanced_preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.button_advanced_preview.setWordWrapMode(QTextOption.WrapMode.NoWrap)
        advanced_layout.addWidget(self.button_advanced_preview, 1)
        self.button_edit_tabs.addTab(self.button_visual_tab, "Visual")

        self.button_yaml_tools = QWidget()
        button_yaml_tools_layout = QHBoxLayout(self.button_yaml_tools)
        button_yaml_tools_layout.setContentsMargins(0, 0, 0, 0)
        button_yaml_tools_layout.setSpacing(8)
        self.btn_yaml_insert_command = QPushButton("Insert Command…")
        self.btn_yaml_insert_command.clicked.connect(lambda: self._insert_picker_value_into_yaml("command"))
        button_yaml_tools_layout.addWidget(self.btn_yaml_insert_command, 0)
        self.btn_yaml_insert_dataref = QPushButton("Insert DataRef…")
        self.btn_yaml_insert_dataref.clicked.connect(lambda: self._insert_picker_value_into_yaml("dataref"))
        button_yaml_tools_layout.addWidget(self.btn_yaml_insert_dataref, 0)
        button_yaml_tools_layout.addStretch(1)

        self.button_edit_editor = QPlainTextEdit()
        self.button_edit_editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.button_edit_editor.setWordWrapMode(QTextOption.WrapMode.NoWrap)
        self.button_edit_editor.textChanged.connect(self._on_button_yaml_text_changed)
        self.button_yaml_tab = QWidget()
        button_yaml_layout = QVBoxLayout(self.button_yaml_tab)
        button_yaml_layout.setContentsMargins(0, 0, 0, 0)
        button_yaml_layout.setSpacing(8)
        button_yaml_layout.addWidget(self.button_yaml_tools, 0)
        button_yaml_layout.addWidget(self.button_edit_editor, 1)
        self.button_edit_tabs.addTab(self.button_yaml_tab, "YAML")
        self.button_edit_tabs.addTab(self.button_advanced_tab, "Advanced")
        button_edit_layout.addWidget(self.button_edit_tabs, 1)

        for widget, signal_name in (
            (self.visual_activation_family_combo, "currentIndexChanged"),
            (self.visual_type_combo, "currentIndexChanged"),
            (self.visual_representation_family_combo, "currentIndexChanged"),
            (self.visual_style_combo, "currentIndexChanged"),
            (self.visual_command_edit, "textChanged"),
            (self.visual_command1_edit, "textChanged"),
            (self.visual_command2_edit, "textChanged"),
            (self.visual_page_edit, "textChanged"),
            (self.visual_pages_edit, "textChanged"),
            (self.visual_deck_edit, "textChanged"),
            (self.visual_span_cols, "valueChanged"),
            (self.visual_span_rows, "valueChanged"),
            (self.visual_label_edit, "textChanged"),
            (self.visual_label_size, "valueChanged"),
            (self.visual_label_color_edit, "textChanged"),
            (self.visual_text_edit, "textChanged"),
            (self.visual_text_size, "valueChanged"),
            (self.visual_text_color_edit, "textChanged"),
            (self.visual_ann_model, "currentIndexChanged"),
            (self.visual_ann_style, "currentIndexChanged"),
            (self.visual_ann_size, "currentIndexChanged"),
            (self.visual_gauge_tick_from, "valueChanged"),
            (self.visual_gauge_tick_to, "valueChanged"),
            (self.visual_gauge_ticks, "valueChanged"),
            (self.visual_gauge_offset, "valueChanged"),
            (self.visual_gauge_needle_color, "textChanged"),
            (self.visual_gauge_needle_width, "valueChanged"),
            (self.visual_gauge_needle_length, "valueChanged"),
            (self.visual_gauge_tick_color, "textChanged"),
            (self.visual_gauge_tick_width, "valueChanged"),
            (self.visual_gauge_tick_label_size, "valueChanged"),
            (self.visual_gauge_formula_edit, "textChanged"),
        ):
            getattr(widget, signal_name).connect(self._apply_visual_fields_to_yaml)
        self.visual_gauge_tick_labels.textChanged.connect(self._apply_visual_fields_to_yaml)
        for row in self.visual_ann_part_rows:
            row["text_edit"].textChanged.connect(self._apply_visual_fields_to_yaml)
            row["font_combo"].currentTextChanged.connect(self._apply_visual_fields_to_yaml)
            row["text_size"].valueChanged.connect(self._apply_visual_fields_to_yaml)
            row["color_edit"].textChanged.connect(self._apply_visual_fields_to_yaml)
            row["formula_edit"].textChanged.connect(self._apply_visual_fields_to_yaml)
            row["led_combo"].currentIndexChanged.connect(self._apply_visual_fields_to_yaml)

        for widget, signal_name in (
            (self.config_home_page_edit, "textChanged"),
            (self.config_label_font_edit, "textChanged"),
            (self.config_label_size, "valueChanged"),
            (self.config_text_size, "valueChanged"),
            (self.config_label_color_edit, "textChanged"),
            (self.config_label_position, "currentIndexChanged"),
            (self.config_vibrate, "currentIndexChanged"),
            (self.config_icon_color_edit, "textChanged"),
            (self.config_ann_style, "currentIndexChanged"),
            (self.config_light_off_intensity, "valueChanged"),
            (self.config_fill_empty_keys, "stateChanged"),
        ):
            getattr(widget, signal_name).connect(self._apply_config_fields_to_editor)

        self.stack.addWidget(self.button_edit_page)

        right_layout.addWidget(self.stack, 1)

        self.designer_panel = QFrame()
        self._designer_panel = self.designer_panel
        designer_layout = QVBoxLayout(self.designer_panel)
        designer_layout.setContentsMargins(10, 10, 10, 10)
        designer_layout.setSpacing(6)
        self.designer_title = QLabel("Designer Help")
        designer_layout.addWidget(self.designer_title)
        self.selected_button_label = QLabel("Select a button in Visual mode.")
        self.selected_button_label.setWordWrap(True)
        designer_layout.addWidget(self.selected_button_label)
        self.slot_caps_label = QLabel("")
        self.slot_caps_label.setWordWrap(True)
        designer_layout.addWidget(self.slot_caps_label)
        self.slot_repr_label = QLabel("")
        self.slot_repr_label.setWordWrap(True)
        designer_layout.addWidget(self.slot_repr_label)
        preset_row = QHBoxLayout()
        preset_row.setContentsMargins(0, 0, 0, 0)
        preset_row.setSpacing(8)
        self.preset_combo = _NoWheelComboBox()
        self.preset_combo.currentIndexChanged.connect(self._update_preset_preview)
        preset_row.addWidget(self.preset_combo, 1)
        self.btn_apply_preset = QPushButton("Apply Preset")
        self.btn_apply_preset.clicked.connect(self._apply_selected_preset)
        preset_row.addWidget(self.btn_apply_preset)
        designer_layout.addLayout(preset_row)
        self.preset_hint = QLabel("")
        self.preset_hint.setWordWrap(True)
        designer_layout.addWidget(self.preset_hint)
        self.preset_editor = QPlainTextEdit()
        self.preset_editor.setReadOnly(True)
        self.preset_editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.preset_editor.setWordWrapMode(QTextOption.WrapMode.NoWrap)
        self.preset_editor.setMaximumHeight(140)
        designer_layout.addWidget(self.preset_editor)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        self.status_label = QLabel("Editor ready")
        actions.addWidget(self.status_label, 1)

        self.btn_reveal_file = QPushButton("Reveal File")
        self.btn_reveal_file.clicked.connect(self._reveal_file)
        actions.addWidget(self.btn_reveal_file)

        right_layout.addLayout(actions)
        center_split.addWidget(right)

        self.designer_panel.setMinimumWidth(280)
        self.designer_panel.setVisible(False)
        center_split.addWidget(self.designer_panel)
        center_split.setStretchFactor(0, 1)
        center_split.setStretchFactor(1, 0)
        center_split.setSizes([1240, 0])

        body.addWidget(center_split)
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        left.setMinimumWidth(260)
        body.setSizes([320, 1180])

        root.addWidget(body, 1)

        self._apply_theme()
        self._populate_presets()
        self._update_action_state()

    def open_root_path(self, path: str | Path | None) -> None:
        normalized = self._normalize_root_path(path)
        self._set_target_path(normalized)

    def refresh_tree(self) -> None:
        current = self._current_target_path
        self.file_tree.clear()
        if current is None or not current.exists():
            self.status_label.setText("Open a valid root to edit.")
            self._update_action_state()
            return

        files = self._collect_target_files(current)
        if not files:
            self.status_label.setText("No editable config files found under this root.")
            self._update_action_state()
            return

        nodes: dict[tuple[str, ...], QTreeWidgetItem] = {}
        for rel_path in files:
            parent: QTreeWidgetItem | QTreeWidget = self.file_tree
            parts = rel_path.parts
            for depth, part in enumerate(parts):
                key = tuple(parts[: depth + 1])
                item = nodes.get(key)
                if item is None:
                    item = QTreeWidgetItem([part])
                    if depth == len(parts) - 1:
                        item.setData(0, Qt.ItemDataRole.UserRole, str(current / rel_path))
                    parent.addChild(item) if isinstance(parent, QTreeWidgetItem) else parent.addTopLevelItem(item)
                    nodes[key] = item
                node_path = current / Path(*key)
                if node_path.is_dir():
                    config_path = node_path / "config.yaml"
                    if config_path.is_file() and not item.data(0, Qt.ItemDataRole.UserRole):
                        item.setData(0, Qt.ItemDataRole.UserRole, str(config_path))
                parent = item

        self.file_tree.expandAll()
        self._update_tree_dirty_state()
        self.status_label.setText(f"{len(files)} editable files loaded.")
        self._update_action_state()

    def save_current_file(self) -> bool:
        if self._current_file_path is None:
            return False
        try:
            self._current_file_path.write_text(self.editor.toPlainText(), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Save failed", str(exc))
            self.status_label.setText("Save failed.")
            self.log_line.emit(f"[error] editor save failed: {exc}")
            return False
        self.editor.document().setModified(False)
        self._update_tree_dirty_state()
        self.status_label.setText(f"Saved {self._current_file_path.name}")
        self.log_line.emit(f"[editor] saved {self._current_file_path}")
        self._update_action_state()
        return True

    def _autosave_page(self) -> None:
        if self._current_file_path is None or not self.editor.document().isModified():
            return
        self.save_current_file()

    def _autosave_button(self) -> None:
        if self._button_edit_id is None:
            return
        text = self.button_edit_editor.toPlainText()
        try:
            data = yaml.safe_load(text) or {}
        except Exception:
            return
        if not isinstance(data, dict):
            return
        ok = self._apply_button_yaml(self._button_edit_id, text, silent=True)
        if ok:
            self._button_edit_base_text = text
            self._button_doc.load_text(text)
            self._page_autosave_timer.stop()
            self.save_current_file()
            self._schedule_button_edit_preview()

    def _collect_target_files(self, target_root: Path) -> list[Path]:
        allowed_suffixes = {".yaml", ".yml", ".json", ".txt", ".j2", ".css", ".js"}
        results: list[Path] = []
        for path in sorted(target_root.rglob("*")):
            if not path.is_file():
                continue
            if any(part.startswith(".") for part in path.relative_to(target_root).parts):
                continue
            if path.suffix.lower() not in allowed_suffixes:
                continue
            results.append(path.relative_to(target_root))
        return results

    def _normalize_root_path(self, path: str | Path | None) -> Path | None:
        raw = str(path or "").strip()
        if not raw:
            return None
        candidate = Path(raw).expanduser()
        if not candidate.exists():
            return None
        if candidate.is_file():
            candidate = candidate.parent
        if candidate.name == "deckconfig" and (candidate / "config.yaml").is_file():
            return candidate.parent
        for base in (candidate, *candidate.parents):
            if (base / "deckconfig" / "config.yaml").is_file():
                return base
        return candidate

    def _open_root_from_edit(self) -> None:
        raw = self.root_path_edit.text().strip()
        if not raw:
            self.open_root_path(None)
            return
        normalized = self._normalize_root_path(raw)
        if normalized is None:
            QMessageBox.warning(self, "Open Root", f"Path does not exist:\n\n{raw}")
            return
        self._set_target_path(normalized)

    def _browse_root(self) -> None:
        start = self.root_path_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Choose Root Folder", start)
        if chosen:
            self.root_path_edit.setText(chosen)
            self._open_root_from_edit()

    def _set_target_path(self, path: Path | None) -> None:
        self._current_target_path = path
        display = "" if path is None else str(path)
        self.root_path_edit.setText(display)
        settings = load_settings()
        settings["COCKPITDECKS_TARGET"] = display
        save_settings(settings)
        self.root_path_changed.emit(display)
        self._suggestion_cache.clear()
        self._refresh_font_combos()
        self._current_file_path = None
        self._set_editor_text("")
        self.file_label.setText("Select a config file")
        self.modified_label.setText("")
        self._visual_reset()
        self.path_label.setText("No root open" if path is None else _short_path(path))
        self._warm_preview_pool_async(path)
        self.refresh_tree()

    def _suggestion_target_key(self) -> str:
        if self._current_target_path is None:
            return ""
        try:
            return str(self._current_target_path.resolve())
        except OSError:
            return str(self._current_target_path)

    def _datarefs_txt_path(self) -> Path:
        return Path.home() / "X-Plane 12" / "Resources" / "plugins" / "DataRefs.txt"

    def _load_dataref_suggestions(self) -> list[tuple[str, str]]:
        cache_key = ("dataref", self._suggestion_target_key())
        cached = self._suggestion_cache.get(cache_key)
        if cached is not None:
            return cached
        items: list[tuple[str, str]] = []
        path = self._datarefs_txt_path()
        if path.exists():
            try:
                with path.open("r", encoding="utf-8", errors="ignore") as fh:
                    for raw in fh:
                        line = raw.strip()
                        if not line or not line.startswith("sim/"):
                            continue
                        parts = re.split(r"\t+", line)
                        value = parts[0].strip()
                        detail = " | ".join(p.strip() for p in parts[1:4] if p.strip())
                        items.append((value, detail))
            except OSError:
                items = []
        self._suggestion_cache[cache_key] = items
        return items

    def _load_command_suggestions(self) -> list[tuple[str, str]]:
        cache_key = ("command", self._suggestion_target_key())
        cached = self._suggestion_cache.get(cache_key)
        if cached is not None:
            return cached
        items: dict[str, str] = {}
        target = self._current_target_path
        if target is not None:
            for path in target.rglob("*.yaml"):
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                for match in re.finditer(r"(?m)^\s*command:\s*['\"]?([^#\n'\"]+)['\"]?\s*$", text):
                    value = match.group(1).strip()
                    if value and value not in items:
                        items[value] = path.relative_to(target).as_posix()
                for match in re.finditer(r"(?m)^\s*-\s*command:\s*['\"]?([^#\n'\"]+)['\"]?\s*$", text):
                    value = match.group(1).strip()
                    if value and value not in items:
                        items[value] = path.relative_to(target).as_posix()
        result = sorted(items.items(), key=lambda item: item[0].lower())
        self._suggestion_cache[cache_key] = result
        return result

    def _suggestions_for_kind(self, kind: str) -> list[tuple[str, str]]:
        if kind == "dataref":
            return self._load_dataref_suggestions()
        if kind == "page":
            return self._load_layout_page_suggestions()
        return self._load_command_suggestions()

    def _load_layout_page_suggestions(self) -> list[tuple[str, str]]:
        if self._current_file_path is None:
            return []
        layout_dir = self._current_file_path.parent
        cache_key = f"layout-pages::{layout_dir}"
        cached = self._suggestion_cache.get(cache_key)
        if cached is not None:
            return cached
        suggestions: list[tuple[str, str]] = []
        for path in sorted(layout_dir.glob("*.y*ml")):
            if not path.is_file() or path.name == "config.yaml":
                continue
            suggestions.append((path.stem, path.name))
        self._suggestion_cache[cache_key] = suggestions
        return suggestions

    def _open_suggestion_picker(self, kind: str) -> str:
        if kind == "dataref":
            title = "Find DataRef"
        elif kind == "page":
            title = "Find Page"
        else:
            title = "Find Command"
        suggestions = self._suggestions_for_kind(kind)
        if not suggestions:
            QMessageBox.information(
                self,
                title,
                "No suggestions are available right now.\n\nDataRefs come from X-Plane's DataRefs.txt. Commands are suggested from the current target config. Pages are suggested from the current layout folder.",
            )
            return ""
        dialog = _SuggestionPickerDialog(title, suggestions, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return ""
        return dialog.selected_value()

    def _open_suggestion_picker_for_line_edit(self, edit: QLineEdit, kind: str) -> None:
        value = self._open_suggestion_picker(kind)
        if value:
            edit.setText(value)

    def _open_suggestion_picker_for_formula_edit(self, edit: QLineEdit) -> None:
        value = self._open_suggestion_picker("dataref")
        if value:
            _insert_dataref_formula(edit, value)

    def _open_layout_page_picker_for_line_edit(self, edit: QLineEdit) -> None:
        value = self._open_suggestion_picker("page")
        if value:
            edit.setText(value)

    def _open_layout_pages_picker_for_line_edit(self, edit: QLineEdit) -> None:
        suggestions = self._load_layout_page_suggestions()
        if not suggestions:
            QMessageBox.information(
                self,
                "Choose Pages",
                "No layout pages are available right now.\n\nPages are suggested from the current layout folder.",
            )
            return
        current = [part.strip() for part in edit.text().split(",") if part.strip()]
        dialog = _MultiSuggestionPickerDialog("Choose Pages", suggestions, selected=current, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.selected_values()
        if values:
            edit.setText(", ".join(values))

    def _insert_picker_value_into_yaml(self, kind: str) -> None:
        value = self._open_suggestion_picker(kind)
        if not value:
            return
        cursor = self.button_edit_editor.textCursor()
        cursor.insertText(value)
        self.button_edit_editor.setTextCursor(cursor)

    def _on_tree_selection_changed(self) -> None:
        items = self.file_tree.selectedItems()
        if not items:
            return
        item = items[0]
        raw_path = item.data(0, Qt.ItemDataRole.UserRole)
        if not raw_path:
            return
        path = Path(str(raw_path))
        if self.editor.document().isModified():
            self._page_autosave_timer.stop()
            self.save_current_file()
        self._load_file(path)

    def _on_tree_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        raw_path = item.data(0, Qt.ItemDataRole.UserRole)
        if not raw_path or self._current_target_path is None:
            return
        target_path = self._current_target_path / str(raw_path)
        if self._current_file_path is None or target_path != self._current_file_path:
            return
        if self.stack.currentWidget() is self.button_edit_page:
            self._close_button_editor_workspace()

    def _load_file(self, path: Path) -> None:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Open failed", str(exc))
            self.status_label.setText("Failed to open file.")
            self.log_line.emit(f"[error] editor open failed: {exc}")
            return
        self._current_file_path = path
        self._set_editor_text(content)
        self._update_tree_dirty_state()
        self.file_label.setText(path.name)
        self.file_label.setToolTip(str(path))
        self.status_label.setText(f"Editing {path.name}")
        self._visual_reset()
        self._refresh_visual_availability(show_errors=False)
        self._switch_mode(self._preferred_mode, force=True)
        self._update_action_state()

    def _drop_button_on_page(self, raw_path: str, payload: str) -> None:
        if self._current_target_path is None:
            return
        target_path = self._current_target_path / raw_path
        if target_path.name == "config.yaml" or target_path.suffix.lower() not in {".yaml", ".yml"}:
            QMessageBox.warning(self, "Paste Button", "Drop buttons only onto page YAML files.")
            return
        try:
            data = json.loads(payload)
        except Exception as exc:
            QMessageBox.warning(self, "Paste Button", f"Clipboard drag payload is invalid.\n\n{exc}")
            return
        if not isinstance(data, dict):
            QMessageBox.warning(self, "Paste Button", "Dragged button payload is invalid.")
            return

        if target_path == self._current_file_path:
            try:
                page_data = yaml.safe_load(self.editor.toPlainText()) or {}
            except Exception as exc:
                QMessageBox.warning(self, "Paste Button", f"Current target page YAML is invalid.\n\n{exc}")
                return
        else:
            try:
                page_data = yaml.safe_load(target_path.read_text(encoding="utf-8")) or {}
            except Exception as exc:
                QMessageBox.warning(self, "Paste Button", f"Could not open target page.\n\n{exc}")
                return
        if not isinstance(page_data, dict):
            QMessageBox.warning(self, "Paste Button", "Target page must parse to a YAML mapping.")
            return

        buttons = page_data.get("buttons") or []
        if not isinstance(buttons, list):
            QMessageBox.warning(self, "Paste Button", "Target page has an invalid `buttons:` section.")
            return

        cols, rows = self._infer_grid_dimensions(target_path, [btn for btn in buttons if isinstance(btn, dict)])
        total_slots = max(1, cols * rows)
        occupied = {
            idx
            for button in buttons
            if isinstance(button, dict) and (idx := self._button_index(button)) is not None
        }
        free_index = next((idx for idx in range(total_slots - 1, -1, -1) if idx not in occupied), None)
        if free_index is None:
            QMessageBox.warning(self, "Paste Button", f"No free slot is available on {target_path.name}.")
            return

        pasted = dict(data)
        pasted["index"] = free_index
        new_name = self._unique_button_name(self._button_name(pasted), buttons)
        if new_name:
            pasted["name"] = new_name
        else:
            pasted.pop("name", None)
        buttons.append(pasted)
        page_data["buttons"] = buttons
        dumped = yaml.safe_dump(page_data, sort_keys=False, allow_unicode=False)

        if target_path == self._current_file_path:
            self._set_editor_text(dumped)
            self._visual_reset()
            self._refresh_visual_availability(show_errors=False)
            self._switch_mode(self._preferred_mode, force=True)
        else:
            try:
                target_path.write_text(dumped, encoding="utf-8")
            except OSError as exc:
                QMessageBox.warning(self, "Paste Button", f"Could not write target page.\n\n{exc}")
                return
        self.status_label.setText(f"Copied button to {target_path.name} slot {free_index}.")

    def _warm_preview_pool_async(self, path: Path | None) -> None:
        if path is None:
            return
        key = str(path.resolve())
        if key in self._preview_warm_targets:
            return
        self._preview_warm_targets.add(key)

        def _worker(target_path: Path = path) -> None:
            error = warm_preview_pool(target_path)
            self.preview_warm_ready.emit(str(target_path.resolve()), error)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_preview_warm_ready(self, target_key: str, error: object) -> None:
        if not str(error or "").strip():
            self._preview_ready_targets.add(target_key)
        if self._current_target_path is None or str(self._current_target_path.resolve()) != target_key:
            return
        self._refresh_font_combos()
        if self._visual_enabled and self.stack.currentWidget() is self.visual_scroll:
            self._queue_visible_previews()

    def _refresh_font_combos(self) -> None:
        fonts = list_preview_fonts(self._current_target_path) if self._current_target_path else []
        for row in self.visual_ann_part_rows:
            combo = row["font_combo"]
            current = combo.currentText().strip()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("(default)", "")
            for f in fonts:
                combo.addItem(f, f)
            self._combo_set_data_or_text(combo, current)
            combo.blockSignals(False)

    def _reload_current_file(self) -> None:
        if self._current_file_path is None:
            return
        self._load_file(self._current_file_path)

    def _reveal_target(self) -> None:
        if self._current_target_path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._current_target_path)))

    def _reveal_file(self) -> None:
        if self._current_file_path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._current_file_path)))

    def _update_tree_dirty_state(self) -> None:
        current_path = str(self._current_file_path) if self._current_file_path is not None else None
        is_modified = self.editor.document().isModified()
        root = self.file_tree.invisibleRootItem()
        stack = [root.child(i) for i in range(root.childCount())]
        while stack:
            item = stack.pop()
            raw_path = item.data(0, Qt.ItemDataRole.UserRole)
            font = QFont(item.font(0))
            font.setBold(bool(raw_path and current_path and str(raw_path) == current_path and is_modified))
            item.setFont(0, font)
            for i in range(item.childCount()):
                stack.append(item.child(i))

    def _on_modification_changed(self, modified: bool) -> None:
        if self._loading_file:
            return
        if modified:
            self.modified_label.setText("Saving…")
            self._page_autosave_timer.start(1000)
        else:
            self.modified_label.setText("Saved")
            self._save_clear_timer.start(2000)
        self._update_tree_dirty_state()
        self._update_action_state()

    def _apply_theme(self) -> None:
        self._dark_mode = False
        panel_bg = "#ffffff"
        card_bg = "#ffffff"
        border = "#e2e8f0"
        fg = "#334155"
        subfg = "#64748b"
        editor_bg = "#f8fafc"
        editor_fg = "#0f172a"
        tree_bg = "#ffffff"
        tree_fg = "#0f172a"

        common_panel = f"QFrame {{ background: {card_bg}; border: 1px solid {border}; border-radius: 10px; }}"
        self._toolbar.setStyleSheet(common_panel)
        self._left_panel.setStyleSheet(common_panel)
        self._right_panel.setStyleSheet(common_panel)
        self.path_label.setStyleSheet(f"font-size: 11px; color: {subfg};")
        self.file_label.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {fg};")
        self.modified_label.setStyleSheet("font-size: 11px; color: #b45309; font-weight: 600;")
        self.status_label.setStyleSheet(f"font-size: 11px; color: {subfg};")
        self.visual_hint.setStyleSheet(f"font-size: 11px; color: {subfg};")
        self.config_form_hint.setStyleSheet(f"font-size: 11px; color: {subfg};")
        self.designer_title.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {fg};")
        self.selected_button_label.setStyleSheet(f"font-size: 11px; color: {fg};")
        self.slot_caps_label.setStyleSheet(f"font-size: 11px; color: {subfg};")
        self.slot_repr_label.setStyleSheet(f"font-size: 11px; color: {subfg};")
        self.preset_hint.setStyleSheet(f"font-size: 11px; color: {subfg};")
        self.file_tree.setStyleSheet(
            f"QTreeWidget {{ border: none; padding: 0 8px 8px 8px; font-size: 12px; color: {tree_fg}; background: {tree_bg}; }}"
            "QTreeWidget::item { padding: 4px 6px; }"
        )
        self.editor.setStyleSheet(
            f"QPlainTextEdit {{ font-family: Menlo, 'SF Mono', Monaco, Consolas, 'Courier New'; font-size: 12px;"
            f" border: 1px solid {border}; border-radius: 8px; background: {editor_bg}; color: {editor_fg}; }}"
        )
        self.preset_editor.setStyleSheet(self.editor.styleSheet())
        self.button_edit_editor.setStyleSheet(self.editor.styleSheet())
        button_style = (
            f"QPushButton {{ min-height: 30px; padding: 4px 10px; border-radius: 6px; border: 1px solid {border};"
            f" background: {panel_bg}; color: {fg}; font-size: 11px; }}"
            f"QPushButton:disabled {{ background: #e5e7eb; color: #94a3b8; border-color: {border}; }}"
            f"QPushButton:checked {{ background: #dbeafe; color: #1d4ed8; border-color: #93c5fd; }}"
        )
        for btn in (
            self.btn_refresh,
            self.btn_reveal_target,
            self.btn_text_view,
            self.btn_visual_view,
            self.btn_zoom_out,
            self.btn_zoom_fit,
            self.btn_zoom_in,
            self.btn_apply_preset,
            self.btn_reveal_file,
        ):
            btn.setStyleSheet(button_style)
        self.zoom_label.setStyleSheet(f"font-size: 11px; color: {subfg};")
        self._designer_panel.setStyleSheet(common_panel)
        self.config_form_section.setStyleSheet(common_panel)
        self.visual_activation_section.setStyleSheet(common_panel)
        self.visual_representation_section.setStyleSheet(common_panel)
        self.config_form_title.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {fg};")
        self.visual_activation_title.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {fg};")
        self.visual_representation_title.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {fg};")
        self.button_preview_status.setStyleSheet(f"font-size: 10px; color: {subfg};")

    def _switch_mode(self, mode: str, *, force: bool = False) -> None:
        if mode == "visual":
            if not self._refresh_visual_availability(show_errors=not force):
                self.btn_text_view.setChecked(True)
                self.stack.setCurrentWidget(self.editor)
                return
            self.btn_visual_view.setChecked(True)
            if self._config_form_enabled:
                self.stack.setCurrentWidget(self.config_form_scroll)
                self.status_label.setText("Visual mode: editing layout config fields.")
            else:
                self.stack.setCurrentWidget(self.visual_scroll)
                self.status_label.setText("Visual mode: drag buttons in the grid or double-click one to edit it.")
            self._preferred_mode = "visual"
            if not self._config_form_enabled:
                QTimer.singleShot(0, self._fit_visual_zoom)
        else:
            self.btn_text_view.setChecked(True)
            self.stack.setCurrentWidget(self.editor)
            self._preferred_mode = "text"
        self._update_action_state()

    def _clear_view_mode_checks(self) -> None:
        self._view_group.setExclusive(False)
        self.btn_text_view.setChecked(False)
        self.btn_visual_view.setChecked(False)
        self._view_group.setExclusive(True)

    def _refresh_visual_availability(self, *, show_errors: bool) -> bool:
        if self._current_file_path is None or self._current_file_path.suffix.lower() not in {".yaml", ".yml"}:
            self._visual_reset()
            self.btn_visual_view.setEnabled(False)
            return False
        try:
            data = yaml.safe_load(self.editor.toPlainText()) or {}
        except Exception as exc:
            self._visual_reset()
            self.btn_visual_view.setEnabled(False)
            if show_errors:
                QMessageBox.warning(self, "Visual mode unavailable", f"YAML parse failed:\n{exc}")
            return False
        if self._is_layout_config_file(self._current_file_path):
            if not isinstance(data, dict):
                self._visual_reset()
                self.btn_visual_view.setEnabled(False)
                if show_errors:
                    QMessageBox.information(self, "Visual mode unavailable", "This layout config does not parse to a YAML mapping.")
                return False
            self._config_form_enabled = True
            self._config_yaml_data = data
            self._visual_enabled = True
            self.btn_visual_view.setEnabled(True)
            self._load_config_form_from_data(data)
            return True
        buttons = data.get("buttons")
        if not isinstance(data, dict) or not isinstance(buttons, list):
            self._visual_reset()
            self.btn_visual_view.setEnabled(False)
            if show_errors:
                QMessageBox.information(self, "Visual mode unavailable", "This file does not look like a Cockpitdecks page with a `buttons:` list.")
            return False

        self._visual_yaml_data = data
        self._config_form_enabled = False
        self._config_yaml_data = None
        self._visual_buttons = {}
        self._visual_button_order = []
        self._selected_button_id = None
        self._visual_deck_name = self._resolve_visual_deck_name(self._current_file_path)
        self._preview_generation += 1
        self._preview_cache = {}
        self._preview_errors = {}
        self._preview_inflight = set()
        self._preview_queue = []
        self._preview_queue_keys = set()
        self._effective_page_attrs_cache = {}
        for idx, button in enumerate(buttons):
            if not isinstance(button, dict):
                continue
            button_id = f"btn-{idx}"
            self._visual_buttons[button_id] = button
            self._visual_button_order.append(button_id)
        self._visual_cols, self._visual_rows = self._infer_grid_dimensions(self._current_file_path, buttons)
        self._visual_enabled = True
        self.btn_visual_view.setEnabled(True)
        self._refresh_selected_button_panel()
        self._rebuild_visual_widgets()
        return True

    def _visual_reset(self) -> None:
        self._visual_enabled = False
        self._visual_yaml_data = None
        self._config_form_enabled = False
        self._config_yaml_data = None
        self._visual_buttons = {}
        self._visual_button_order = []
        self._visual_cols = 0
        self._visual_rows = 0
        self._selected_button_id = None
        self._visual_deck_name = None
        self._preview_generation += 1
        self._preview_cache = {}
        self._preview_errors = {}
        self._preview_inflight = set()
        self._preview_queue = []
        self._preview_queue_keys = set()
        self._effective_page_attrs_cache = {}
        self.btn_visual_view.setEnabled(False)
        self._refresh_selected_button_panel()
        self._rebuild_visual_widgets()

    def _resolve_visual_deck_name(self, page_path: Path | None) -> str | None:
        if page_path is None or self._current_target_path is None:
            return None
        layout_id = page_path.parent.name
        config_path = self._current_target_path / "deckconfig" / "config.yaml"
        try:
            cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except Exception:
            return None
        for deck in cfg.get("decks", []):
            if not isinstance(deck, dict):
                continue
            if str(deck.get("layout") or "").strip() == layout_id:
                name = str(deck.get("name") or "").strip()
                if name:
                    return name
        return None

    def _is_layout_config_file(self, path: Path | None) -> bool:
        if path is None or path.name != "config.yaml":
            return False
        parent = path.parent.name
        return parent not in {"deckconfig", ""}

    def _combo_set_data_or_text(self, combo: QComboBox, value: str) -> None:
        idx = combo.findData(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)
            return
        if combo.isEditable():
            combo.setEditText(value)
            return
        combo.setCurrentIndex(0)

    def _load_config_form_from_data(self, data: dict) -> None:
        self._config_form_loading = True
        try:
            self.config_home_page_edit.setText(str(data.get("home-page-name") or ""))
            self.config_label_font_edit.setText(str(data.get("default-label-font") or ""))
            self.config_label_size.setValue(int(data.get("default-label-size") or 0))
            self.config_text_size.setValue(int(data.get("default-text-size") or 0))
            self.config_label_color_edit.setText(str(data.get("default-label-color") or ""))
            self._combo_set_data_or_text(self.config_label_position, str(data.get("default-label-position") or ""))
            self._combo_set_data_or_text(self.config_vibrate, str(data.get("default-vibrate") or ""))
            self.config_icon_color_edit.setText(str(data.get("default-icon-color") or ""))
            self._combo_set_data_or_text(self.config_ann_style, str(data.get("default-annunciator-style") or ""))
            self.config_light_off_intensity.setValue(int(data.get("default-light-off-intensity") or 0))
            self.config_fill_empty_keys.setChecked(bool(data.get("fill-empty-keys", False)))
        finally:
            self._config_form_loading = False

    def _apply_config_fields_to_editor(self) -> None:
        if self._config_form_loading or self._config_yaml_data is None:
            return
        data = dict(self._config_yaml_data)

        def _set_or_pop(key: str, value) -> None:
            empty = value == "" or value == 0 or value is None
            if empty:
                data.pop(key, None)
            else:
                data[key] = value

        _set_or_pop("home-page-name", self.config_home_page_edit.text().strip())
        _set_or_pop("default-label-font", self.config_label_font_edit.text().strip())
        _set_or_pop("default-label-size", self.config_label_size.value())
        _set_or_pop("default-text-size", self.config_text_size.value())
        _set_or_pop("default-label-color", self.config_label_color_edit.text().strip())
        _set_or_pop("default-label-position", (self.config_label_position.currentData() or self.config_label_position.currentText()).strip())
        _set_or_pop("default-vibrate", (self.config_vibrate.currentData() or self.config_vibrate.currentText()).strip())
        _set_or_pop("default-icon-color", self.config_icon_color_edit.text().strip())
        _set_or_pop("default-annunciator-style", str(self.config_ann_style.currentData() or "").strip())
        _set_or_pop("default-light-off-intensity", self.config_light_off_intensity.value())
        if self.config_fill_empty_keys.isChecked():
            data["fill-empty-keys"] = True
        else:
            data.pop("fill-empty-keys", None)

        self._config_yaml_data = data
        dumped = yaml.safe_dump(data, sort_keys=False, allow_unicode=False)
        self._set_editor_text(dumped, mark_modified=True)
        self.status_label.setText("Updated layout config fields.")
        self._update_action_state()

    def _button_index(self, button: dict) -> int | None:
        value = button.get("index")
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError:
                return None
        return None

    def _button_name(self, button: dict) -> str:
        if not isinstance(button, dict):
            return ""
        return str(button.get("name") or "").strip()

    def _unique_button_name(self, base_name: str, buttons: list[dict], *, exclude_index: int | None = None) -> str:
        base = str(base_name or "").strip()
        if not base:
            return base
        existing = {
            name
            for button in buttons
            if isinstance(button, dict)
            and (name := self._button_name(button))
            and (exclude_index is None or self._button_index(button) != exclude_index)
        }
        if base not in existing:
            return base
        suffix = 2
        while f"{base}-{suffix}" in existing:
            suffix += 1
        return f"{base}-{suffix}"

    def _button_id_at_index(self, target_index: int) -> str | None:
        for button_id, button in self._visual_buttons.items():
            if self._button_index(button) == target_index:
                return button_id
        return None

    def _effective_page_attributes(self) -> dict:
        if self._current_file_path is None:
            return {}
        context: dict = {}
        target_root = self._current_target_path
        layout_dir = self._current_file_path.parent

        def _merge_from_file(path: Path) -> None:
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except Exception:
                return
            if not isinstance(data, dict):
                return
            for key, value in data.items():
                if key in {"buttons", "includes"}:
                    continue
                context[key] = value

        if target_root is not None:
            layout_cfg = target_root / "deckconfig" / layout_dir.name / "config.yaml"
            if layout_cfg.is_file():
                _merge_from_file(layout_cfg)

        try:
            page_data = yaml.safe_load(self.editor.toPlainText()) or {}
        except Exception:
            page_data = {}
        if isinstance(page_data, dict):
            for key, value in page_data.items():
                if key in {"buttons", "includes"}:
                    continue
                context[key] = value
            includes = page_data.get("includes")
            if isinstance(includes, str):
                include_names = [part.strip() for part in includes.split(",") if part.strip()]
            elif isinstance(includes, list):
                include_names = [str(part).strip() for part in includes if str(part).strip()]
            else:
                include_names = []
            for name in include_names:
                inc_path = layout_dir / f"{name}.yaml"
                if inc_path.is_file():
                    _merge_from_file(inc_path)
        self._effective_page_attrs_cache = context
        return context

    def _button_preview_config(self, button_id: str) -> dict:
        button = dict(self._visual_buttons.get(button_id, {}))
        effective_attrs = self._effective_page_attrs_cache or self._effective_page_attributes()
        for key, value in effective_attrs.items():
            button.setdefault(key, value)
        return button

    def _presets(self) -> dict[str, dict]:
        return {
            "push_annunciator": {
                "label": "Push Annunciator",
                "hint": "Best for toggles, AP modes, lights, pumps, and other stateful controls.",
                "config": {
                    "activation": "push",
                    "commands": {"press": "sim/none/command"},
                    "annunciator": {
                        "size": "medium",
                        "model": "B",
                        "parts": {
                            "B0": {"color": "lime", "led": "bars", "formula": "0"},
                            "B1": {"text": "LABEL", "text-size": 44, "formula": "1"},
                        },
                    },
                },
            },
            "page_nav": {
                "label": "Page Navigation",
                "hint": "Use this for page changes or returning to a home/index page.",
                "config": {"activation": "page", "label": "PAGE", "label-size": 12, "page": "index"},
            },
            "status_tile": {
                "label": "Status Tile",
                "hint": "Read-only value tile for things like IAS, ALT, BARO, fuel, or temps.",
                "config": {"activation": "none", "label": "STATUS", "label-size": 12, "text": "VALUE", "text-size": 24},
            },
        }

    def _format_capability_summary(self, values: list[str], *, label: str) -> str:
        cleaned = [str(v).strip() for v in values if str(v).strip()]
        if not cleaned:
            return f"{label}: none"
        if len(cleaned) <= 4:
            return f"{label}: {', '.join(cleaned)}"
        return f"{label}: {', '.join(cleaned[:4])} +{len(cleaned) - 4} more"

    def _populate_presets(self) -> None:
        self.preset_combo.clear()
        for key, preset in self._presets().items():
            self.preset_combo.addItem(preset["label"], key)
        self._update_preset_preview()

    def _selected_preset_key(self) -> str | None:
        idx = self.preset_combo.currentIndex()
        return self.preset_combo.itemData(idx) if idx >= 0 else None

    def _set_visual_combo_value(self, combo: QComboBox, value: str) -> None:
        idx = combo.findData(value)
        if idx < 0:
            idx = combo.findText(value)
        combo.setCurrentIndex(max(0, idx))

    def _activation_family_for_type(self, activation_type: str) -> str:
        for family, items in _ACTIVATION_SCHEMA.items():
            if any(name == activation_type for name, _label in items):
                return family
        return "Push Button"

    def _representation_family_for_style(self, style: str) -> str:
        for family, items in _REPRESENTATION_SCHEMA.items():
            if any(name == style for name, _label in items):
                return family
        return "Basic"

    def _populate_activation_subtypes(self, selected: str | None = None) -> None:
        family = str(self.visual_activation_family_combo.currentData() or self.visual_activation_family_combo.currentText() or "Push Button")
        self.visual_type_combo.blockSignals(True)
        self.visual_type_combo.clear()
        for name, label in _ACTIVATION_SCHEMA.get(family, []):
            self.visual_type_combo.addItem(label, name)
        self.visual_type_combo.blockSignals(False)
        self._set_visual_combo_value(self.visual_type_combo, selected or _ACTIVATION_SCHEMA.get(family, [("push", "")])[0][0])

    def _populate_representation_subtypes(self, selected: str | None = None) -> None:
        family = str(self.visual_representation_family_combo.currentData() or self.visual_representation_family_combo.currentText() or "Basic")
        self.visual_style_combo.blockSignals(True)
        self.visual_style_combo.clear()
        for name, label in _REPRESENTATION_SCHEMA.get(family, []):
            self.visual_style_combo.addItem(label, name)
        self.visual_style_combo.blockSignals(False)
        self._set_visual_combo_value(self.visual_style_combo, selected or _REPRESENTATION_SCHEMA.get(family, [("standard", "")])[0][0])

    def _update_annunciator_part_rows(self) -> None:
        model = str(self.visual_ann_model.currentData() or self.visual_ann_model.currentText() or "B")
        parts = _ANNUNCIATOR_PART_IDS.get(model, ["B0", "B1"])
        for idx, row in enumerate(self.visual_ann_part_rows):
            label = row["label"]
            host = row["host"]
            visible = idx < len(parts)
            label.setVisible(visible)
            host.setVisible(visible)
            if visible:
                label.setText(parts[idx])

    def _update_advanced_preview(self) -> None:
        data = dict(self._button_doc.current_data or {})
        preserved = dict(data)
        for key in [
            "activation",
            "commands",
            "page",
            "deck",
            "label",
            "label-size",
            "label-color",
            "text",
            "text-size",
            "text-color",
            "formula",
        ]:
            preserved.pop(key, None)
        if preserved.get("annunciator") and str(self.visual_style_combo.currentData() or "") == "annunciator":
            ann = dict(preserved.get("annunciator") or {})
            ann.pop("model", None)
            ann.pop("annunciator-style", None)
            ann.pop("size", None)
            ann.pop("parts", None)
            if ann:
                preserved["annunciator"] = ann
            else:
                preserved.pop("annunciator", None)
        if preserved.get("gauge") and str(self.visual_style_combo.currentData() or "") == "gauge":
            gauge = dict(preserved.get("gauge") or {})
            for _k in ("tick-from", "tick-to", "ticks", "gauge-offset", "needle-color", "needle-width",
                       "needle-length", "tick-color", "tick-width", "tick-label-size", "tick-labels"):
                gauge.pop(_k, None)
            if gauge:
                preserved["gauge"] = gauge
            else:
                preserved.pop("gauge", None)
        self.button_advanced_preview.setPlainText(
            yaml.safe_dump(preserved or {"info": "No preserved advanced fields"}, sort_keys=False, allow_unicode=False)
        )

    def _update_visual_field_visibility(self) -> None:
        action_type = str(self.visual_type_combo.currentData() or self.visual_type_combo.currentText() or "")
        style = str(self.visual_style_combo.currentData() or self.visual_style_combo.currentText() or "standard")

        is_page = action_type == "page"
        is_page_cycle = action_type == "page-cycle"
        is_command_like = action_type in {"push", "begin-end-command"}
        is_two_command = action_type in {"encoder-toggle", "short-or-long-press"}
        uses_remote_deck = action_type in {"page", "reload"}
        is_annunciator = style == "annunciator"
        is_gauge = style == "gauge"

        _set_form_row_visible(self.visual_activation_form, self.visual_command_row, is_command_like)
        _set_form_row_visible(self.visual_activation_form, self.visual_command_pair_host, is_two_command)
        _set_form_row_visible(self.visual_activation_form, self.visual_page_row, is_page)
        _set_form_row_visible(self.visual_activation_form, self.visual_pages_row, is_page_cycle)
        _set_form_row_visible(self.visual_activation_form, self.visual_deck_row, uses_remote_deck)

        if action_type == "encoder-toggle":
            self.visual_command1_label.setText("On")
            self.visual_command2_label.setText("Off")
            self.visual_command1_edit.setPlaceholderText("Command when turning on")
            self.visual_command2_edit.setPlaceholderText("Command when turning off")
        elif action_type == "short-or-long-press":
            self.visual_command1_label.setText("Short")
            self.visual_command2_label.setText("Long")
            self.visual_command1_edit.setPlaceholderText("Command for short press")
            self.visual_command2_edit.setPlaceholderText("Command for long press")
        else:
            self.visual_command1_label.setText("Primary")
            self.visual_command2_label.setText("Secondary")
            self.visual_command1_edit.setPlaceholderText("sim/...")
            self.visual_command2_edit.setPlaceholderText("sim/...")

        if action_type == "page":
            self.visual_command_edit.setPlaceholderText("")
            self.visual_page_edit.setPlaceholderText("index")
        elif action_type == "page-cycle":
            self.visual_command_edit.setPlaceholderText("")
            self.visual_pages_edit.setPlaceholderText("index, page2")
        else:
            self.visual_command_edit.setPlaceholderText("sim/...")
            self.visual_page_edit.setPlaceholderText("")
            self.visual_pages_edit.setPlaceholderText("")

        show_basic_text = not is_annunciator and not is_gauge
        _set_form_row_visible(self.visual_representation_form, self.visual_label_color_row, is_gauge)
        _set_form_row_visible(self.visual_representation_form, self.visual_text_row, show_basic_text)
        _set_form_row_visible(self.visual_representation_form, self.visual_text_size_row, show_basic_text)
        _set_form_row_visible(self.visual_representation_form, self.visual_text_color_row, show_basic_text)
        _set_form_row_visible(self.visual_representation_form, self.visual_ann_model_row, is_annunciator)
        _set_form_row_visible(self.visual_representation_form, self.visual_ann_style_row, is_annunciator)
        _set_form_row_visible(self.visual_representation_form, self.visual_ann_size_row, is_annunciator)
        _set_form_row_visible(self.visual_representation_form, self.visual_ann_parts_row, is_annunciator)
        _set_form_row_visible(self.visual_representation_form, self.visual_gauge_tick_range_row, is_gauge)
        _set_form_row_visible(self.visual_representation_form, self.visual_gauge_needle_row, is_gauge)
        _set_form_row_visible(self.visual_representation_form, self.visual_gauge_ticks_style_row, is_gauge)
        _set_form_row_visible(self.visual_representation_form, self.visual_gauge_formula_row, is_gauge)
        _set_form_row_visible(self.visual_representation_form, self.visual_gauge_tick_labels_row, is_gauge)
        self._update_annunciator_part_rows()

    def _sync_visual_fields_from_doc(self) -> None:
        data = dict(self._button_doc.current_data or {})
        self._button_visual_syncing = True
        try:
            action_type = str(data.get("activation") or "push")
            family = self._activation_family_for_type(action_type)
            self._set_visual_combo_value(self.visual_activation_family_combo, family)
            self._populate_activation_subtypes(action_type)
            self._set_visual_combo_value(self.visual_type_combo, action_type)

            ann = data.get("annunciator")
            gauge = data.get("gauge")
            if isinstance(ann, dict):
                style = "annunciator"
            elif isinstance(gauge, dict):
                style = "gauge"
            else:
                style = "standard"
            rep_family = self._representation_family_for_style(style)
            self._set_visual_combo_value(self.visual_representation_family_combo, rep_family)
            self._populate_representation_subtypes(style)
            self._set_visual_combo_value(self.visual_style_combo, style)

            cmds = data.get("commands") or {}
            self.visual_command_edit.setText(str(cmds.get("press") or ""))
            command1 = ""
            command2 = ""
            pair_fields = _two_command_fields(action_type)
            if pair_fields is not None:
                command1 = str(cmds.get(pair_fields[0]) or "")
                command2 = str(cmds.get(pair_fields[1]) or "")
            self.visual_command1_edit.setText(command1)
            self.visual_command2_edit.setText(command2)
            self.visual_page_edit.setText(str(data.get("page") or ""))
            pages = data.get("pages") if isinstance(data.get("pages"), list) else []
            self.visual_pages_edit.setText(", ".join(str(page).strip() for page in pages if str(page).strip()))
            self.visual_deck_edit.setText(str(data.get("deck") or ""))
            self.visual_label_edit.setText(str(data.get("label") or ""))
            self.visual_label_size.setValue(int(data.get("label-size") or 0))
            self.visual_label_color_edit.setText(str(data.get("label-color") or ""))
            self.visual_text_edit.setText(str(data.get("text") or ""))
            self.visual_text_size.setValue(int(data.get("text-size") or 0))
            self.visual_text_color_edit.setText(str(data.get("text-color") or ""))

            ann_model = "B"
            part_texts: dict[str, str] = {}
            part_fonts: dict[str, str] = {}
            part_sizes: dict[str, int] = {}
            part_colors: dict[str, str] = {}
            part_formulas: dict[str, str] = {}
            part_leds: dict[str, str] = {}
            if isinstance(ann, dict):
                ann_model = str(ann.get("model") or "B")
                ann_style = str(ann.get("annunciator-style") or "")
                ann_size = str(ann.get("size") or "medium")
                parts_raw = ann.get("parts") or []
                part_ids_for_model = _ANNUNCIATOR_PART_IDS.get(ann_model, [])
                if isinstance(parts_raw, list):
                    parts = {part_ids_for_model[i]: parts_raw[i] for i in range(min(len(parts_raw), len(part_ids_for_model))) if isinstance(parts_raw[i], dict)}
                elif isinstance(parts_raw, dict):
                    parts = parts_raw
                else:
                    parts = {}
                for part_id in part_ids_for_model:
                    part_cfg = parts.get(part_id) or {}
                    part_texts[part_id] = str(part_cfg.get("text") or "")
                    part_fonts[part_id] = str(part_cfg.get("text-font") or "")
                    part_sizes[part_id] = int(part_cfg.get("text-size") or 0)
                    part_colors[part_id] = str(part_cfg.get("color") or "")
                    part_formulas[part_id] = str(part_cfg.get("formula") or "")
                    part_leds[part_id] = str(part_cfg.get("led") or "")
            else:
                ann_style = ""
                ann_size = "medium"
            self._set_visual_combo_value(self.visual_ann_model, ann_model)
            self._set_visual_combo_value(self.visual_ann_style, ann_style)
            self._set_visual_combo_value(self.visual_ann_size, ann_size)
            self._update_annunciator_part_rows()
            for idx, row in enumerate(self.visual_ann_part_rows):
                part_ids = _ANNUNCIATOR_PART_IDS.get(ann_model, [])
                part_id = part_ids[idx] if idx < len(part_ids) else ""
                row["text_edit"].setText(part_texts.get(part_id, ""))
                self._combo_set_data_or_text(row["font_combo"], part_fonts.get(part_id, ""))
                row["text_size"].setValue(part_sizes.get(part_id, 0))
                row["color_edit"].setText(part_colors.get(part_id, ""))
                row["formula_edit"].setText(part_formulas.get(part_id, ""))
                self._set_visual_combo_value(row["led_combo"], part_leds.get(part_id, ""))

            # ── Gauge sync ────────────────────────────────────────────────────
            g = gauge if isinstance(gauge, dict) else {}
            self.visual_gauge_tick_from.setValue(int(g.get("tick-from") or -120))
            self.visual_gauge_tick_to.setValue(int(g.get("tick-to") or 120))
            self.visual_gauge_ticks.setValue(int(g.get("ticks") or 9))
            self.visual_gauge_offset.setValue(int(g.get("gauge-offset") or 20))
            self.visual_gauge_needle_color.setText(str(g.get("needle-color") or ""))
            self.visual_gauge_needle_width.setValue(int(g.get("needle-width") or 0))
            self.visual_gauge_needle_length.setValue(int(g.get("needle-length") or 0))
            self.visual_gauge_tick_color.setText(str(g.get("tick-color") or ""))
            self.visual_gauge_tick_width.setValue(int(g.get("tick-width") or 0))
            self.visual_gauge_tick_label_size.setValue(int(g.get("tick-label-size") or 0))
            self.visual_gauge_formula_edit.setText(str(data.get("formula") or ""))
            tick_labels = g.get("tick-labels")
            if isinstance(tick_labels, list):
                self.visual_gauge_tick_labels.setPlainText("\n".join(str(t) for t in tick_labels))
            else:
                self.visual_gauge_tick_labels.setPlainText("")

            # ── Span sync ─────────────────────────────────────────────────────
            span = data.get("span")
            if isinstance(span, (list, tuple)) and len(span) == 2:
                self.visual_span_cols.setValue(max(1, int(span[0])))
                self.visual_span_rows.setValue(max(1, int(span[1])))
            else:
                self.visual_span_cols.setValue(1)
                self.visual_span_rows.setValue(1)
        finally:
            self._button_visual_syncing = False
        self._update_visual_field_visibility()
        self._update_advanced_preview()

    def _apply_visual_fields_to_yaml(self, *_args) -> None:
        if self._button_visual_syncing:
            return
        sender = self.sender()
        previous_type = str((self._button_doc.current_data or {}).get("activation") or "push")
        if sender is self.visual_activation_family_combo:
            self._populate_activation_subtypes()
        elif sender is self.visual_representation_family_combo:
            self._populate_representation_subtypes()
        self._update_visual_field_visibility()
        data = dict(self._button_doc.current_data or {})

        def _set_or_del(key: str, value: str | int) -> None:
            if isinstance(value, int):
                if value > 0:
                    data[key] = value
                else:
                    data.pop(key, None)
                return
            if str(value).strip():
                data[key] = str(value).strip()
            else:
                data.pop(key, None)

        action_type = str(self.visual_type_combo.currentData() or self.visual_type_combo.currentText())
        _set_or_del("activation", action_type)
        _set_or_del("page", self.visual_page_edit.text())
        _set_or_del("deck", self.visual_deck_edit.text())
        _set_or_del("label", self.visual_label_edit.text())
        _set_or_del("label-size", self.visual_label_size.value())
        _set_or_del("text", self.visual_text_edit.text())
        _set_or_del("text-size", self.visual_text_size.value())
        _set_or_del("text-color", self.visual_text_color_edit.text())

        if action_type != previous_type:
            pair_fields = _two_command_fields(action_type)
            if action_type == "encoder-toggle" and pair_fields is not None:
                label = str(data.get("label") or data.get("text") or data.get("name") or "BUTTON").strip().upper()
                cmds = dict(data.get("commands") or {})
                cmds.setdefault(pair_fields[0], f"sim/none/{label.lower()}_on")
                cmds.setdefault(pair_fields[1], f"sim/none/{label.lower()}_off")
                data["commands"] = cmds
            elif action_type == "short-or-long-press" and pair_fields is not None:
                label = str(data.get("label") or data.get("text") or data.get("name") or "BUTTON").strip().upper()
                cmds = dict(data.get("commands") or {})
                cmds.setdefault(pair_fields[0], f"sim/none/{label.lower()}_short")
                cmds.setdefault(pair_fields[1], f"sim/none/{label.lower()}_long")
                data["commands"] = cmds
            elif action_type == "page" and not str(data.get("page") or "").strip():
                data["page"] = "index"
            elif action_type == "page-cycle" and not isinstance(data.get("pages"), list):
                data["pages"] = ["index", "page2"]

        # Write commands dict — build from current UI state
        cmds = dict(data.get("commands") or {})
        pair_fields = _two_command_fields(action_type)
        if pair_fields is not None:
            command1 = self.visual_command1_edit.text().strip()
            command2 = self.visual_command2_edit.text().strip()
            if command1:
                cmds[pair_fields[0]] = command1
            else:
                cmds.pop(pair_fields[0], None)
            if command2:
                cmds[pair_fields[1]] = command2
            else:
                cmds.pop(pair_fields[1], None)
            cmds.pop("press", None)
        else:
            press_cmd = self.visual_command_edit.text().strip()
            if press_cmd:
                cmds["press"] = press_cmd
            else:
                cmds.pop("press", None)
        if cmds:
            data["commands"] = cmds
        else:
            data.pop("commands", None)

        if action_type == "page-cycle":
            pages = [part.strip() for part in self.visual_pages_edit.text().split(",") if part.strip()]
            if pages:
                data["pages"] = pages
            else:
                data.pop("pages", None)
        else:
            data.pop("pages", None)

        style = str(self.visual_style_combo.currentData() or "standard")
        if style == "gauge":
            _set_or_del("label-color", self.visual_label_color_edit.text())
        else:
            data.pop("label-color", None)
        if style == "annunciator":
            data.pop("text", None)
            data.pop("text-size", None)
            data.pop("text-color", None)
            ann = dict(data.get("annunciator") or {})
            ann["model"] = str(self.visual_ann_model.currentData() or self.visual_ann_model.currentText() or "B")
            ann_style = str(self.visual_ann_style.currentData() or "").strip()
            if ann_style:
                ann["annunciator-style"] = ann_style
            else:
                ann.pop("annunciator-style", None)
            ann["size"] = str(self.visual_ann_size.currentData() or "medium")
            model = ann["model"]
            wanted_parts = _ANNUNCIATOR_PART_IDS.get(model, ["B0", "B1"])
            raw_parts_input = ann.get("parts") or []
            if isinstance(raw_parts_input, list):
                parts = {wanted_parts[i]: dict(raw_parts_input[i]) for i in range(min(len(raw_parts_input), len(wanted_parts))) if isinstance(raw_parts_input[i], dict)}
            elif isinstance(raw_parts_input, dict):
                parts = {k: dict(v) for k, v in raw_parts_input.items() if k in wanted_parts and isinstance(v, dict)}
            else:
                parts = {}
            for idx, row in enumerate(self.visual_ann_part_rows):
                if idx >= len(wanted_parts):
                    continue
                part_id = wanted_parts[idx]
                text = row["text_edit"].text().strip()
                font = row["font_combo"].currentText().strip()
                text_size = row["text_size"].value()
                color = row["color_edit"].text().strip()
                formula = row["formula_edit"].text().strip()
                led = str(row["led_combo"].currentData() or "").strip()
                part_cfg = dict(parts.get(part_id) or {})
                if text:
                    part_cfg["text"] = text
                    if not color:
                        part_cfg.setdefault("color", "lime" if idx else "orange")
                else:
                    part_cfg.pop("text", None)
                if font and font != "(default)":
                    part_cfg["text-font"] = font
                else:
                    part_cfg.pop("text-font", None)
                if text_size > 0:
                    part_cfg["text-size"] = text_size
                else:
                    part_cfg.pop("text-size", None)
                if color:
                    part_cfg["color"] = color
                else:
                    part_cfg.pop("color", None)
                if formula:
                    part_cfg["formula"] = formula
                else:
                    part_cfg.pop("formula", None)
                if led:
                    part_cfg["led"] = led
                else:
                    part_cfg.pop("led", None)
                if part_cfg:
                    parts[part_id] = part_cfg
                elif part_id in parts:
                    parts.pop(part_id, None)
            # Write parts as an ordered list (positional, matching wanted_parts order)
            parts_list = [parts[pid] for pid in wanted_parts if pid in parts and parts[pid]]
            if parts_list:
                ann["parts"] = parts_list
            else:
                ann.pop("parts", None)
            data["annunciator"] = ann
        else:
            data.pop("annunciator", None)

        if style == "gauge":
            data.pop("text", None)
            data.pop("text-size", None)
            data.pop("text-color", None)
            data.pop("annunciator", None)

            def _int_or_del(d: dict, key: str, val: int, default: int) -> None:
                if val != default:
                    d[key] = val
                else:
                    d.pop(key, None)

            def _str_or_del(d: dict, key: str, val: str) -> None:
                if val.strip():
                    d[key] = val.strip()
                else:
                    d.pop(key, None)

            g = dict(data.get("gauge") or {})
            g["tick-from"] = self.visual_gauge_tick_from.value()
            g["tick-to"] = self.visual_gauge_tick_to.value()
            g["ticks"] = self.visual_gauge_ticks.value()
            if self.visual_gauge_offset.value() != 0:
                g["gauge-offset"] = self.visual_gauge_offset.value()
            else:
                g.pop("gauge-offset", None)
            _str_or_del(g, "needle-color", self.visual_gauge_needle_color.text())
            needle_width = self.visual_gauge_needle_width.value()
            if needle_width > 0:
                g["needle-width"] = needle_width
            else:
                g.pop("needle-width", None)
            needle_length = self.visual_gauge_needle_length.value()
            if needle_length > 0:
                g["needle-length"] = needle_length
            else:
                g.pop("needle-length", None)
            _str_or_del(g, "tick-color", self.visual_gauge_tick_color.text())
            tick_width = self.visual_gauge_tick_width.value()
            if tick_width > 0:
                g["tick-width"] = tick_width
            else:
                g.pop("tick-width", None)
            tick_label_size = self.visual_gauge_tick_label_size.value()
            if tick_label_size > 0:
                g["tick-label-size"] = tick_label_size
            else:
                g.pop("tick-label-size", None)
            raw_labels = self.visual_gauge_tick_labels.toPlainText().strip()
            if raw_labels:
                g["tick-labels"] = [line.strip() for line in raw_labels.splitlines() if line.strip()]
            else:
                g.pop("tick-labels", None)
            if g:
                data["gauge"] = g
            else:
                data.pop("gauge", None)
            formula = self.visual_gauge_formula_edit.text().strip()
            if formula:
                data["formula"] = formula
            else:
                data.pop("formula", None)
        else:
            data.pop("gauge", None)

        sc = self.visual_span_cols.value()
        sr = self.visual_span_rows.value()
        if sc > 1 or sr > 1:
            data["span"] = [sc, sr]
        else:
            data.pop("span", None)

        self._button_doc.set_current_data(data)
        self._loading_file = True
        try:
            self.button_edit_editor.setPlainText(self._button_doc.to_yaml())
        finally:
            self._loading_file = False
        self._schedule_button_edit_preview()
        self._update_advanced_preview()
        self._button_autosave_timer.start(600)
        self._update_action_state()

    def _on_button_yaml_text_changed(self) -> None:
        if self._loading_file:
            return
        ok, _err = self._button_doc.update_from_yaml_text(self.button_edit_editor.toPlainText())
        if ok:
            self._sync_visual_fields_from_doc()
        self._schedule_button_edit_preview()
        self._button_autosave_timer.start(600)
        self._update_action_state()

    def _update_preset_preview(self) -> None:
        key = self._selected_preset_key()
        preset = self._presets().get(str(key)) if key is not None else None
        if not preset:
            self.preset_hint.setText("")
            self.preset_editor.setPlainText("")
            return
        self.preset_hint.setText(preset["hint"])
        self.preset_editor.setPlainText(yaml.safe_dump(preset["config"], sort_keys=False, allow_unicode=False))

    def _slot_capabilities(self, button_id: str) -> dict:
        if self._current_target_path is None or not self._visual_deck_name:
            return {}
        button = self._visual_buttons.get(button_id, {})
        index = self._button_index(button)
        if index is None:
            return {}
        info, err = describe_slot_native(self._current_target_path, self._visual_deck_name, index)
        if err is not None:
            return {"error": err}
        return info or {}

    def _refresh_selected_button_panel(self) -> None:
        visual_active = self.stack.currentWidget() is self.visual_scroll
        self._designer_panel.setVisible(False)
        if not visual_active:
            return
        if self._selected_button_id is None or self._selected_button_id not in self._visual_buttons:
            self.selected_button_label.setText("Select a button in Visual mode.")
            self.slot_caps_label.setText("")
            self.slot_repr_label.setText("")
            return
        button = self._visual_buttons[self._selected_button_id]
        idx = self._button_index(button)
        name = str(button.get("name") or button.get("label") or button.get("text") or self._selected_button_id)
        self.selected_button_label.setText(f"Selected: {name} at slot {idx if idx is not None else '—'}")
        self._selected_slot_info = self._slot_capabilities(self._selected_button_id)
        if self._selected_slot_info.get("error"):
            self.slot_caps_label.setText(f"Capabilities unavailable: {self._selected_slot_info['error']}")
            self.slot_repr_label.setText("")
            return
        activations = ", ".join(self._selected_slot_info.get("activations", [])[:8])
        representations = ", ".join(self._selected_slot_info.get("representations", [])[:8])
        self.slot_caps_label.setText(f"Activations: {activations or '—'}")
        self.slot_repr_label.setText(f"Representations: {representations or '—'}")

    def _apply_selected_preset(self) -> None:
        if self._selected_button_id is None or self._selected_button_id not in self._visual_buttons:
            QMessageBox.information(self, "No button selected", "Click a button in Visual mode first.")
            return
        key = self._selected_preset_key()
        preset = self._presets().get(str(key)) if key is not None else None
        if not preset:
            return
        current = dict(self._visual_buttons[self._selected_button_id])
        current_name = str(current.get("name") or "").strip()
        base = {"index": current.get("index")}
        if current_name:
            base["name"] = current_name
        merged = base | dict(preset["config"])
        if "label" not in merged and current.get("label"):
            merged["label"] = current.get("label")
        ok = self._apply_button_yaml(self._selected_button_id, yaml.safe_dump(merged, sort_keys=False, allow_unicode=False))
        if ok:
            self.status_label.setText(f"Applied preset '{preset['label']}'.")

    def _schedule_button_edit_preview(self) -> None:
        if self._button_edit_id is None:
            self.button_preview_label.clear()
            self.button_preview_status.setText("Preview will appear here.")
            return
        text = self.button_edit_editor.toPlainText().strip()
        try:
            data = yaml.safe_load(text or "{}") or {}
        except Exception:
            data = None
        if isinstance(data, dict):
            error = _button_preview_validation_error(data)
            if error:
                self.button_preview_label.clear()
                self.button_preview_status.setText(error)
                self._button_edit_preview_timer.stop()
                return
        self.button_preview_status.setText("Rendering preview…")
        self._button_edit_preview_generation += 1
        self._button_edit_preview_timer.start(120)

    def _render_button_edit_preview(self) -> None:
        generation = self._button_edit_preview_generation
        button_id = self._button_edit_id
        target_root = self._current_target_path
        deck_name = self._visual_deck_name
        text = self.button_edit_editor.toPlainText().strip()
        if button_id is None or target_root is None or not deck_name:
            self.button_preview_label.clear()
            self.button_preview_status.setText("Preview unavailable.")
            return
        try:
            data = yaml.safe_load(text) or {}
        except Exception as exc:
            self.button_preview_label.clear()
            self.button_preview_status.setText(f"Invalid YAML: {exc}")
            return
        if not isinstance(data, dict):
            self.button_preview_label.clear()
            self.button_preview_status.setText("Preview requires a YAML mapping.")
            return
        if "index" not in data and button_id in self._visual_buttons:
            data["index"] = self._visual_buttons[button_id].get("index")
        preview_yaml = yaml.safe_dump(data, sort_keys=False, allow_unicode=False)

        def _worker(preview_target_root=target_root, preview_deck=deck_name, preview_yaml_text=preview_yaml, preview_generation=generation) -> None:
            image_bytes = None
            meta = None
            error = "no preview target"
            warning = None
            image_bytes, meta, error = _render_preview_with_fallback(preview_target_root, preview_deck, preview_yaml_text)
            if isinstance(meta, dict):
                meta_error = str(meta.get("error") or "").strip()
                if meta_error and meta_error != "ok":
                    if image_bytes and not error:
                        warning = meta_error
                    elif not image_bytes:
                        error = error or meta_error
            self.button_edit_preview_ready.emit(image_bytes, {"generation": preview_generation, "error": error, "warning": warning})

        threading.Thread(target=_worker, daemon=True).start()

    def _on_button_edit_preview_ready(self, image_bytes: object, info: object) -> None:
        payload = info if isinstance(info, dict) else {}
        if payload.get("generation") != self._button_edit_preview_generation:
            return
        error = str(payload.get("error") or "").strip()
        warning = str(payload.get("warning") or "").strip()
        if isinstance(image_bytes, (bytes, bytearray)) and image_bytes:
            pixmap = QPixmap()
            if pixmap.loadFromData(bytes(image_bytes), "PNG"):
                target = max(120, min(self.button_preview_label.width(), self.button_preview_label.height()))
                src_w = max(1, pixmap.width())
                src_h = max(1, pixmap.height())
                dpr = max(1.0, min(src_w / target, src_h / target))
                pixmap.setDevicePixelRatio(dpr)
                self.button_preview_label.setPixmap(pixmap)
                self.button_preview_status.setText(f"Warning: {warning}" if warning else "Live native preview")
                return
            error = error or "preview decode failed"
        self.button_preview_label.clear()
        if error:
            self.button_preview_label.setText(error)
            self.button_preview_status.setText("")
        else:
            self.button_preview_status.setText("Preview unavailable.")

    def _button_edit_is_dirty(self) -> bool:
        if self._button_edit_id is None:
            return False
        return self.button_edit_editor.toPlainText() != self._button_edit_base_text

    def _queue_visible_previews(self) -> None:
        if not self._visual_enabled or self._visual_cols <= 0:
            return
        target_root = self._current_target_path
        if target_root is None:
            return
        target_key = str(target_root.resolve())
        if target_key not in self._preview_ready_targets:
            self.visual_hint.setText(
                f"Grid {self._visual_cols}×{self._visual_rows}. Warming preview engine before rendering visible buttons…"
            )
            return
        row_height = max(1, int(128 * self._visual_zoom) + 8)
        viewport_h = max(1, self.visual_scroll.viewport().height())
        scroll_y = self.visual_scroll.verticalScrollBar().value()
        start_row = max(0, scroll_y // row_height)
        visible_rows = max(1, (viewport_h + row_height - 1) // row_height)
        end_row = min(self._visual_rows - 1, start_row + visible_rows + 1)
        start_index = start_row * self._visual_cols
        end_index = (end_row + 1) * self._visual_cols
        self.visual_hint.setText(f"Grid {self._visual_cols}×{self._visual_rows}. Drag to move buttons. Drop on an occupied slot to swap.")
        for button_id, button in self._visual_buttons.items():
            index = self._button_index(button)
            if index is None:
                # Named-slot button — always queue preview since it's always visible
                if button_id in self._visible_named_cards:
                    self._ensure_button_preview(button_id)
                continue
            if start_index <= index < end_index:
                self._ensure_button_preview(button_id)

    def _infer_grid_dimensions(self, page_path: Path, buttons: list[dict]) -> tuple[int, int]:
        max_index = max((int(btn.get("index", -1)) for btn in buttons if isinstance(btn, dict) and isinstance(btn.get("index"), int)), default=-1)
        target_root = self._current_target_path
        if target_root is not None:
            config_path = target_root / "deckconfig" / "config.yaml"
            type_dir = target_root / "deckconfig" / "resources" / "decks" / "types"
            try:
                cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
                layout_id = page_path.parent.name
                for deck in cfg.get("decks", []):
                    if not isinstance(deck, dict) or deck.get("layout") != layout_id:
                        continue
                    type_name = deck.get("type")
                    if not type_name or not type_dir.is_dir():
                        break
                    for type_file in sorted(type_dir.glob("*.y*ml")):
                        tcfg = yaml.safe_load(type_file.read_text(encoding="utf-8")) or {}
                        if tcfg.get("name") != type_name:
                            continue
                        defs = tcfg.get("buttons") or []
                        if defs and isinstance(defs[0], dict):
                            repeat = defs[0].get("repeat") or []
                            if len(repeat) == 2:
                                cols = int(repeat[0])
                                rows = int(repeat[1])
                                return max(cols, 1), max(rows, 1)
            except Exception:
                pass
        if max_index < 0:
            return 6, 4
        total = max_index + 1
        if total <= 12:
            cols = min(4, total)
        elif total <= 24:
            cols = 6
        elif total <= 48:
            cols = 8
        else:
            cols = 12
        rows = max(1, (total + cols - 1) // cols)
        return cols, rows

    def _rebuild_visual_widgets(self) -> None:
        # Clear selection state when rebuilding the grid (e.g. switching pages/decks)
        self._selected_button_ids.clear()
        self._selected_button_id = None
        self._refresh_selected_button_panel()
        
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for card in list(self._visible_named_cards.values()):
            try:
                card.deleteLater()
            except RuntimeError:
                pass
        self._visible_cards = {}
        self._visible_slots = {}
        self._visible_cell_slots = {}
        self._visible_named_cards = {}
        self._span_card_specs = {}

        if not self._visual_enabled:
            self.visual_hint.setText("Visual mode is available for YAML page files with a `buttons:` list.")
            return

        self.visual_hint.setText(f"Grid {self._visual_cols}×{self._visual_rows}. Drag to move buttons. Drop on an occupied slot to swap.")
        self.zoom_label.setText(f"{int(round(self._visual_zoom * 100))}%")
        print(f"DEBUG: _rebuild_visual_widgets starting for {self._visual_cols}x{self._visual_rows}")

        tile_px = int(128 * self._visual_zoom)
        gap_px = 8

        for col in range(self._visual_cols):
            self.grid_layout.setColumnMinimumWidth(col, tile_px)
        for row in range(self._visual_rows):
            self.grid_layout.setRowMinimumHeight(row, tile_px)

        card_px = int(118 * self._visual_zoom)
        margin = (tile_px - card_px) // 2

        by_index = {
            index: button_id
            for button_id, button in self._visual_buttons.items()
            if (index := self._button_index(button)) is not None
        }

        # ── Compute span origins and covered cells ────────────────────────────
        span_origins: dict[tuple[int, int], tuple[int, int, str]] = {}  # (r,c) → (sw, sh, btn_id)
        covered_cells: set[tuple[int, int]] = set()
        for button_id, button in self._visual_buttons.items():
            index = self._button_index(button)
            if index is None:
                continue
            
            # Robust span parsing (handles lists, strings, and partials)
            span_raw = button.get("span")
            sw, sh = 1, 1
            if isinstance(span_raw, (list, tuple)) and len(span_raw) >= 2:
                try:
                    sw, sh = max(1, int(span_raw[0])), max(1, int(span_raw[1]))
                except (ValueError, TypeError, IndexError): pass
            elif isinstance(span_raw, str):
                try:
                    parts = span_raw.replace(","," ").split()
                    if len(parts) >= 2:
                        sw, sh = max(1, int(parts[0])), max(1, int(parts[1]))
                except (ValueError, TypeError, IndexError): pass
            
            if sw == 1 and sh == 1:
                continue
                
            r0, c0 = index // self._visual_cols, index % self._visual_cols
            span_origins[(r0, c0)] = (sw, sh, button_id)
            for dr in range(sh):
                for dc in range(sw):
                    covered_cells.add((r0 + dr, c0 + dc))
            print(f"DEBUG: Spanned Button {button_id} at ({r0},{c0}) -> {sw}x{sh}")

        # ── Build QGridLayout ─────────────────────────────────────────────────
        for row in range(self._visual_rows):
            for col in range(self._visual_cols):
                slot_index = row * self._visual_cols + col
                slot = _GridSlot(slot_index, dark=self._dark_mode, scale=self._visual_zoom)
                slot.dropped.connect(self._move_button_to_index)
                slot.create_requested.connect(self._create_new_button_at_index)
                slot.context_requested.connect(self._show_slot_context_menu)
                slot.deselect_requested.connect(self._clear_visual_selection)
                self._visible_cell_slots[(row, col)] = slot
                if (row, col) in covered_cells:
                    slot.set_force_hidden(True)

                button_id = by_index.get(slot_index)
                span_origin = span_origins.get((row, col))

                # ── Handle Spanned Cards (Natively in Grid) ───────────────────
                if span_origin:
                    sw, sh, span_button_id = span_origin
                    button = self._visual_buttons.get(span_button_id)
                    if button:
                        card = _VisualButtonCard(
                            span_button_id,
                            button,
                            dark=self._dark_mode,
                            scale=self._visual_zoom,
                            preview=self._preview_cache.get(self._preview_key(span_button_id)),
                            preview_status=self._preview_errors.get(self._preview_key(span_button_id)),
                        )
                        card.selected.connect(self._set_selected_visual_button)
                        card.edit_requested.connect(self._select_visual_button)
                        card.context_requested.connect(self._show_button_context_menu)

                        # Sync size: (Area) - (2 * 1x1 margin)
                        sw_px = sw * tile_px + (sw - 1) * gap_px - 2 * margin
                        sh_px = sh * tile_px + (sh - 1) * gap_px - 2 * margin
                        card.resize_to_span(sw_px, sh_px)

                        self.grid_layout.addWidget(card, row, col, sh, sw)
                        self._visible_cards[span_button_id] = card
                        self._visible_named_cards[span_button_id] = card
                        if span_button_id in self._selected_button_ids:
                            card.set_selected(True)
                        card.show()
                        card.raise_()

                # ── Handle Primary 1x1 Cards (Inside Slot) ────────────────────
                elif button_id is not None:
                    button = self._visual_buttons[button_id]
                    card = _VisualButtonCard(
                        button_id,
                        button,
                        dark=self._dark_mode,
                        scale=self._visual_zoom,
                        preview=self._preview_cache.get(self._preview_key(button_id)),
                        preview_status=self._preview_errors.get(self._preview_key(button_id)),
                    )
                    card.selected.connect(self._set_selected_visual_button)
                    card.edit_requested.connect(self._select_visual_button)
                    card.context_requested.connect(self._show_button_context_menu)
                    slot.set_card(card)
                    self._visible_cards[button_id] = card
                    self._visible_slots[button_id] = slot
                    if button_id in self._selected_button_ids:
                        card.set_selected(True)
                
                # Only add slot if not covered by a spanned card
                if (row, col) not in covered_cells:
                    self.grid_layout.addWidget(slot, row, col, 1, 1)
                else:
                    # If covered, we still need to track it for drops, but it's not in the layout
                    slot.setParent(None)
                    slot.deleteLater()

        # Stretch the last column and row so the grid stays top-left aligned
        self.grid_layout.setColumnStretch(self._visual_cols, 1)
        self.grid_layout.setRowStretch(self._visual_rows, 1)

        # ── Named-slot buttons (legacy string indices) ────────────────────────
        named_buttons = [
            (button_id, button)
            for button_id, button in self._visual_buttons.items()
            if isinstance(button.get("index"), str) and not button.get("index", "").strip().lstrip("-").isdigit()
        ]
        if named_buttons:
            extra_row = self._visual_rows + 1
            sep_label = QLabel("Named Slots")
            sep_label.setStyleSheet("font-size: 11px; font-weight: 600; color: #64748b; padding: 4px 0 2px 0;")
            self.grid_layout.addWidget(sep_label, extra_row, 0, 1, max(1, self._visual_cols))
            named_row_widget = QWidget()
            named_row_layout = QHBoxLayout(named_row_widget)
            named_row_layout.setContentsMargins(0, 0, 0, 0)
            named_row_layout.setSpacing(8)
            for button_id, button in named_buttons:
                card = _VisualButtonCard(
                    button_id,
                    button,
                    dark=self._dark_mode,
                    scale=self._visual_zoom,
                    preview=self._preview_cache.get(self._preview_key(button_id)),
                    preview_status=self._preview_errors.get(self._preview_key(button_id)),
                )
                card.selected.connect(self._set_selected_visual_button)
                card.edit_requested.connect(self._select_visual_button)
                card.context_requested.connect(self._show_button_context_menu)
                self._visible_cards[button_id] = card
                self._visible_named_cards[button_id] = card
                named_row_layout.addWidget(card)
            named_row_layout.addStretch(1)
            self.grid_layout.addWidget(named_row_widget, extra_row + 1, 0, 1, max(1, self._visual_cols))
        # ── end named slots ───────────────────────────────────────────────────

        self.grid_host.adjustSize()
        self.grid_host.updateGeometry()
        self.visual_root.adjustSize()
        self.visual_root.updateGeometry()
        self.grid_layout.activate()
        self._apply_selection_highlights()
        QTimer.singleShot(0, self._queue_visible_previews)

    def _move_button_to_index(self, button_id: str, target_index: int) -> None:
        if button_id not in self._visual_buttons:
            return
        # Multi-drag: if the dragged card is part of a multi-selection, shift all selected
        if button_id in self._selected_button_ids and len(self._selected_button_ids) > 1:
            source_index = self._button_index(self._visual_buttons[button_id])
            if source_index is None or source_index == target_index:
                return
            delta = target_index - source_index
            total = self._visual_cols * self._visual_rows
            non_selected_occupied = {
                b["index"]
                for bid, b in self._visual_buttons.items()
                if bid not in self._selected_button_ids and isinstance(b.get("index"), int)
            }
            for bid in self._selected_button_ids:
                curr = self._visual_buttons.get(bid, {}).get("index")
                if not isinstance(curr, int):
                    continue
                new_idx = curr + delta
                if new_idx < 0 or new_idx >= total or new_idx in non_selected_occupied:
                    self.status_label.setText("Cannot move: target slots are occupied or out of bounds.")
                    return
            for bid in self._selected_button_ids:
                curr = self._visual_buttons.get(bid, {}).get("index")
                if isinstance(curr, int):
                    self._visual_buttons[bid]["index"] = curr + delta
        else:
            # Single button move — swap with occupant if present
            current_button = self._visual_buttons[button_id]
            current_index = current_button.get("index")
            for other_id, other in self._visual_buttons.items():
                if other_id != button_id and other.get("index") == target_index:
                    self._visual_buttons[other_id]["index"] = current_index
                    break
            current_button["index"] = target_index
        scroll_pos = self.visual_scroll.verticalScrollBar().value()
        self._sync_text_from_visual()
        self._rebuild_visual_widgets()
        self._update_action_state()
        QTimer.singleShot(0, lambda: self.visual_scroll.verticalScrollBar().setValue(scroll_pos))

    def _set_selected_visual_button(self, button_id: str) -> None:
        if button_id not in self._visual_buttons:
            return
        
        modifiers = QApplication.keyboardModifiers()
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        cmd = bool(modifiers & Qt.KeyboardModifier.ControlModifier) # Command on macOS

        if shift and self._selected_button_id is not None:
            # Range-select: add all buttons between last anchor and this one
            last_idx = self._button_index(self._visual_buttons.get(self._selected_button_id, {}))
            this_idx = self._button_index(self._visual_buttons.get(button_id, {}))
            if last_idx is not None and this_idx is not None:
                lo, hi = min(last_idx, this_idx), max(last_idx, this_idx)
                for bid, bdata in self._visual_buttons.items():
                    idx = self._button_index(bdata)
                    if idx is not None and lo <= idx <= hi:
                        self._selected_button_ids.add(bid)
            self._selected_button_id = button_id
        elif cmd:
            # Command+Click: toggle this button
            if button_id in self._selected_button_ids:
                self._selected_button_ids.discard(button_id)
                if self._selected_button_id == button_id:
                    self._selected_button_id = next(iter(self._selected_button_ids), None)
            else:
                self._selected_button_ids.add(button_id)
                self._selected_button_id = button_id
        else:
            # Plain click: ignore for selection (standard UX remains stable)
            return

        self._apply_selection_highlights()
        self._refresh_selected_button_panel()

    def _select_visual_button(self, button_id: str) -> None:
        if button_id not in self._visual_buttons:
            return
        self._selected_button_ids = {button_id}
        self._selected_button_id = button_id
        self._apply_selection_highlights()
        self._refresh_selected_button_panel()
        self._open_button_editor_workspace(button_id)

    def _position_span_cards(self) -> None:
        """DEPRECATED: Now handled by native QGridLayout spanning in _rebuild_visual_widgets."""
        pass


    def _apply_selection_highlights(self) -> None:
        for bid, slot in self._visible_slots.items():
            slot.set_selected(bid in self._selected_button_ids)
        for bid, card in self._visible_named_cards.items():
            card.set_selected(bid in self._selected_button_ids)

    def _clear_visual_selection(self) -> None:
        self._selected_button_ids.clear()
        self._selected_button_id = None
        self._apply_selection_highlights()
        self._refresh_selected_button_panel()

    def _delete_selection(self) -> None:
        ids = [bid for bid in self._selected_button_ids if bid in self._visual_buttons]
        if not ids:
            return
        noun = f"{len(ids)} buttons" if len(ids) > 1 else "this button"
        answer = QMessageBox.question(
            self,
            "Delete buttons?" if len(ids) > 1 else "Delete button?",
            f"Delete {noun} from the page?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        for bid in ids:
            self._visual_buttons.pop(bid, None)
            self._drop_preview_cache(bid)
        self._selected_button_ids.clear()
        self._selected_button_id = None
        scroll_pos = self.visual_scroll.verticalScrollBar().value()
        self._sync_text_from_visual()
        self._rebuild_visual_widgets()
        self._refresh_selected_button_panel()
        self.status_label.setText(f"Deleted {len(ids)} button{'s' if len(ids) > 1 else ''}.")
        self._update_action_state()
        QTimer.singleShot(0, lambda: self.visual_scroll.verticalScrollBar().setValue(scroll_pos))

    def _show_button_context_menu(self, button_id: str, global_pos: QPoint) -> None:
        if button_id not in self._visual_buttons:
            return
        # Right-click on an unselected button: replace selection
        if button_id not in self._selected_button_ids:
            self._selected_button_ids = {button_id}
            self._selected_button_id = button_id
            self._apply_selection_highlights()
            self._refresh_selected_button_panel()
        is_multi = len(self._selected_button_ids) > 1
        count = len(self._selected_button_ids)
        menu = QMenu(self)
        if is_multi:
            copy_action = menu.addAction(f"Copy {count} Buttons")
            menu.addSeparator()
            paste_action = None
            edit_action = None
            delete_action = menu.addAction(f"Delete {count} Buttons")
        else:
            copy_action = menu.addAction("Copy Button")
            edit_action = menu.addAction("Edit Button")
            paste_action = menu.addAction("Paste Over Button")
            paste_action.setEnabled(self._clipboard_button_data() is not None)
            delete_action = menu.addAction("Delete Button")
        chosen = menu.exec(global_pos)
        if chosen is copy_action:
            self._copy_selection_to_clipboard()
            return
        if edit_action and chosen is edit_action:
            self._select_visual_button(button_id)
            return
        if paste_action and chosen is paste_action:
            index = self._button_index(self._visual_buttons[button_id])
            if index is not None:
                self._paste_button_at_index(index)
            return
        if chosen is delete_action:
            self._delete_selection()

    def _show_slot_context_menu(self, slot_index: int, global_pos: QPoint) -> None:
        menu = QMenu(self)
        create_action = menu.addAction("Create Button")
        paste_action = menu.addAction("Paste Button")
        paste_action.setEnabled(bool(self._clipboard_buttons_list()))
        chosen = menu.exec(global_pos)
        if chosen is create_action:
            self._create_new_button_at_index(slot_index)
            return
        if chosen is paste_action:
            self._paste_buttons_from_clipboard()

    def _copy_selection_to_clipboard(self) -> None:
        ids = [bid for bid in self._selected_button_ids if bid in self._visual_buttons]
        if not ids:
            return
        buttons = [dict(self._visual_buttons[bid]) for bid in ids]
        payload_obj: list | dict = buttons if len(buttons) > 1 else buttons[0]
        mime = QMimeData()
        mime.setData(_BUTTON_CLIPBOARD_MIME, json.dumps(payload_obj, ensure_ascii=True).encode("utf-8"))
        if len(buttons) == 1:
            mime.setText(yaml.safe_dump(buttons[0], sort_keys=False, allow_unicode=False))
        QApplication.clipboard().setMimeData(mime)
        label = buttons[0].get("name") or ids[0] if len(ids) == 1 else f"{len(ids)} buttons"
        self.status_label.setText(f"Copied {label} to clipboard.")

    def _clipboard_buttons_list(self) -> list[dict]:
        mime = QApplication.clipboard().mimeData()
        if mime is None:
            return []
        if mime.hasFormat(_BUTTON_CLIPBOARD_MIME):
            try:
                data = json.loads(bytes(mime.data(_BUTTON_CLIPBOARD_MIME)).decode("utf-8"))
            except Exception:
                data = None
            if isinstance(data, dict):
                return [data]
            if isinstance(data, list):
                return [d for d in data if isinstance(d, dict)]
        text = QApplication.clipboard().text().strip()
        if not text:
            return []
        try:
            data = yaml.safe_load(text) or {}
        except Exception:
            return []
        return [data] if isinstance(data, dict) else []

    def _clipboard_button_data(self) -> dict | None:
        buttons = self._clipboard_buttons_list()
        return buttons[0] if buttons else None

    def _paste_buttons_from_clipboard(self) -> None:
        buttons = self._clipboard_buttons_list()
        if not buttons:
            return
        occupied = {
            b["index"]
            for b in self._visual_buttons.values()
            if isinstance(b.get("index"), int)
        }
        total = self._visual_cols * self._visual_rows
        free = [i for i in range(total) if i not in occupied]
        if len(free) < len(buttons):
            QMessageBox.information(
                self, "Paste",
                f"Not enough free slots ({len(free)} available, {len(buttons)} needed).",
            )
            return
        new_ids: list[str] = []
        for button, slot_index in zip(buttons, free):
            pasted = dict(button)
            pasted["index"] = slot_index
            next_seq = 0
            while f"btn-{next_seq}" in self._visual_buttons:
                next_seq += 1
            bid = f"btn-{next_seq}"
            new_name = self._unique_button_name(self._button_name(pasted), list(self._visual_buttons.values()))
            if new_name:
                pasted["name"] = new_name
            else:
                pasted.pop("name", None)
            self._visual_buttons[bid] = pasted
            self._drop_preview_cache(bid)
            new_ids.append(bid)
        self._selected_button_ids = set(new_ids)
        self._selected_button_id = new_ids[-1] if new_ids else None
        scroll_pos = self.visual_scroll.verticalScrollBar().value()
        self._sync_text_from_visual()
        self._rebuild_visual_widgets()
        self._refresh_selected_button_panel()
        self.status_label.setText(f"Pasted {len(new_ids)} button{'s' if len(new_ids) > 1 else ''}.")
        self._update_action_state()
        QTimer.singleShot(0, lambda: self.visual_scroll.verticalScrollBar().setValue(scroll_pos))

    def _paste_button_at_index(self, target_index: int) -> None:
        data = self._clipboard_button_data()
        if data is None:
            QMessageBox.information(self, "Paste Button", "Clipboard does not contain a Cockpitdecks button config.")
            return
        pasted = dict(data)
        pasted["index"] = target_index
        new_name = self._unique_button_name(
            self._button_name(pasted),
            list(self._visual_buttons.values()),
            exclude_index=target_index,
        )
        if new_name:
            pasted["name"] = new_name
        else:
            pasted.pop("name", None)
        existing_id = self._button_id_at_index(target_index)
        if existing_id is not None:
            self._visual_buttons[existing_id] = pasted
            self._selected_button_id = existing_id
            self._drop_preview_cache(existing_id)
            target_label = existing_id
        else:
            button_id = f"btn-{target_index}"
            suffix = 1
            while button_id in self._visual_buttons:
                suffix += 1
                button_id = f"btn-{target_index}-{suffix}"
            self._visual_buttons[button_id] = pasted
            self._selected_button_id = button_id
            self._drop_preview_cache(button_id)
            target_label = button_id
        scroll_pos = self.visual_scroll.verticalScrollBar().value()
        self._sync_text_from_visual()
        self._rebuild_visual_widgets()
        self._refresh_selected_button_panel()
        self._update_action_state()
        self.status_label.setText(f"Pasted button into slot {target_index}.")
        QTimer.singleShot(0, lambda: self.visual_scroll.verticalScrollBar().setValue(scroll_pos))

    def _apply_button_yaml(self, button_id: str, text: str, *, silent: bool = False) -> bool:
        try:
            data = yaml.safe_load(text) or {}
        except Exception as exc:
            if not silent:
                QMessageBox.warning(self, "Invalid button YAML", str(exc))
            return False
        if not isinstance(data, dict):
            if not silent:
                QMessageBox.warning(self, "Invalid button YAML", "Button config must parse to a YAML mapping.")
            return False
        if "annunciator" in data and any(key in data for key in ("text", "text-size", "text-color")):
            if not silent:
                QMessageBox.warning(
                    self,
                    "Invalid button YAML",
                    "This button mixes an annunciator representation with top-level text fields. Remove the top-level text fields or switch representation.",
                )
            return False
        ann = data.get("annunciator")
        if isinstance(ann, dict):
            parts = ann.get("parts")
            if isinstance(parts, dict):
                model = str(ann.get("model") or "B")
                wanted_parts = set(_ANNUNCIATOR_PART_IDS.get(model, ["B0", "B1"]))
                cleaned_parts = {k: v for k, v in parts.items() if k in wanted_parts and isinstance(v, dict)}
                if cleaned_parts:
                    ann["parts"] = cleaned_parts
                else:
                    ann.pop("parts", None)
        if button_id not in self._visual_buttons:
            return False
        current_index = self._visual_buttons[button_id].get("index")
        if "index" not in data:
            data["index"] = current_index
        self._visual_buttons[button_id] = data
        self._drop_preview_cache(button_id)
        self._sync_text_from_visual()
        self._rebuild_visual_widgets()
        self._refresh_selected_button_panel()
        self.status_label.setText("Applied selected button config.")
        self._update_action_state()
        return True

    def _open_button_editor_workspace(self, button_id: str, *, initial_text: str | None = None, on_apply=None) -> None:
        self._button_edit_id = button_id
        self._button_edit_on_apply = on_apply
        self._clear_view_mode_checks()
        if initial_text is None and button_id in self._visual_buttons:
            initial_text = yaml.safe_dump(self._visual_buttons[button_id], sort_keys=False, allow_unicode=False)
        self._button_edit_base_text = initial_text or ""
        self._button_doc.load_text(self._button_edit_base_text)
        self._loading_file = True
        try:
            self.button_edit_editor.setPlainText(self._button_edit_base_text)
        finally:
            self._loading_file = False
        self._sync_visual_fields_from_doc()
        self.button_edit_tabs.setCurrentWidget(self.button_visual_tab)
        self._selected_button_id = button_id
        self._refresh_selected_button_panel()
        self._designer_panel.setVisible(False)
        self.stack.setCurrentWidget(self.button_edit_page)
        page_name = self._current_file_path.stem if self._current_file_path is not None else "page"
        self.file_label.setText(f'<a href="back">{page_name}</a> \u2192 {button_id}')
        self._schedule_button_edit_preview()
        self._update_action_state()

    def _close_button_editor_workspace(self) -> None:
        self._button_edit_preview_timer.stop()
        self.button_preview_label.clear()
        self.button_preview_status.setText("Preview will appear here.")
        self._button_edit_id = None
        self._button_edit_on_apply = None
        self._button_edit_base_text = ""
        if self._current_file_path is not None:
            self.file_label.setText(str(self._current_file_path.name))
        else:
            self.file_label.setText("Select a config file")
        if self._visual_enabled:
            self.btn_visual_view.setChecked(True)
            self.stack.setCurrentWidget(self.visual_scroll)
        else:
            self.btn_text_view.setChecked(True)
            self.stack.setCurrentWidget(self.editor)
        self._refresh_selected_button_panel()
        if self._visual_enabled:
            self.status_label.setText("Visual mode: drag buttons in the grid or double-click one to edit it.")
            QTimer.singleShot(0, self._queue_visible_previews)
        self._update_action_state()

    def _apply_button_edit_workspace(self) -> None:
        if self._button_edit_id is None:
            return
        text = self.button_edit_editor.toPlainText()
        ok = self._button_edit_on_apply(text) if self._button_edit_on_apply is not None else self._apply_button_yaml(self._button_edit_id, text)
        if ok:
            button_id = self._button_edit_id
            if self.save_current_file():
                self._button_edit_base_text = text
                self._button_doc.load_text(text)
                self.status_label.setText(f"Saved {button_id} to {self._current_file_path.name if self._current_file_path is not None else 'file'}.")
                self._schedule_button_edit_preview()
                self._update_action_state()

    def _delete_button_from_workspace(self) -> None:
        if self._button_edit_id is None or self._button_edit_on_apply is not None:
            return
        answer = QMessageBox.question(
            self,
            "Delete button?",
            "Delete this button from the page?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._delete_button(self._button_edit_id)
            self._close_button_editor_workspace()

    def _create_new_button_at_index(self, target_index: int) -> None:
        if not self._visual_enabled:
            QMessageBox.information(self, "Visual mode unavailable", "Open a page YAML with buttons first.")
            return
        occupied = {
            int(button.get("index"))
            for button in self._visual_buttons.values()
            if isinstance(button.get("index"), int)
        }
        if target_index in occupied:
            return
        next_seq = 0
        while f"btn-{next_seq}" in self._visual_buttons:
            next_seq += 1
        button_id = f"btn-{next_seq}"
        new_name = self._unique_button_name(f"NEW {next_seq}", list(self._visual_buttons.values()))
        new_button = {
            "index": target_index,
            "name": new_name,
            "activation": "push",
            "label": new_name,
            "label-size": 12,
            "commands": {"press": "sim/none/command"},
        }
        self._visual_buttons[button_id] = new_button
        self._selected_button_id = button_id
        self._sync_text_from_visual()
        self._rebuild_visual_widgets()
        self._refresh_selected_button_panel()
        self._open_button_editor_workspace(
            button_id,
            initial_text=yaml.safe_dump(new_button, sort_keys=False, allow_unicode=False),
        )

    def _delete_button(self, button_id: str) -> None:
        if button_id not in self._visual_buttons:
            return
        self._visual_buttons.pop(button_id, None)
        if self._selected_button_id == button_id:
            self._selected_button_id = None
        self._selected_button_ids.discard(button_id)
        self._drop_preview_cache(button_id)
        scroll_pos = self.visual_scroll.verticalScrollBar().value()
        self._sync_text_from_visual()
        self._rebuild_visual_widgets()
        self._refresh_selected_button_panel()
        self.status_label.setText("Button deleted.")
        self._update_action_state()
        QTimer.singleShot(0, lambda: self.visual_scroll.verticalScrollBar().setValue(scroll_pos))

    def _set_visual_zoom(self, value: float) -> None:
        new_zoom = max(0.5, min(2.0, round(value, 2)))
        if abs(new_zoom - self._visual_zoom) < 0.001:
            return
        self._visual_zoom = new_zoom
        self.zoom_label.setText(f"{int(round(self._visual_zoom * 100))}%")
        if self._visual_enabled:
            self._rebuild_visual_widgets()
            self.status_label.setText(f"Visual zoom: {int(round(self._visual_zoom * 100))}%")
        else:
            self.zoom_label.setText(f"{int(round(self._visual_zoom * 100))}%")

    def _fit_visual_zoom(self) -> None:
        if not self._visual_enabled or self._visual_cols <= 0:
            self._set_visual_zoom(1.0)
            return
        slot_size = 128
        gap = 8

        viewport_w = max(260, self.visual_scroll.viewport().width() - 32)
        viewport_h = max(260, self.visual_scroll.viewport().height() - 160)

        needed_w = self._visual_cols * slot_size + max(0, self._visual_cols - 1) * gap
        needed_h = self._visual_rows * slot_size + max(0, self._visual_rows - 1) * gap

        fit_w = viewport_w / needed_w if needed_w > 0 else 1.0
        fit_h = viewport_h / needed_h if needed_h > 0 else 1.0
        fit = min(fit_w, fit_h) * 0.96
        self._set_visual_zoom(min(1.0, fit))
        self.status_label.setText(f"Fitted visual grid to {int(round(self._visual_zoom * 100))}%.")

    def _sync_text_from_visual(self) -> None:
        if self._visual_yaml_data is None:
            return
        buttons = list(self._visual_buttons.values())
        placed = [btn for btn in buttons if self._button_index(btn) is not None]
        placed.sort(key=lambda btn: self._button_index(btn) or -1)
        if len(placed) != len(buttons):
            return
        self._visual_yaml_data["buttons"] = placed
        dumped = yaml.safe_dump(self._visual_yaml_data, sort_keys=False, allow_unicode=False)
        self._set_editor_text(dumped, mark_modified=True)

    def _set_editor_text(self, text: str, *, mark_modified: bool = False) -> None:
        self._loading_file = True
        try:
            self.editor.setPlainText(text)
            self.editor.document().setModified(mark_modified)
        finally:
            self._loading_file = False
        if mark_modified:
            self.modified_label.setText("Unsaved changes")
            self._page_autosave_timer.start(1000)
        else:
            self.modified_label.setText("")
        self._effective_page_attrs_cache = {}

    def _update_action_state(self) -> None:
        has_target = self._current_target_path is not None
        has_file = self._current_file_path is not None
        current = self.stack.currentWidget()
        visual_active = current is self.visual_scroll
        config_visual_active = current is self.config_form_scroll
        button_edit_active = current is self.button_edit_page
        self.btn_refresh.setEnabled(has_target)
        self.btn_reveal_target.setEnabled(has_target)
        self.btn_reveal_file.setEnabled(has_file)
        self._view_zoom_bar.setVisible(not button_edit_active)
        self.btn_visual_view.setEnabled(self._visual_enabled)
        self.btn_zoom_out.setEnabled(self._visual_enabled and visual_active)
        self.btn_zoom_fit.setEnabled(self._visual_enabled and visual_active)
        self.btn_zoom_in.setEnabled(self._visual_enabled and visual_active)
        self.btn_zoom_out.setVisible(not config_visual_active)
        self.btn_zoom_fit.setVisible(not config_visual_active)
        self.btn_zoom_in.setVisible(not config_visual_active)
        self.zoom_label.setVisible(not config_visual_active)
        self.btn_apply_preset.setEnabled(self._visual_enabled and self._selected_button_id is not None)

    def _preview_key(self, button_id: str) -> str:
        deck_name = self._visual_deck_name or ""
        button = self._button_preview_config(button_id)
        rendered = yaml.safe_dump(button, sort_keys=False, allow_unicode=False)
        return f"{deck_name}\n{rendered}"

    def _drop_preview_cache(self, button_id: str) -> None:
        key = self._preview_key(button_id)
        self._preview_cache.pop(key, None)
        self._preview_errors.pop(key, None)
        self._preview_inflight.discard(key)
        self._preview_queue_keys.discard(key)
        self._preview_queue = [item for item in self._preview_queue if item[0] != key]

    def _ensure_button_preview(self, button_id: str) -> None:
        if button_id not in self._visual_buttons or not self._visual_deck_name:
            return
        key = self._preview_key(button_id)
        if key in self._preview_cache or key in self._preview_errors or key in self._preview_inflight or key in self._preview_queue_keys:
            return
        button_yaml = yaml.safe_dump(self._button_preview_config(button_id), sort_keys=False, allow_unicode=False)
        deck_name = self._visual_deck_name
        generation = self._preview_generation
        self._preview_queue.append((key, deck_name, button_yaml, generation))
        self._preview_queue_keys.add(key)
        self._pump_preview_queue()

    def _pump_preview_queue(self) -> None:
        if not self._visual_enabled:
            return
        while self._preview_queue and len(self._preview_inflight) < self._preview_max_inflight:
            key, deck_name, button_yaml, generation = self._preview_queue.pop(0)
            self._preview_queue_keys.discard(key)
            if generation != self._preview_generation:
                continue
            self._preview_inflight.add(key)
            target_root = self._current_target_path

            def _worker(preview_key: str = key, preview_deck: str = deck_name, preview_yaml: str = button_yaml, preview_generation: int = generation, preview_target_root=target_root) -> None:
                image_bytes = None
                meta = None
                error = "no preview target"
                image_bytes, meta, error = _render_preview_with_fallback(preview_target_root, preview_deck, preview_yaml)
                if error is None and isinstance(meta, dict):
                    meta_error = str(meta.get("error") or "").strip()
                    if meta_error and meta_error != "ok":
                        error = meta_error
                self.preview_ready.emit(preview_key, image_bytes, {"generation": preview_generation, "error": error})

            threading.Thread(target=_worker, daemon=True).start()

    def _on_preview_ready(self, key: str, image_bytes: object, info: object) -> None:
        self._preview_inflight.discard(key)
        payload = info if isinstance(info, dict) else {}
        if payload.get("generation") != self._preview_generation:
            self._pump_preview_queue()
            return
        error = str(payload.get("error") or "").strip()
        if isinstance(image_bytes, (bytes, bytearray)) and image_bytes:
            pixmap = QPixmap()
            if pixmap.loadFromData(bytes(image_bytes), "PNG"):
                self._preview_cache[key] = pixmap
                self._preview_errors.pop(key, None)
            else:
                self._preview_errors[key] = "preview decode failed"
        elif error:
                self._preview_errors[key] = error
        else:
            self._preview_errors[key] = "preview unavailable"
        self._pump_preview_queue()
        if self._visual_enabled and self.stack.currentWidget() is self.visual_scroll:
            self._preview_refresh_timer.start(75)

    def _refresh_preview_results(self) -> None:
        if self._visual_enabled and self.stack.currentWidget() is self.visual_scroll:
            for button_id, card in list(self._visible_cards.items()):
                if button_id not in self._visual_buttons:
                    continue
                key = self._preview_key(button_id)
                card.update_preview(
                    self._preview_cache.get(key),
                    self._preview_errors.get(key),
                )

    def keyPressEvent(self, event) -> None:
        if self.stack.currentWidget() is self.visual_scroll and self._selected_button_ids:
            if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self._delete_selection()
                event.accept()
                return
            if event.matches(QKeySequence.StandardKey.Copy):
                self._copy_selection_to_clipboard()
                event.accept()
                return
        if self.stack.currentWidget() is self.visual_scroll:
            if event.matches(QKeySequence.StandardKey.Paste):
                self._paste_buttons_from_clipboard()
                event.accept()
                return
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
