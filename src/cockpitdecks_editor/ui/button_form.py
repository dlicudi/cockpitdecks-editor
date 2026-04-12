"""Standalone visual button form widget.

Provides the same activation + representation field panel used in EditorTab,
but as an independent QWidget that works from a plain dict and emits a signal
when the user changes anything.
"""
from __future__ import annotations

from typing import Any

import yaml
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QCheckBox,
)
from PySide6.QtGui import QFont

from cockpitdecks_editor.ui.editor_tab import (
    _ACTIVATION_SCHEMA,
    _ANNUNCIATOR_PART_IDS,
    _field_with_button,
    _set_form_row_visible,
    _two_command_fields,
)
from cockpitdecks_editor.services.native_preview import get_representation_schema_map
from cockpitdecks_editor.ui.templates import TEMPLATES


# ── Wheel-skip widgets ────────────────────────────────────────────────────────

class _NoWheelComboBox(QComboBox):
    def wheelEvent(self, event):
        event.ignore()


class _NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event):
        event.ignore()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _set_combo(combo: QComboBox, value: str) -> None:
    idx = combo.findData(value)
    if idx >= 0:
        combo.setCurrentIndex(idx)
        return
    idx = combo.findText(value)
    if idx >= 0:
        combo.setCurrentIndex(idx)
        return
    if combo.isEditable():
        combo.setEditText(value)
        return
    combo.setCurrentIndex(0)


