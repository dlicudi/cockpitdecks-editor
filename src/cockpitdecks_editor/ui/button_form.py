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


# Priority order for representation parameter group headers.
# Groups not listed here fall back to a high sort key (appear last).
_GROUP_ORDER: dict[str, int] = {
    "Style": 0,
    "Identification": 1,
    "Display": 2,
    "Visuals": 3,
    "Positions": 4,
    "Appearance": 5,
    "Ticks": 6,
    "Labels": 7,
    "Needle": 8,
    "Colors": 9,
    "Logic": 10,
    "Execution": 11,
    "Effects": 12,
    "Parameters": 98,
}


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

# Keys stripped from the top-level data dict before re-serialising in _collect().
# Any key managed by the form (activation block, representation block, legacy
# top-level shorthand fields) must be listed here so stale data is not carried
# forward when the user changes activation type or representation style.
_COLLECT_MANAGED_ROOT_KEYS: frozenset[str] = frozenset({
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
})

# Keys managed inside the activation block (type overrides, command routing, navigation).
_COLLECT_MANAGED_ACT_KEYS: frozenset[str] = frozenset({
    "type", "commands", "page", "pages", "positions", "step", "minimum-distance",
    "set-dataref", "value-min", "value-max", "value-step",
})

# ── Annunciator parts widget ─────────────────────────────────────────────────

_ANN_LED_CHOICES = [
    ("", "None (text only)"),
    ("bar", "bar"), ("bars", "bars"), ("block", "block"),
    ("dot", "dot"), ("lgear", "lgear"), ("led", "led"),
]