class CollapsibleSection(QWidget):
    """A widget that provides a collapsible header and a content area."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        self.header = QPushButton(title)
        self.header.setCheckable(True)
        self.header.setChecked(True)
        self.header.setCursor(Qt.PointingHandCursor)
        self.header.setStyleSheet(
            "QPushButton { text-align: left; font-weight: bold; padding: 10px; "
            "background: #f1f5f9; color: #334155; border: 1px solid #e2e8f0; "
            "border-left: 4px solid #6366f1; border-radius: 4px; font-size: 13px; }"
            "QPushButton:hover { background: #e2e8f0; }"
            "QPushButton:checked { border-bottom-left-radius: 0; border-bottom-right-radius: 0; }"
        )
        vbox.addWidget(self.header)

        self.content_wrapper = QFrame()
        self.content_wrapper.setStyleSheet(
            "QFrame { background: #f8fafc; border: 1px solid #e2e8f0; "
            "border-top: none; border-bottom-left-radius: 4px; border-bottom-right-radius: 4px; }"
        )
        self.content_layout = QFormLayout(self.content_wrapper)
        self.content_layout.setContentsMargins(12, 12, 12, 12)
        vbox.addWidget(self.content_wrapper)

        self.header.toggled.connect(self.content_wrapper.setVisible)


def _activation_family_for_type(activation_type: str) -> str:
    for family, items in _ACTIVATION_SCHEMA.items():
        if any(name == activation_type for name, _ in items):
            return family
    return "Push Button"


def _representation_family_for_style(style: str) -> str:
    for family, items in _REPRESENTATION_SCHEMA.items():
        if any(name == style for name, _ in items):
            return family
    return next(iter(_REPRESENTATION_SCHEMA), "Representation")


def _known_representation_styles() -> set[str]:
    return {name for items in _REPRESENTATION_SCHEMA.values() for name, _ in items}


_REPRESENTATION_SCHEMAS = get_representation_schema_map()
_REPRESENTATION_SCHEMA: dict[str, list[tuple[str, str]]] = {}
for _schema_name, _schema in sorted(
    _REPRESENTATION_SCHEMAS.items(),
    key=lambda item: (
        str(item[1].get("family") or ""),
        str(item[1].get("label") or item[0]),
        item[0],
    ),
):
    _family = str(_schema.get("family") or "Representation")
    _REPRESENTATION_SCHEMA.setdefault(_family, []).append(
        (_schema_name, str(_schema.get("label") or _schema_name))
    )

_REPRESENTATION_NESTED_BLOCKS = {
    name
    for name, schema in _REPRESENTATION_SCHEMAS.items()
    if str(schema.get("storage_mode") or "flat") == "nested_block"
}
_REPRESENTATION_ROOT_FIELDS = {
    "cockpit-color",
    "cockpit-texture",
    "color",
    "formula",
    "frame",
    "icon",
    "label",
    "label-color",
    "label-font",
    "label-position",
    "label-size",
    "sound",
    "text",
    "text-color",
    "text-font",
    "text-format",
    "text-position",
    "text-size",
    "texture",
    "vibrate",
}

# ── Widget ────────────────────────────────────────────────────────────────────

class ButtonFormWidget(QWidget):
    """Visual form for editing a button config dict.

    Call ``load(data)`` to populate fields from a dict.
    Connect ``form_changed`` to receive updated YAML strings when the user edits.
    """

    form_changed = Signal(str)   # emits serialized YAML

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._syncing = False
        self._current_data: dict = {}
        self._current_style: str = "standard"
        self._rep_stash: dict[str, dict] = {}  # style → saved style-specific keys

        main_vbox = QVBoxLayout(self)
        main_vbox.setContentsMargins(0, 0, 0, 0)
        main_vbox.setSpacing(0)

        # Template Gallery Bar
        gallery_bar = QWidget()
        gallery_bar.setStyleSheet("background: #1e293b; border-bottom: 2px solid #334155;")
        gallery_layout = QHBoxLayout(gallery_bar)
        gallery_layout.setContentsMargins(12, 8, 12, 8)
        gallery_layout.setSpacing(12)

        title = QLabel("Gallery:")
        title.setStyleSheet("color: #64748b; font-weight: bold; font-size: 11px; text-transform: uppercase;")
        gallery_layout.addWidget(title)

        for category, items in TEMPLATES.items():
            btn = QPushButton(category)
            btn.setStyleSheet("""
                QPushButton { 
                    background: #f1f5f9; color: #475569; border: 1px solid #e2e8f0; 
                    padding: 6px 14px; border-radius: 6px; font-size: 12px; font-weight: 500;
                }
                QPushButton:hover { background: #e2e8f0; color: #0f172a; border-color: #cbd5e1; }
                QPushButton::menu-indicator { image: none; }
            """)
            menu = QMenu(self)
            menu.setStyleSheet("""
                QMenu { background: white; color: #334155; border: 1px solid #e2e8f0; border-radius: 6px; }
                QMenu::item { padding: 8px 24px; border-radius: 4px; }
                QMenu::item:selected { background: #6366f1; color: white; }
            """)
            
            for name, data in items.items():
                action = menu.addAction(name)
                action.triggered.connect(lambda checked=False, d=data: self.load(d))
            
            btn.setMenu(menu)
            gallery_layout.addWidget(btn)

        gallery_layout.addStretch()
        main_vbox.addWidget(gallery_bar)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        main_vbox.addWidget(scroll)

        host = QWidget()
        scroll.setWidget(host)
        root = QVBoxLayout(host)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(16)

        # ── Activation section ────────────────────────────────────────────────
        act_frame = QFrame()
        act_frame.setObjectName("sectionFrame")
        act_layout = QVBoxLayout(act_frame)
        act_layout.setContentsMargins(0, 0, 0, 0)
        act_layout.setSpacing(12)
        act_title = QLabel("ACTIVATION")
        act_title.setStyleSheet("font-size: 11px; letter-spacing: 0.1em; font-weight: 800; color: #64748b; margin-bottom: 4px;")
        act_layout.addWidget(act_title)

        self._act_form = QFormLayout()
        self._act_form.setContentsMargins(0, 0, 0, 0)
        self._act_form.setSpacing(6)

        self.family_combo = _NoWheelComboBox()
        for family in _ACTIVATION_SCHEMA:
            self.family_combo.addItem(family, family)
        
        family_lbl = QLabel("Family")
        bold_font = family_lbl.font()
        bold_font.setBold(True)
        family_lbl.setFont(bold_font)
        self._act_form.addRow(family_lbl, self._wrap_with_hint(self.family_combo, "General category of button behavior"))

        self.type_combo = _NoWheelComboBox()
        subtype_lbl = QLabel("Subtype")
        subtype_lbl.setFont(bold_font)
        self._act_form.addRow(subtype_lbl, self._wrap_with_hint(self.type_combo, "Specific behavior for this activation family"))

        self.command_edit = QLineEdit()
        self.command_edit.setPlaceholderText("sim/...")
        self._command_row = self.command_edit
        cmd_lbl = QLabel("Command")
        cmd_lbl.setFont(bold_font)
        self._act_form.addRow(cmd_lbl, self._command_row)

        self.command1_label = QLabel("On")
        self.command1_edit = QLineEdit()
        self.command1_edit.setPlaceholderText("sim/...")
        self.command2_label = QLabel("Off")
        self.command2_edit = QLineEdit()
        self.command2_edit.setPlaceholderText("sim/...")
        pair_host = QWidget()
        pair_layout = QFormLayout(pair_host)
        pair_layout.setContentsMargins(0, 0, 0, 0)
        pair_layout.setSpacing(4)
        pair_layout.addRow(self.command1_label, self.command1_edit)
        pair_layout.addRow(self.command2_label, self.command2_edit)
        self._pair_row = self._wrap_with_hint(pair_host, "Primary and secondary commands (e.g. On/Off, Short/Long)")
        self._act_form.addRow("Commands", self._pair_row)

        self.page_edit = QLineEdit()
        self.page_edit.setPlaceholderText("index")
        self._page_row = self._wrap_with_hint(self.page_edit, "Name of the page to load")
        page_lbl = QLabel("Page")
        page_lbl.setFont(bold_font)
        self._act_form.addRow(page_lbl, self._page_row)

        self.pages_edit = QLineEdit()
        self.pages_edit.setPlaceholderText("index, page2")
        self._pages_row = self._wrap_with_hint(self.pages_edit, "Comma-separated list of pages to cycle through")
        self._act_form.addRow("Pages", self._pages_row)

        # Encoder command fields
        self.enc_cw_edit = QLineEdit()
        self.enc_cw_edit.setPlaceholderText("sim/...")
        self._enc_cw_row = self.enc_cw_edit
        self._act_form.addRow("CW", self._enc_cw_row)

        self.enc_ccw_edit = QLineEdit()
        self.enc_ccw_edit.setPlaceholderText("sim/...")
        self._enc_ccw_row = self.enc_ccw_edit
        self._act_form.addRow("CCW", self._enc_ccw_row)

        self.enc_press_edit = QLineEdit()
        self.enc_press_edit.setPlaceholderText("sim/...")
        self._enc_press_row = self.enc_press_edit
        self._act_form.addRow("Press", self._enc_press_row)

        self.enc_long_press_edit = QLineEdit()
        self.enc_long_press_edit.setPlaceholderText("sim/...")
        self._enc_long_press_row = self.enc_long_press_edit
        self._act_form.addRow("Long Press", self._enc_long_press_row)

        self.enc_cw_off_edit = QLineEdit()
        self.enc_cw_off_edit.setPlaceholderText("sim/... (mode-off CW)")
        self._enc_cw_off_row = self.enc_cw_off_edit
        self._act_form.addRow("CW (off)", self._enc_cw_off_row)

        self.enc_ccw_off_edit = QLineEdit()
        self.enc_ccw_off_edit.setPlaceholderText("sim/... (mode-off CCW)")
        self._enc_ccw_off_row = self.enc_ccw_off_edit
        self._act_form.addRow("CCW (off)", self._enc_ccw_off_row)

        # Sweep positions field
        self.sweep_positions_edit = QPlainTextEdit()
        self.sweep_positions_edit.setFixedHeight(80)
        self.sweep_positions_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.sweep_positions_edit.setPlaceholderText("sim/command/pos_0\nsim/command/pos_1")
        self._sweep_positions_row = self.sweep_positions_edit
        self._act_form.addRow("Positions", self._sweep_positions_row)

        act_layout.addLayout(self._act_form)
        root.addWidget(act_frame)

        # ── Representation section ─────────────────────────────────────────────
        rep_frame = QFrame()
        rep_frame.setObjectName("sectionFrame")
        rep_layout = QVBoxLayout(rep_frame)
        rep_layout.setContentsMargins(0, 0, 0, 0)
        rep_layout.setSpacing(12)
        self.rep_title = QLabel("REPRESENTATION")
        self.rep_title.setStyleSheet("font-size: 11px; letter-spacing: 0.1em; font-weight: 800; color: #64748b; margin-top: 8px; margin-bottom: 4px;")
        rep_layout.addWidget(self.rep_title)

        self._rep_form = QFormLayout()
        self._rep_form.setContentsMargins(0, 0, 0, 0)
        self._rep_form.setSpacing(6)

        self.rep_family_combo = _NoWheelComboBox()
        for family in _REPRESENTATION_SCHEMA:
            self.rep_family_combo.addItem(family, family)
        
        rep_fam_lbl = QLabel("Family")
        rep_fam_lbl.setFont(bold_font)
        self._rep_form.addRow(rep_fam_lbl, self._wrap_with_hint(self.rep_family_combo, "General category of visual appearance"))

        self.style_combo = _NoWheelComboBox()
        rep_sub_lbl = QLabel("Subtype")
        rep_sub_lbl.setFont(bold_font)
        self._rep_form.addRow(rep_sub_lbl, self._wrap_with_hint(self.style_combo, {"hint": "Specific visual style for this representation family"}))

        # Container for categorized parameter groups
        self._dynamic_rep_container = QWidget()
        self._dynamic_rep_vbox = QVBoxLayout(self._dynamic_rep_container)
        self._dynamic_rep_vbox.setContentsMargins(0, 8, 0, 0)
        self._dynamic_rep_vbox.setSpacing(12)
        self._rep_form.addRow(self._dynamic_rep_container)
        self._dynamic_rep_row = self._dynamic_rep_container
        self._dynamic_sections: dict[str, CollapsibleSection] = {}
        self._dynamic_rep_widgets: dict[str, QWidget] = {}

        rep_layout.addLayout(self._rep_form)
        root.addWidget(rep_frame)

        self.rep_hint = QLabel("")
        self.rep_hint.setWordWrap(True)
        self.rep_hint.setStyleSheet("font-size: 10px; color: #64748b;")
        rep_layout.addWidget(self.rep_hint)

        root.addStretch(1)

        # ── Wire signals ──────────────────────────────────────────────────────
        for widget, sig in [
            (self.family_combo, "currentIndexChanged"),
            (self.type_combo, "currentIndexChanged"),
            (self.command_edit, "textChanged"),
            (self.command1_edit, "textChanged"),
            (self.command2_edit, "textChanged"),
            (self.page_edit, "textChanged"),
            (self.pages_edit, "textChanged"),
            (self.enc_cw_edit, "textChanged"),
            (self.enc_ccw_edit, "textChanged"),
            (self.enc_press_edit, "textChanged"),
            (self.enc_long_press_edit, "textChanged"),
            (self.enc_cw_off_edit, "textChanged"),
            (self.enc_ccw_off_edit, "textChanged"),
            (self.rep_family_combo, "currentIndexChanged"),
            (self.style_combo, "currentIndexChanged"),
        ]:
            getattr(widget, sig).connect(self._on_form_changed)
        self.sweep_positions_edit.textChanged.connect(self._on_form_changed)

    def _create_dynamic_rep_widget(self, field: dict, value: Any) -> QWidget:
        field_type = str(field.get("type") or "string")
        if field_type == "boolean":
            widget = QCheckBox()
            widget.setChecked(bool(value))
            widget.toggled.connect(self._on_form_changed)
            return widget
        if field_type == "choice":
            combo = _NoWheelComboBox()
            combo.setEditable(True)
            combo.addItem("", "")
            for choice in field.get("choices") or []:
                combo.addItem(str(choice), str(choice))
            combo.currentIndexChanged.connect(self._on_form_changed)
            combo.currentTextChanged.connect(self._on_form_changed)
            if value not in (None, ""):
                _set_combo(combo, str(value))
            elif field.get("default") not in (None, ""):
                _set_combo(combo, str(field.get("default")))
            return combo
        if field_type in {"sub", "selector"} or (field_type == "list" and field.get("item_fields")):
            edit = QPlainTextEdit()
            edit.setFixedHeight(90)
            edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            if value not in (None, "", [], {}):
                edit.setPlainText(yaml.safe_dump(value, sort_keys=False, allow_unicode=False).strip())
            edit.textChanged.connect(self._on_form_changed)
            return edit
        if field_type == "list":
            edit = QPlainTextEdit()
            edit.setFixedHeight(72)
            edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            if isinstance(value, list):
                edit.setPlainText("\n".join(str(item) for item in value))
            elif value not in (None, ""):
                edit.setPlainText(str(value))
            edit.textChanged.connect(self._on_form_changed)
            return edit
        edit = QLineEdit()
        edit.setPlaceholderText(str(field.get("default") or ""))
        if value not in (None, ""):
            edit.setText(str(value))
        edit.textChanged.connect(self._on_form_changed)
        return edit

    def _representation_values_from_data(self, style: str, data: dict) -> dict[str, Any]:
        rep_name = "icon-color" if style == "standard" else style
        rep_cfg = data.get("representation") if isinstance(data.get("representation"), dict) else {}
        values: dict[str, Any] = {}
        if isinstance(rep_cfg, dict):
            for key, value in rep_cfg.items():
                if key == "type":
                    continue
                if key == rep_name and isinstance(value, dict):
                    continue
                values[key] = value
            nested = rep_cfg.get(rep_name)
            if isinstance(nested, dict):
                values.update(nested)
        for key in _REPRESENTATION_ROOT_FIELDS:
            if key not in values and key in data:
                values[key] = data.get(key)
        nested_top = data.get(rep_name)
        if rep_name in _REPRESENTATION_NESTED_BLOCKS and isinstance(nested_top, dict):
            for key, value in nested_top.items():
                values.setdefault(key, value)
        return values

    def _wrap_with_hint(self, widget: QWidget, field_or_hint: dict | str) -> QWidget:
        if isinstance(field_or_hint, str):
            hint_text = field_or_hint
            sample_data = None
        else:
            hint_text = str(field_or_hint.get("hint") or "")
            sample_data = field_or_hint.get("sample")
        
        wrapper = QWidget()
        vbox = QVBoxLayout(wrapper)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(2)

        hbox = QHBoxLayout()
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(4)
        hbox.addWidget(widget)

        if sample_data:
            suggest_btn = QPushButton("?")
            suggest_btn.setToolTip(f"Suggest sample data: {sample_data}")
            suggest_btn.setFixedSize(24, 24)
            suggest_btn.setFlat(True)
            suggest_btn.setStyleSheet("QPushButton { color: #94a3b8; } QPushButton:hover { color: #fbbf24; }")
            
            def on_suggest():
                if isinstance(widget, QLineEdit):
                    widget.setText(str(sample_data))
                elif isinstance(widget, QComboBox):
                    _set_combo(widget, str(sample_data))
                elif isinstance(widget, QPlainTextEdit):
                    if isinstance(sample_data, (list, dict)):
                        widget.setPlainText(yaml.safe_dump(sample_data, sort_keys=False).strip())
                    else:
                        widget.setPlainText(str(sample_data))
            
            suggest_btn.clicked.connect(on_suggest)
            hbox.addWidget(suggest_btn)

            def update_suggest_visibility():
                is_empty = False
                if isinstance(widget, QLineEdit):
                    is_empty = not widget.text().strip()
                elif isinstance(widget, QComboBox):
                    is_empty = not widget.currentText().strip()
                elif isinstance(widget, QPlainTextEdit):
                    is_empty = not widget.toPlainText().strip()
                suggest_btn.setVisible(is_empty)

            if isinstance(widget, (QLineEdit, QPlainTextEdit)):
                widget.textChanged.connect(update_suggest_visibility)
            elif isinstance(widget, QComboBox):
                widget.currentTextChanged.connect(update_suggest_visibility)
            update_suggest_visibility()

        vbox.addLayout(hbox)
        if hint_text:
            hint = QLabel(hint_text)
            hint.setStyleSheet("font-size: 10px; color: #64748b;")
            hint.setWordWrap(True)
            vbox.addWidget(hint)
        return wrapper

    def _clear_dynamic_rep_form(self) -> None:
        while self._dynamic_rep_vbox.count():
            item = self._dynamic_rep_vbox.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._dynamic_sections.clear()
        self._dynamic_rep_widgets = {}

    def _rebuild_dynamic_rep_form(self, style: str, data: dict) -> None:
        self._clear_dynamic_rep_form()
        rep_name = "icon-color" if style == "standard" else style
        schema = _REPRESENTATION_SCHEMAS.get(rep_name)
        if not schema:
            return
            
        values = self._representation_values_from_data(rep_name, data)
        self._dynamic_rep_widgets = {}
        
        # Group fields
        fields_by_group: dict[str, list[tuple[str, dict]]] = {}
        for field_name, field in (schema.get("editor_fields") or {}).items():
            if field_name == "type":
                continue
            group = str(field.get("group") or "Parameters")
            fields_by_group.setdefault(group, []).append((field_name, field))

        # Create sections
        for group, fields in fields_by_group.items():
            section = CollapsibleSection(group)
            self._dynamic_rep_vbox.addWidget(section)
            self._dynamic_sections[group] = section
            
            for field_name, field in fields:
                widget = self._create_dynamic_rep_widget(field, values.get(field_name))
                self._dynamic_rep_widgets[field_name] = widget
                
                wrapped = self._wrap_with_hint(widget, field)
                label_text = str(field.get("label") or field_name)
                
                label = QLabel(label_text)
                if field.get("required"):
                    font = label.font()
                    font.setBold(True)
                    label.setFont(font)
                
                section.content_layout.addRow(label, wrapped)

        self._update_group_visibilities()

    def _update_group_visibilities(self) -> None:
        for group, section in self._dynamic_sections.items():
            section.setVisible(section.content_layout.rowCount() > 0)

    def _on_gallery_template_selected(self, data: dict) -> None:
        self.load(data)

    def _dynamic_rep_value(self, field_name: str, field: dict) -> Any:
        widget = self._dynamic_rep_widgets.get(field_name)
        if widget is None:
            return None
        field_type = str(field.get("type") or "string")
        if field_type == "boolean":
            return widget.isChecked() or None
        if field_type == "choice":
            text = widget.currentText().strip()
            return text or None
        if field_type in {"sub", "selector"} or (field_type == "list" and field.get("item_fields")):
            text = widget.toPlainText().strip()
            if not text:
                return None
            try:
                return yaml.safe_load(text)
            except Exception:
                return text
        if field_type == "list":
            text = widget.toPlainText().strip()
            if not text:
                return None
            return [line.strip() for line in text.splitlines() if line.strip()]
        text = widget.text().strip()
        if not text:
            return None
        if field_type == "integer":
            try:
                return int(text)
            except ValueError:
                return text
        if field_type == "float":
            try:
                return float(text)
            except ValueError:
                return text
        return text

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self, data: dict) -> None:
        """Populate all form fields from a button config dict."""
        self._current_data = dict(data)
        self._syncing = True
        try:
            activation_cfg = data.get("activation") if isinstance(data.get("activation"), dict) else {}
            representation_cfg = data.get("representation") if isinstance(data.get("representation"), dict) else {}

            action_type = str(activation_cfg.get("type") or data.get("activation") or "push")
            family = _activation_family_for_type(action_type)
            _set_combo(self.family_combo, family)
            self._populate_activation_subtypes(action_type)

            rep_payload_ann = representation_cfg.get("annunciator") if isinstance(representation_cfg.get("annunciator"), dict) else None
            rep_payload_gauge = representation_cfg.get("gauge") if isinstance(representation_cfg.get("gauge"), dict) else None
            ann = rep_payload_ann or (representation_cfg if str(representation_cfg.get("type") or "") == "annunciator" else data.get("annunciator"))
            gauge = rep_payload_gauge or (representation_cfg if str(representation_cfg.get("type") or "") == "gauge" else data.get("gauge"))
            rep = str(representation_cfg.get("type") or data.get("representation") or "").strip()
            disp = data.get("display") or {}
            if not isinstance(disp, dict):
                disp = {}
            if rep == "side-display" or bool(disp):
                style = "side-display"
            elif isinstance(ann, dict):
                style = "annunciator"
            elif isinstance(gauge, dict):
                style = "gauge"
            elif rep in _known_representation_styles():
                style = rep
            else:
                style = "standard"
            rep_family = _representation_family_for_style(style)
            _set_combo(self.rep_family_combo, rep_family)
            self._populate_representation_subtypes(style)
            self._rebuild_dynamic_rep_form(style, data)

            cmds = activation_cfg.get("commands") if isinstance(activation_cfg.get("commands"), dict) else data.get("commands") or {}
            self.command_edit.setText(str(cmds.get("press") or ""))
            pair_fields = _two_command_fields(action_type)
            self.command1_edit.setText(str(cmds.get(pair_fields[0]) or "") if pair_fields else "")
            self.command2_edit.setText(str(cmds.get(pair_fields[1]) or "") if pair_fields else "")
            self.enc_cw_edit.setText(str(cmds.get("cw") or ""))
            self.enc_ccw_edit.setText(str(cmds.get("ccw") or ""))
            self.enc_press_edit.setText(str(cmds.get("press") or ""))
            self.enc_long_press_edit.setText(str(cmds.get("long-press") or ""))
            self.enc_cw_off_edit.setText(str(cmds.get("cw-off") or ""))
            self.enc_ccw_off_edit.setText(str(cmds.get("ccw-off") or ""))
            self.page_edit.setText(str(activation_cfg.get("page") or data.get("page") or ""))
            pages = activation_cfg.get("pages") if isinstance(activation_cfg.get("pages"), list) else data.get("pages") if isinstance(data.get("pages"), list) else []
            self.pages_edit.setText(", ".join(str(p).strip() for p in pages if str(p).strip()))
            positions = activation_cfg.get("positions") if isinstance(activation_cfg.get("positions"), list) else []
            self.sweep_positions_edit.setPlainText("\n".join(str(p) for p in positions))
        finally:
            self._syncing = False
        self._current_style = style
        self._update_visibility()

    def clear_stash(self) -> None:
        """Discard saved per-representation data (call when loading a new button)."""
        self._rep_stash.clear()
        self._current_style = "standard"

    def populate_fonts(self, font_names: list[str]) -> None:
        """Reload font combos with available font names (call after target is set)."""
        pass

    # ── Internal ──────────────────────────────────────────────────────────────

    def _populate_activation_subtypes(self, selected: str | None = None) -> None:
        family = str(self.family_combo.currentData() or "Push Button")
        self.type_combo.blockSignals(True)
        self.type_combo.clear()
        for name, label in _ACTIVATION_SCHEMA.get(family, []):
            self.type_combo.addItem(label, name)
        self.type_combo.blockSignals(False)
        _set_combo(self.type_combo, selected or _ACTIVATION_SCHEMA.get(family, [("push", "")])[0][0])

    def _populate_representation_subtypes(self, selected: str | None = None) -> None:
        family = str(self.rep_family_combo.currentData() or "Basic")
        self.style_combo.blockSignals(True)
        self.style_combo.clear()
        for name, label in _REPRESENTATION_SCHEMA.get(family, []):
            self.style_combo.addItem(label, name)
        self.style_combo.blockSignals(False)
        _set_combo(self.style_combo, selected or _REPRESENTATION_SCHEMA.get(family, [("standard", "")])[0][0])

    def _on_form_changed(self) -> None:
        if self._syncing:
            return
        data = self._collect()
        self.form_changed.emit(yaml.safe_dump(data, sort_keys=False, allow_unicode=False))

    def _update_visibility(self) -> None:
        action_type = str(self.type_combo.currentData() or "push")
        style = str(self.style_combo.currentData() or "standard")
        
        is_page = action_type in {"page", "page-cycle"}
        is_page_cycle = action_type == "page-cycle"
        is_command_like = action_type not in {"none", "page", "page-cycle", "swipe", "encoder", "encoder-push", "encoder-mode", "encoder-toggle", "encoder-value", "encoder-value-extended"}
        is_two_command = action_type in {"encoder-toggle", "short-or-long-press"}
        is_encoder = action_type in {"encoder", "encoder-push", "encoder-mode", "encoder-toggle", "encoder-value", "encoder-value-extended"}
        is_encoder_push = action_type in {"encoder-push", "encoder-value", "encoder-value-extended"}
        is_encoder_mode = action_type == "encoder-mode"
        is_sweep = action_type == "sweep"

        _set_form_row_visible(self._act_form, self._command_row, is_command_like)
        _set_form_row_visible(self._act_form, self._pair_row, is_two_command)
        _set_form_row_visible(self._act_form, self._page_row, is_page)
        _set_form_row_visible(self._act_form, self._pages_row, is_page_cycle)
        _set_form_row_visible(self._act_form, self._enc_cw_row, is_encoder)
        _set_form_row_visible(self._act_form, self._enc_ccw_row, is_encoder)
        _set_form_row_visible(self._act_form, self._enc_press_row, is_encoder_push)
        _set_form_row_visible(self._act_form, self._enc_long_press_row, is_encoder_push or is_encoder_mode)
        _set_form_row_visible(self._act_form, self._enc_cw_off_row, is_encoder_mode)
        _set_form_row_visible(self._act_form, self._enc_ccw_off_row, is_encoder_mode)
        _set_form_row_visible(self._act_form, self._sweep_positions_row, is_sweep)

        if action_type == "encoder-toggle":
            self.command1_label.setText("On")
            self.command2_label.setText("Off")
        elif action_type == "short-or-long-press":
            self.command1_label.setText("Short")
            self.command2_label.setText("Long")

        _set_form_row_visible(self._rep_form, self._dynamic_rep_row, True)
        self.rep_title.setText("Representation")
        schema = _REPRESENTATION_SCHEMAS.get("icon-color" if style == "standard" else style, {})
        self.rep_hint.setText(str(schema.get("hint") or ""))

    def _collect(self) -> dict:
        """Read all form fields into a data dict (preserving unrecognised keys)."""
        sender = self.sender()
        data = {
            key: value
            for key, value in dict(self._current_data).items()
            if key not in {
                "activation",
                "representation",
                "commands",
                "page",
                "pages",
                "positions",
                "deck",
                "label",
                "label-size",
                "label-color",
                "text",
                "text-size",
                "text-color",
                "text-format",
                "formula",
                "annunciator",
                "gauge",
                "display",
            }
        }

        if sender is self.family_combo:
            self._populate_activation_subtypes()
        elif sender is self.rep_family_combo:
            self._populate_representation_subtypes()
            style = str(self.style_combo.currentData() or "standard")
            self._rebuild_dynamic_rep_form(style, self._current_data)
        elif sender is self.style_combo:
            style = str(self.style_combo.currentData() or "standard")
            self._rebuild_dynamic_rep_form(style, self._current_data)
        self._update_visibility()

        action_type = str(self.type_combo.currentData() or "push")
        style = str(self.style_combo.currentData() or "standard")

        # Commands
        is_encoder = action_type in {"encoder", "encoder-push", "encoder-mode"}
        cmds: dict = {}
        pair_fields = _two_command_fields(action_type)
        if is_encoder:
            for key, edit in [
                ("cw", self.enc_cw_edit), ("ccw", self.enc_ccw_edit),
                ("press", self.enc_press_edit), ("long-press", self.enc_long_press_edit),
                ("cw-off", self.enc_cw_off_edit), ("ccw-off", self.enc_ccw_off_edit),
            ]:
                val = edit.text().strip()
                if val:
                    cmds[key] = val
        elif pair_fields is not None:
            c1 = self.command1_edit.text().strip()
            c2 = self.command2_edit.text().strip()
            if c1:
                cmds[pair_fields[0]] = c1
            if c2:
                cmds[pair_fields[1]] = c2
        else:
            press = self.command_edit.text().strip()
            if press:
                cmds["press"] = press
        if not cmds:
            cmds = {}

        # Activation
        current_activation = self._current_data.get("activation") if isinstance(self._current_data.get("activation"), dict) else {}
        _managed_act_keys = {"type", "commands", "page", "pages", "positions"}
        activation_obj: dict = {k: v for k, v in current_activation.items() if k not in _managed_act_keys}
        activation_obj["type"] = action_type
        if cmds:
            activation_obj["commands"] = cmds
        
        p = self.page_edit.text().strip()
        if p: activation_obj["page"] = p
        
        pages = [p.strip() for p in self.pages_edit.text().split(",") if p.strip()]
        if pages: activation_obj["pages"] = pages
        
        if action_type == "sweep":
            activation_obj["positions"] = [ln.strip() for ln in self.sweep_positions_edit.toPlainText().splitlines() if ln.strip()]
            
        data["activation"] = activation_obj

        # Representation — Dynamic fields
        current_representation = self._current_data.get("representation") if isinstance(self._current_data.get("representation"), dict) else {}
        rep_type = "icon-color" if style == "standard" else style
        schema = _REPRESENTATION_SCHEMAS.get(rep_type, {})
        schema_fields = set((schema.get("editor_fields") or {}).keys()) - {"type"}
        managed_root_keys = {"type", rep_type} | schema_fields | _REPRESENTATION_NESTED_BLOCKS
        representation_obj: dict = {
            key: value for key, value in current_representation.items() if key not in managed_root_keys
        }
        representation_obj["type"] = rep_type
        
        for field_name, field in (schema.get("editor_fields") or {}).items():
            if field_name == "type":
                continue
            val = self._dynamic_rep_value(field_name, field)
            if val is not None:
                storage = str(field.get("storage_mode") or "flat")
                if storage == "nested_block":
                    representation_obj.setdefault(rep_type, {})[field_name] = val
                else:
                    representation_obj[field_name] = val
                    
        data["representation"] = representation_obj
        return data