class AnnunciatorPartsWidget(QWidget):
    """Structured editor for a list of annunciator parts (one group per part)."""

    changed = Signal()

    def __init__(self, model: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._loading = False
        self._rows: list[dict] = []
        part_ids = _ANNUNCIATOR_PART_IDS.get(model, [f"{model}0"])

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        for idx, part_id in enumerate(part_ids):
            frame = QFrame()
            frame.setFrameShape(QFrame.Shape.StyledPanel)
            frame.setStyleSheet("QFrame { border: 1px solid #e2e8f0; border-radius: 4px; }")
            fl = QFormLayout(frame)
            fl.setContentsMargins(8, 6, 8, 6)
            fl.setSpacing(4)
            fl.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            header = QLabel(f"Part {part_id}")
            header.setStyleSheet("font-size: 10px; font-weight: 700; color: #64748b; border: none;")
            fl.addRow(header)

            text_edit = QLineEdit()
            text_edit.setPlaceholderText("${formula}")
            fl.addRow("Text", text_edit)

            formula_edit = QLineEdit()
            formula_edit.setPlaceholderText("${sim/...} ...")
            fl.addRow("Formula", formula_edit)

            fmt_edit = QLineEdit()
            fmt_edit.setPlaceholderText("{0:.0f}")
            fl.addRow("Format", fmt_edit)

            font_combo = _NoWheelComboBox()
            font_combo.setEditable(True)
            font_combo.addItem("(default)", "")
            font_combo.lineEdit().setPlaceholderText("font name")

            size_spin = _NoWheelSpinBox()
            size_spin.setRange(0, 256)
            size_spin.setSpecialValueText("Default")
            size_spin.setFixedWidth(70)

            font_size_row = QWidget()
            font_size_row.setStyleSheet("border: none;")
            fsl = QHBoxLayout(font_size_row)
            fsl.setContentsMargins(0, 0, 0, 0)
            fsl.setSpacing(4)
            fsl.addWidget(font_combo, 1)
            fsl.addWidget(QLabel("Size"))
            fsl.addWidget(size_spin)
            fl.addRow("Font", font_size_row)

            color_edit = QLineEdit()
            color_edit.setPlaceholderText("lime, orange, #ff0000…")
            fl.addRow("Color", color_edit)

            led_combo = _NoWheelComboBox()
            for val, label in _ANN_LED_CHOICES:
                led_combo.addItem(label, val)
            fl.addRow("LED", led_combo)

            framed_check = QCheckBox()
            fl.addRow("Framed", framed_check)

            outer.addWidget(frame)
            row = {
                "text_edit": text_edit,
                "formula_edit": formula_edit,
                "fmt_edit": fmt_edit,
                "font_combo": font_combo,
                "size_spin": size_spin,
                "color_edit": color_edit,
                "led_combo": led_combo,
                "framed_check": framed_check,
            }
            self._rows.append(row)

            for w in (text_edit, formula_edit, fmt_edit, color_edit):
                w.textChanged.connect(self._emit)
            font_combo.currentTextChanged.connect(self._emit)
            size_spin.valueChanged.connect(self._emit)
            led_combo.currentIndexChanged.connect(self._emit)
            framed_check.stateChanged.connect(self._emit)

    def _emit(self) -> None:
        if not self._loading:
            self.changed.emit()

    def populate_fonts(self, font_names: list[str]) -> None:
        for row in self._rows:
            combo = row["font_combo"]
            current = combo.currentText().strip()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("(default)", "")
            for name in font_names:
                combo.addItem(name, name)
            _set_combo(combo, current)
            combo.blockSignals(False)

    def load(self, parts_list: list) -> None:
        self._loading = True
        try:
            for idx, row in enumerate(self._rows):
                part = parts_list[idx] if idx < len(parts_list) else {}
                if not isinstance(part, dict):
                    part = {}
                row["text_edit"].setText(str(part.get("text") or ""))
                row["formula_edit"].setText(str(part.get("formula") or ""))
                row["fmt_edit"].setText(str(part.get("text-format") or ""))
                _set_combo(row["font_combo"], str(part.get("text-font") or ""))
                row["size_spin"].setValue(int(part.get("text-size") or 0))
                row["color_edit"].setText(str(part.get("color") or ""))
                _set_combo(row["led_combo"], str(part.get("led") or ""))
                row["framed_check"].setChecked(bool(part.get("framed", False)))
        finally:
            self._loading = False

    def collect(self) -> list | None:
        result = []
        for row in self._rows:
            part: dict = {}
            text = row["text_edit"].text().strip()
            formula = row["formula_edit"].text().strip()
            fmt = row["fmt_edit"].text().strip()
            font = row["font_combo"].currentText().strip()
            size = row["size_spin"].value()
            color = row["color_edit"].text().strip()
            led = str(row["led_combo"].currentData() or "").strip()
            framed = row["framed_check"].isChecked()
            if text:
                part["text"] = text
            if formula:
                part["formula"] = formula
            if fmt:
                part["text-format"] = fmt
            if font and font not in ("", "(default)"):
                part["text-font"] = font
            if size > 0:
                part["text-size"] = size
            if color:
                part["color"] = color
            if led:
                part["led"] = led
            if framed:
                part["framed"] = True
            if part:
                result.append(part)
        return result or None


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

        # Swipe-specific fields
        self.swipe_step_spin = _NoWheelSpinBox()
        self.swipe_step_spin.setRange(1, 9999)
        self.swipe_step_spin.setValue(50)
        self.swipe_step_spin.setToolTip("Pixels of swipe per command repeat (default 50)")
        self._swipe_step_row = self.swipe_step_spin
        self._act_form.addRow("Step (px)", self._swipe_step_row)

        self.swipe_min_distance_spin = _NoWheelSpinBox()
        self.swipe_min_distance_spin.setRange(1, 9999)
        self.swipe_min_distance_spin.setValue(20)
        self.swipe_min_distance_spin.setToolTip("Minimum swipe distance in pixels to trigger a command (default 20)")
        self._swipe_min_distance_row = self.swipe_min_distance_spin
        self._act_form.addRow("Min Distance (px)", self._swipe_min_distance_row)

        # Slider-specific fields
        self.slider_dataref_edit = QLineEdit()
        self.slider_dataref_edit.setPlaceholderText("sim/...")
        self.slider_dataref_edit.setToolTip("Dataref to write when this activation fires")
        self._slider_dataref_row = self.slider_dataref_edit
        self._act_form.addRow("Set Dataref", self._slider_dataref_row)

        self.slider_min_edit = QLineEdit()
        self.slider_min_edit.setPlaceholderText("0")
        self.slider_min_edit.setToolTip("Value written when slider is at its minimum position")
        self._slider_min_row = self.slider_min_edit
        self._act_form.addRow("Value Min", self._slider_min_row)

        self.slider_max_edit = QLineEdit()
        self.slider_max_edit.setPlaceholderText("1")
        self.slider_max_edit.setToolTip("Value written when slider is at its maximum position")
        self._slider_max_row = self.slider_max_edit
        self._act_form.addRow("Value Max", self._slider_max_row)

        self.slider_step_edit = QLineEdit()
        self.slider_step_edit.setPlaceholderText("0 (continuous)")
        self.slider_step_edit.setToolTip("Snap step size (0 = continuous, no snapping)")
        self._slider_step_row = self.slider_step_edit
        self._act_form.addRow("Step", self._slider_step_row)

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

        # Container for dynamic representation parameter fields
        self._dynamic_rep_container = QWidget()
        self._dynamic_rep_vbox = QVBoxLayout(self._dynamic_rep_container)
        self._dynamic_rep_vbox.setContentsMargins(0, 8, 0, 0)
        self._dynamic_rep_vbox.setSpacing(0)
        self._rep_form.addRow(self._dynamic_rep_container)
        self._dynamic_rep_row = self._dynamic_rep_container
        self._dynamic_rep_widgets: dict[str, QWidget] = {}
        self._available_fonts: list[str] = []

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
        self.swipe_step_spin.valueChanged.connect(self._on_form_changed)
        self.swipe_min_distance_spin.valueChanged.connect(self._on_form_changed)
        self.slider_dataref_edit.textChanged.connect(self._on_form_changed)
        self.slider_min_edit.textChanged.connect(self._on_form_changed)
        self.slider_max_edit.textChanged.connect(self._on_form_changed)
        self.slider_step_edit.textChanged.connect(self._on_form_changed)

    def _create_dynamic_rep_widget(self, field: dict, value: Any) -> QWidget:
        field_type = str(field.get("type") or "string")
        if field_type == "font":
            combo = _NoWheelComboBox()
            combo.setEditable(True)
            combo.addItem("(default)", "")
            combo.lineEdit().setPlaceholderText("font name")
            for name in self._available_fonts:
                combo.addItem(name, name)
            if value not in (None, ""):
                _set_combo(combo, str(value))
            combo.currentIndexChanged.connect(self._on_form_changed)
            combo.lineEdit().textEdited.connect(self._on_form_changed)
            return combo
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
        self._dynamic_rep_widgets = {}

    def _rebuild_dynamic_rep_form(self, style: str, data: dict) -> None:
        self._clear_dynamic_rep_form()
        rep_name = "icon-color" if style == "standard" else style
        schema = _REPRESENTATION_SCHEMAS.get(rep_name)
        if not schema:
            return

        values = self._representation_values_from_data(rep_name, data)
        self._dynamic_rep_widgets = {}

        # Collect and sort fields by group priority
        fields_by_group: dict[str, list[tuple[str, dict]]] = {}
        for field_name, field in (schema.get("editor_fields") or {}).items():
            if field_name == "type":
                continue
            group = str(field.get("group") or "Parameters")
            fields_by_group.setdefault(group, []).append((field_name, field))

        if not fields_by_group:
            return

        sorted_groups = sorted(
            fields_by_group.keys(),
            key=lambda g: (_GROUP_ORDER.get(g, 99), g),
        )

        # Single flat QFormLayout matching the Activation / Representation style above,
        # with lightweight uppercase separator labels between groups.
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        container = QWidget()
        container.setLayout(form)
        self._dynamic_rep_vbox.addWidget(container)

        for i, group in enumerate(sorted_groups):
            sep = QLabel(group.upper())
            sep.setStyleSheet(
                ("margin-top: 10px; " if i > 0 else "") +
                "font-size: 10px; letter-spacing: 0.08em; font-weight: 700; "
                "color: #94a3b8; padding-bottom: 2px; border-bottom: 1px solid #e2e8f0;"
            )
            form.addRow(sep)

            for field_name, field in fields_by_group[group]:
                # Annunciator parts get a structured multi-row widget, not a raw text area.
                if rep_name == "annunciator" and field_name == "parts":
                    rep_cfg = data.get("representation") if isinstance(data.get("representation"), dict) else {}
                    ann_cfg = rep_cfg.get("annunciator") if isinstance(rep_cfg.get("annunciator"), dict) else {}
                    model = str(ann_cfg.get("model") or "A")
                    widget = AnnunciatorPartsWidget(model)
                    parts_val = values.get("parts")
                    if isinstance(parts_val, list):
                        widget.load(parts_val)
                    widget.changed.connect(self._on_form_changed)
                    self._dynamic_rep_widgets["parts"] = widget
                    form.addRow(widget)
                    continue

                widget = self._create_dynamic_rep_widget(field, values.get(field_name))
                self._dynamic_rep_widgets[field_name] = widget

                wrapped = self._wrap_with_hint(widget, field)
                label_text = str(field.get("label") or field_name)
                label = QLabel(label_text)
                if field.get("required"):
                    font = label.font()
                    font.setBold(True)
                    label.setFont(font)
                form.addRow(label, wrapped)

    def _on_gallery_template_selected(self, data: dict) -> None:
        self.load(data)

    def _dynamic_rep_value(self, field_name: str, field: dict) -> Any:
        widget = self._dynamic_rep_widgets.get(field_name)
        if widget is None:
            return None
        if isinstance(widget, AnnunciatorPartsWidget):
            return widget.collect()
        field_type = str(field.get("type") or "string")
        if field_type == "boolean":
            return True if widget.isChecked() else None
        if field_type in {"choice", "font"}:
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
            try:
                self.swipe_step_spin.setValue(max(1, int(activation_cfg.get("step") or 50)))
            except (ValueError, TypeError):
                self.swipe_step_spin.setValue(50)
            try:
                self.swipe_min_distance_spin.setValue(max(1, int(activation_cfg.get("minimum-distance") or 20)))
            except (ValueError, TypeError):
                self.swipe_min_distance_spin.setValue(20)
            self.slider_dataref_edit.setText(str(activation_cfg.get("set-dataref") or ""))
            self.slider_min_edit.setText(str(activation_cfg.get("value-min") if activation_cfg.get("value-min") is not None else "0"))
            self.slider_max_edit.setText(str(activation_cfg.get("value-max") if activation_cfg.get("value-max") is not None else "1"))
            step_val = activation_cfg.get("step") if action_type == "push-value" else activation_cfg.get("value-step")
            self.slider_step_edit.setText(str(step_val or ""))
        finally:
            self._syncing = False
        self._update_visibility()

    def populate_fonts(self, font_names: list[str]) -> None:
        """Reload font combos with available font names (call after target is set)."""
        self._available_fonts = list(font_names)
        # Refresh font-type fields in the dynamic representation form
        for widget in self._dynamic_rep_widgets.values():
            if isinstance(widget, QComboBox):
                # Only refresh combos that were created as font pickers
                # (identified by having a "(default)" placeholder item)
                if widget.count() > 0 and widget.itemText(0) == "(default)":
                    current = widget.currentText().strip()
                    widget.blockSignals(True)
                    widget.clear()
                    widget.addItem("(default)", "")
                    for name in font_names:
                        widget.addItem(name, name)
                    _set_combo(widget, current)
                    widget.blockSignals(False)
        # Refresh annunciator parts font combos
        parts_widget = self._dynamic_rep_widgets.get("parts")
        if isinstance(parts_widget, AnnunciatorPartsWidget):
            parts_widget.populate_fonts(font_names)

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

        # When the annunciator model field changes, the parts widget must be
        # rebuilt to match the new model's part count.  Detect this before the
        # normal _collect() so the rebuilt widget is already in place.
        style = str(self.style_combo.currentData() or "standard")
        if style == "annunciator":
            model_widget = self._dynamic_rep_widgets.get("model")
            if model_widget is not None:
                if isinstance(model_widget, QComboBox):
                    new_model = str(model_widget.currentData() or model_widget.currentText()).strip()
                elif isinstance(model_widget, QLineEdit):
                    new_model = model_widget.text().strip()
                else:
                    new_model = ""
                if new_model:
                    rep_cfg = self._current_data.get("representation") if isinstance(self._current_data.get("representation"), dict) else {}
                    ann_cfg = rep_cfg.get("annunciator") if isinstance(rep_cfg.get("annunciator"), dict) else {}
                    old_model = str(ann_cfg.get("model") or "A")
                    if new_model != old_model:
                        # Preserve any edits already made to the parts widget
                        existing_parts_widget = self._dynamic_rep_widgets.get("parts")
                        current_parts = existing_parts_widget.collect() if isinstance(existing_parts_widget, AnnunciatorPartsWidget) else None

                        updated_data = dict(self._current_data)
                        updated_rep = dict(rep_cfg)
                        updated_ann = dict(ann_cfg)
                        updated_ann["model"] = new_model
                        if current_parts is not None:
                            updated_ann["parts"] = current_parts
                        updated_rep["annunciator"] = updated_ann
                        updated_data["representation"] = updated_rep
                        self._current_data = updated_data

                        self._syncing = True
                        try:
                            self._rebuild_dynamic_rep_form(style, updated_data)
                        finally:
                            self._syncing = False

        data = self._collect()
        self.form_changed.emit(yaml.safe_dump(data, sort_keys=False, allow_unicode=False))

    def _update_visibility(self) -> None:
        action_type = str(self.type_combo.currentData() or "push")
        style = str(self.style_combo.currentData() or "standard")
        
        is_page = action_type in {"page", "page-cycle"}
        is_page_cycle = action_type == "page-cycle"
        is_command_like = action_type not in {"none", "page", "page-cycle", "swipe", "push-value", "encoder", "encoder-push", "encoder-mode", "encoder-toggle", "encoder-value", "encoder-value-extended"}
        is_two_command = action_type in {"encoder-toggle", "short-or-long-press", "swipe"}
        is_swipe = action_type == "swipe"
        is_encoder = action_type in {"encoder", "encoder-push", "encoder-mode", "encoder-toggle", "encoder-value", "encoder-value-extended"}
        is_encoder_push = action_type in {"encoder-push", "encoder-value", "encoder-value-extended"}
        is_encoder_mode = action_type == "encoder-mode"
        is_sweep = action_type == "sweep"
        is_slider = action_type == "slider"

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
        _set_form_row_visible(self._act_form, self._swipe_step_row, is_swipe)
        _set_form_row_visible(self._act_form, self._swipe_min_distance_row, is_swipe)
        is_push_value = action_type == "push-value"
        _set_form_row_visible(self._act_form, self._slider_dataref_row, is_slider or is_push_value)
        _set_form_row_visible(self._act_form, self._slider_min_row, is_slider or is_push_value)
        _set_form_row_visible(self._act_form, self._slider_max_row, is_slider or is_push_value)
        _set_form_row_visible(self._act_form, self._slider_step_row, is_slider or is_push_value)

        if is_push_value:
            self.slider_step_edit.setPlaceholderText("1")
            self.slider_max_edit.setPlaceholderText("1")
        elif is_slider:
            self.slider_step_edit.setPlaceholderText("0 (continuous)")
            self.slider_max_edit.setPlaceholderText("1")

        if action_type == "encoder-toggle":
            self.command1_label.setText("On")
            self.command2_label.setText("Off")
        elif action_type == "short-or-long-press":
            self.command1_label.setText("Short")
            self.command2_label.setText("Long")
        elif action_type == "swipe":
            self.command1_label.setText("Up / Left")
            self.command2_label.setText("Down / Right")

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
            if key not in _COLLECT_MANAGED_ROOT_KEYS
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
        activation_obj: dict = {k: v for k, v in current_activation.items() if k not in _COLLECT_MANAGED_ACT_KEYS}
        activation_obj["type"] = action_type
        if cmds:
            activation_obj["commands"] = cmds
        
        p = self.page_edit.text().strip()
        if p: activation_obj["page"] = p
        
        pages = [p.strip() for p in self.pages_edit.text().split(",") if p.strip()]
        if pages: activation_obj["pages"] = pages
        
        if action_type == "sweep":
            activation_obj["positions"] = [ln.strip() for ln in self.sweep_positions_edit.toPlainText().splitlines() if ln.strip()]
        if action_type == "swipe":
            activation_obj["step"] = self.swipe_step_spin.value()
            activation_obj["minimum-distance"] = self.swipe_min_distance_spin.value()
        if action_type in {"slider", "push-value"}:
            dr = self.slider_dataref_edit.text().strip()
            if dr:
                activation_obj["set-dataref"] = dr
            for key, edit in [("value-min", self.slider_min_edit), ("value-max", self.slider_max_edit)]:
                raw = edit.text().strip()
                if raw:
                    try:
                        activation_obj[key] = float(raw)
                    except ValueError:
                        activation_obj[key] = raw
            step_raw = self.slider_step_edit.text().strip()
            step_key = "step" if action_type == "push-value" else "value-step"
            if step_raw:
                try:
                    activation_obj[step_key] = float(step_raw)
                except ValueError:
                    pass

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

        # For nested_block representations, seed the nested sub-dict with existing data so that
        # unmanaged keys (e.g. "size", "model" inside "annunciator") are not silently dropped.
        if rep_type in _REPRESENTATION_NESTED_BLOCKS:
            existing_nested = current_representation.get(rep_type)
            if isinstance(existing_nested, dict):
                representation_obj.setdefault(rep_type, {}).update(
                    {k: v for k, v in existing_nested.items()}
                )

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
