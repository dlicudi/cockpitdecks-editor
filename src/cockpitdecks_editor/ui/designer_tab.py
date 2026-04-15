"""Standalone button designer tab.

Three-pane layout: Visual form | YAML editor | Preview + controls.
Start from a preset, edit via the form or raw YAML (both stay in sync),
inject fake dataref values to simulate states, copy when done.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

import yaml
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from cockpitdecks_editor.ui.button_form import ButtonFormWidget
from cockpitdecks_editor.ui.editor_tab import _render_preview_with_fallback
from cockpitdecks_editor.ui.syntax import KeyValueHighlighter, YamlHighlighter


_DATAREF_TOKEN_RE = re.compile(r"\$\{([^}]+)\}")
_DATAREF_KEYS = {"dataref", "set-dataref"}


def _extract_datarefs_from_button(data: dict) -> list[str]:
    """Return deduplicated list of dataref names referenced in a button config."""
    found: list[str] = []

    def _scan(obj: Any) -> None:
        if isinstance(obj, str):
            for m in _DATAREF_TOKEN_RE.finditer(obj):
                found.append(m.group(1))
        elif isinstance(obj, dict):
            for k, v in obj.items():
                if k in _DATAREF_KEYS and isinstance(v, str) and v.strip():
                    found.append(v.strip())
                else:
                    _scan(v)
        elif isinstance(obj, list):
            for item in obj:
                _scan(item)

    _scan(data)
    return list(dict.fromkeys(found))


def _validate_button_config(data: dict, target_root: Any, deck_name: str) -> str | None:
    """Return a human-readable error string for obvious config problems, or None if OK."""
    rep_cfg = data.get("representation")
    rep = str(rep_cfg.get("type") or "").strip() if isinstance(rep_cfg, dict) else str(rep_cfg or "").strip()
    if not rep:
        return (
            "Missing 'representation.type' value.\n"
            "Set a representation object such as:\n"
            "representation:\n"
            "  type: icon-color"
        )
    # Validate against the cockpitdecks representation pool if available
    if target_root and deck_name:
        try:
            from cockpitdecks_editor.services.native_preview import _get_pool
            from pathlib import Path
            pool = _get_pool(Path(target_root).expanduser().resolve())
            ctx = pool.primary()
            known = set(ctx.cockpit.all_representations.keys())
            if known and rep not in known:
                close = sorted(r for r in known if r.startswith(rep[:3])) or sorted(known)[:6]
                return (
                    f"Unknown representation '{rep}'.\n"
                    f"Did you mean: {', '.join(close[:6])}?"
                )
        except Exception:
            pass  # pool not ready yet — skip validation
    return None


def _friendly_preview_error(error: str) -> str:
    """Rewrite cockpitdecks internal error messages into user-readable form."""
    if not error:
        return "No preview available."
    low = error.lower()
    if "representation is not an image" in low:
        return (
            "Representation does not produce an image.\n"
            "Check that 'representation:' is set to an image-based type "
            "(e.g. icon-color, text, annunciator, gauge)."
        )
    if "button not created" in low:
        lines = [l for l in error.splitlines() if l.strip()]
        detail = "\n".join(lines[1:]) if len(lines) > 1 else ""
        msg = "Button could not be created."
        if detail:
            msg += f"\n\n{detail}"
        return msg
    return error


def _parse_fake_datarefs(text: str) -> dict[str, Any]:
    """Parse a block of 'dataref = value' lines into a dict."""
    result: dict[str, Any] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        name, _, raw_val = line.partition("=")
        name = name.strip()
        raw_val = raw_val.strip()
        if not name:
            continue
        try:
            result[name] = float(raw_val)
        except ValueError:
            result[name] = raw_val
    return result


class _DefaultsDialog(QDialog):
    """Dialog for viewing and editing layout and page-level defaults that feed
    into the preview renderer.  Two tabs — one for the deck layout config.yaml,
    one for the page file's top-level keys — both editable as raw YAML."""

    def __init__(
        self,
        layout_yaml: str,
        page_yaml: str,
        *,
        layout_source: str = "",
        page_source: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Context Defaults")
        self.setMinimumSize(520, 440)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 8)
        root.setSpacing(8)

        desc = QLabel(
            "These defaults are merged into the button config before rendering the preview. "
            "The button's own keys always win; page defaults override layout defaults."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("font-size: 11px; color: #64748b;")
        root.addWidget(desc)

        tabs = QTabWidget()

        def _make_yaml_tab(title: str, content: str, source: str) -> QWidget:
            tab = QWidget()
            tab_layout = QVBoxLayout(tab)
            tab_layout.setContentsMargins(0, 6, 0, 0)
            tab_layout.setSpacing(4)
            if source:
                src_lbl = QLabel(source)
                src_lbl.setStyleSheet("font-size: 10px; color: #94a3b8;")
                src_lbl.setWordWrap(True)
                tab_layout.addWidget(src_lbl)
            editor = QPlainTextEdit()
            editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            editor.setPlainText(content)
            YamlHighlighter(editor.document())
            tab_layout.addWidget(editor, 1)
            return tab, editor

        layout_tab, self._layout_edit = _make_yaml_tab(
            "Layout", layout_yaml, layout_source
        )
        page_tab, self._page_edit = _make_yaml_tab(
            "Page", page_yaml, page_source
        )
        tabs.addTab(layout_tab, "Layout (config.yaml)")
        tabs.addTab(page_tab, "Page")
        root.addWidget(tabs, 1)

        btn_box = QDialogButtonBox()
        btn_clear = QPushButton("Clear All")
        btn_clear.clicked.connect(self._clear_all)
        btn_box.addButton(btn_clear, QDialogButtonBox.ButtonRole.ResetRole)
        btn_box.addButton(QDialogButtonBox.StandardButton.Cancel)
        apply_btn = btn_box.addButton("Apply", QDialogButtonBox.ButtonRole.AcceptRole)
        apply_btn.setDefault(True)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        root.addWidget(btn_box)

    def _clear_all(self) -> None:
        self._layout_edit.clear()
        self._page_edit.clear()

    def layout_yaml(self) -> str:
        return self._layout_edit.toPlainText().strip()

    def page_yaml(self) -> str:
        return self._page_edit.toPlainText().strip()


class DesignerTab(QWidget):
    log_line = Signal(str)
    save_to_page = Signal(str, str, str)  # button_yaml, button_id, file_path
    _preview_done = Signal(object, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._target_root: Path | None = None
        self._preview_generation = 0
        self._loading = False          # suppresses YAML→form sync during bulk loads
        self._form_driving = False     # form→YAML update in progress (suppress YAML→form)
        self._source_button_id: str = ""   # set when opened from the editor
        self._source_file_path: str = ""   # set when opened from the editor
        self._loaded_yaml_str: str = ""    # canonical YAML at last load/save (dirty detection)
        # Context defaults — merged into button before preview (button > page > layout)
        self._layout_defaults: dict = {}
        self._layout_defaults_yaml: str = ""
        self._layout_defaults_source: str = ""
        self._page_defaults: dict = {}
        self._page_defaults_yaml: str = ""
        self._page_defaults_source: str = ""

        self._preview_done.connect(self._on_preview_done)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # ── Three-pane splitter ───────────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        # Pane 1 — Visual form
        form_frame = QFrame()
        form_frame.setObjectName("sectionFrame")
        form_layout = QVBoxLayout(form_frame)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(0)
        form_header = QLabel("Visual Editor")
        form_header.setStyleSheet("font-size: 11px; font-weight: 700; padding: 8px 10px; color: #334155;")
        form_layout.addWidget(form_header)
        self.button_form = ButtonFormWidget()
        self.button_form.form_changed.connect(self._on_form_changed)
        form_layout.addWidget(self.button_form, 1)
        splitter.addWidget(form_frame)

        # Pane 2 — YAML editor
        editor_frame = QFrame()
        editor_frame.setObjectName("sectionFrame")
        editor_layout = QVBoxLayout(editor_frame)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(0)
        editor_header = QLabel("Button YAML")
        editor_header.setStyleSheet("font-size: 11px; font-weight: 700; padding: 8px 10px; color: #334155;")
        editor_layout.addWidget(editor_header)
        self.yaml_edit = QPlainTextEdit()
        self.yaml_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.yaml_edit.setPlaceholderText(
            "# Start from a preset above, or type YAML directly.\n"
            "index: 0\n"
            "label: MY BUTTON\n"
            "activation: push\n"
            "commands:\n"
            "  press: sim/none/command\n"
        )
        self.yaml_edit.textChanged.connect(self._on_yaml_changed)
        YamlHighlighter(self.yaml_edit.document())
        editor_layout.addWidget(self.yaml_edit, 1)
        splitter.addWidget(editor_frame)

        # Pane 3 — Preview + controls
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        preview_frame = QFrame()
        preview_frame.setObjectName("sectionFrame")
        preview_inner = QVBoxLayout(preview_frame)
        preview_inner.setContentsMargins(10, 10, 10, 10)
        preview_inner.setSpacing(6)
        preview_title = QLabel("Preview")
        preview_title.setStyleSheet("font-size: 11px; font-weight: 700; color: #334155;")
        preview_inner.addWidget(preview_title)
        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(180, 180)
        self.preview_label.setMaximumSize(260, 260)
        self.preview_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        preview_inner.addWidget(self.preview_label, 0, Qt.AlignmentFlag.AlignHCenter)
        self.preview_status = QPlainTextEdit()
        self.preview_status.setReadOnly(True)
        self.preview_status.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.preview_status.setMaximumHeight(80)
        self.preview_status.setPlaceholderText("Pick a preset or write YAML to preview.")
        self.preview_status.setStyleSheet(
            "QPlainTextEdit { font-size: 11px; color: #64748b; border: none; background: transparent; }"
        )
        preview_inner.addWidget(self.preview_status)
        right_layout.addWidget(preview_frame, 0)

        deck_frame = QFrame()
        deck_frame.setObjectName("sectionFrame")
        deck_inner = QVBoxLayout(deck_frame)
        deck_inner.setContentsMargins(10, 8, 10, 8)
        deck_inner.setSpacing(4)
        deck_lbl = QLabel("Preview deck name")
        deck_lbl.setStyleSheet("font-size: 11px; font-weight: 700; color: #334155;")
        deck_inner.addWidget(deck_lbl)
        self.deck_edit = QLineEdit()
        self.deck_edit.setPlaceholderText("e.g. sr22-ipad")
        self.deck_edit.textChanged.connect(self._schedule_preview)
        deck_inner.addWidget(self.deck_edit)
        self.btn_defaults = QPushButton("Defaults…")
        self.btn_defaults.setFixedHeight(28)
        self.btn_defaults.setToolTip(
            "View / edit layout and page defaults that are applied when rendering the preview"
        )
        self.btn_defaults.clicked.connect(self._open_defaults_dialog)
        deck_inner.addWidget(self.btn_defaults)
        right_layout.addWidget(deck_frame, 0)

        dr_frame = QFrame()
        dr_frame.setObjectName("sectionFrame")
        dr_inner = QVBoxLayout(dr_frame)
        dr_inner.setContentsMargins(10, 8, 10, 8)
        dr_inner.setSpacing(4)
        dr_lbl = QLabel("Fake dataref values")
        dr_lbl.setStyleSheet("font-size: 11px; font-weight: 700; color: #334155;")
        dr_inner.addWidget(dr_lbl)
        dr_hint = QLabel("One per line:  dataref/name = value")
        dr_hint.setStyleSheet("font-size: 10px; color: #64748b;")
        dr_inner.addWidget(dr_hint)
        self.fake_dr_edit = QPlainTextEdit()
        self.fake_dr_edit.setFixedHeight(120)
        self.fake_dr_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.fake_dr_edit.setPlaceholderText(
            "sim/cockpit2/electrical/battery_on[0] = 1\n"
            "sim/cockpit2/switches/strobe_lights_on = 0\n"
            "# lines starting with # are ignored"
        )
        self.fake_dr_edit.textChanged.connect(self._schedule_preview)
        KeyValueHighlighter(self.fake_dr_edit.document())
        dr_inner.addWidget(self.fake_dr_edit)
        right_layout.addWidget(dr_frame, 0)

        right_layout.addStretch(1)
        splitter.addWidget(right_widget)

        # Form | YAML | Preview  →  3 : 2 : 2
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 2)
        root.addWidget(splitter, 1)

        # ── Bottom bar ────────────────────────────────────────────────────────
        bottom = QHBoxLayout()
        self.btn_copy = QPushButton("Copy YAML")
        self.btn_copy.setFixedHeight(32)
        self.btn_copy.clicked.connect(self._copy_yaml)
        bottom.addWidget(self.btn_copy)
        btn_clear = QPushButton("Clear")
        btn_clear.setFixedHeight(32)
        btn_clear.clicked.connect(self._clear)
        bottom.addWidget(btn_clear)
        bottom.addStretch(1)
        self.btn_save_to_page = QPushButton("Done")
        self.btn_save_to_page.setFixedHeight(32)
        self.btn_save_to_page.setEnabled(False)
        self.btn_save_to_page.setToolTip("Apply this button back to the page and return to the Editor tab (file not saved yet)")
        self.btn_save_to_page.clicked.connect(self._save_to_page)
        bottom.addWidget(self.btn_save_to_page)
        root.addLayout(bottom)

        # Preview debounce timer
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._render_preview)

    # ── Public API ────────────────────────────────────────────────────────────

    def _set_preview_deck_name(self, deck_name: str) -> None:
        deck_name = str(deck_name or "").strip()
        if not deck_name:
            return
        if self.deck_edit.text().strip() == deck_name:
            return
        self.deck_edit.setText(deck_name)

    def set_target(self, root_path: str | Path | None, deck_name: str = "") -> None:
        self._target_root = Path(root_path).expanduser().resolve() if root_path else None
        self._set_preview_deck_name(deck_name)
        if self._target_root:
            from cockpitdecks_editor.services.native_preview import list_preview_fonts
            self.button_form.populate_fonts(list_preview_fonts(self._target_root))
        self._schedule_preview()

    def load_button(
        self,
        button_yaml: str,
        *,
        deck_name: str = "",
        root_path: str | Path | None = None,
        button_id: str = "",
        file_path: str = "",
    ) -> None:
        """Load a button YAML string — called when opening from the Editor tab."""
        if root_path:
            self.set_target(root_path, deck_name=deck_name)
        else:
            self._set_preview_deck_name(deck_name)
        self._source_button_id = button_id
        self._source_file_path = file_path
        has_source = bool(button_id and file_path)
        self.btn_save_to_page.setEnabled(False)  # no changes yet
        self.btn_save_to_page.setToolTip(
            f"Apply back to {Path(file_path).name} ({button_id}) and return to the Editor tab (file not saved yet)" if has_source
            else "No source page — use Copy YAML instead"
        )
        try:
            data = yaml.safe_load(button_yaml) or {}
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        self._loaded_yaml_str = yaml.safe_dump(data, sort_keys=False, allow_unicode=False)
        self._loading = True
        try:
            self.yaml_edit.setPlainText(self._loaded_yaml_str)
            self.button_form.load(data)
        finally:
            self._loading = False
        self.button_form.populate_fonts(self.button_form._available_fonts)
        self._autofill_fake_datarefs(data)
        if file_path:
            self._auto_load_defaults(file_path=file_path)
        self._schedule_preview()

    def _save_to_page(self) -> None:
        text = self.yaml_edit.toPlainText().strip()
        if not text or not self._source_button_id or not self._source_file_path:
            return
        self.save_to_page.emit(text, self._source_button_id, self._source_file_path)
        self.log_line.emit(f"Applied {self._source_button_id} to {Path(self._source_file_path).name} (unsaved)")
        # Reset dirty baseline so button disables until the next edit
        try:
            data = yaml.safe_load(text)
            if isinstance(data, dict):
                self._loaded_yaml_str = yaml.safe_dump(data, sort_keys=False, allow_unicode=False)
        except Exception:
            pass
        self.btn_save_to_page.setEnabled(False)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _clear(self) -> None:
        self._source_button_id = ""
        self._source_file_path = ""
        self._loaded_yaml_str = ""
        self.btn_save_to_page.setEnabled(False)
        self.btn_save_to_page.setToolTip("No source page — use Copy YAML instead")
        self._loading = True
        try:
            self.yaml_edit.setPlainText("")
        finally:
            self._loading = False
        self.preview_label.clear()
        self._set_status("")

    def _auto_load_defaults(self, file_path: str) -> None:
        """Read layout config.yaml and page top-level keys and store them as defaults."""
        fp = Path(file_path)

        # Page defaults — everything in the page file except buttons/includes
        try:
            page_data = yaml.safe_load(fp.read_text(encoding="utf-8")) or {}
            if isinstance(page_data, dict):
                page_defs = {k: v for k, v in page_data.items() if k not in {"buttons", "includes"}}
                self._page_defaults = page_defs
                self._page_defaults_yaml = yaml.safe_dump(page_defs, sort_keys=False, allow_unicode=False).strip() if page_defs else ""
                self._page_defaults_source = str(fp)
        except Exception:
            pass

        # Layout defaults — deckconfig/<layout>/config.yaml
        if self._target_root:
            layout_cfg = self._target_root / "deckconfig" / fp.parent.name / "config.yaml"
            if layout_cfg.is_file():
                try:
                    layout_data = yaml.safe_load(layout_cfg.read_text(encoding="utf-8")) or {}
                    if isinstance(layout_data, dict):
                        layout_defs = {k: v for k, v in layout_data.items() if k not in {"buttons", "includes"}}
                        self._layout_defaults = layout_defs
                        self._layout_defaults_yaml = yaml.safe_dump(layout_defs, sort_keys=False, allow_unicode=False).strip() if layout_defs else ""
                        self._layout_defaults_source = str(layout_cfg)
                except Exception:
                    pass

        self._update_defaults_button()

    def _update_defaults_button(self) -> None:
        count = len(self._layout_defaults) + len(self._page_defaults)
        self.btn_defaults.setText(f"Defaults… ({count})" if count else "Defaults…")

    def _open_defaults_dialog(self) -> None:
        dlg = _DefaultsDialog(
            self._layout_defaults_yaml,
            self._page_defaults_yaml,
            layout_source=self._layout_defaults_source,
            page_source=self._page_defaults_source,
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        # Layout
        raw = dlg.layout_yaml()
        self._layout_defaults_yaml = raw
        try:
            parsed = yaml.safe_load(raw) or {}
            self._layout_defaults = dict(parsed) if isinstance(parsed, dict) else {}
        except Exception:
            self._layout_defaults = {}

        # Page
        raw = dlg.page_yaml()
        self._page_defaults_yaml = raw
        try:
            parsed = yaml.safe_load(raw) or {}
            self._page_defaults = dict(parsed) if isinstance(parsed, dict) else {}
        except Exception:
            self._page_defaults = {}

        self._update_defaults_button()
        self._schedule_preview()

    def _autofill_fake_datarefs(self, data: dict) -> None:
        """Pre-populate fake datarefs with refs found in data (only when field is empty)."""
        if self.fake_dr_edit.toPlainText().strip():
            return
        refs = _extract_datarefs_from_button(data)
        if refs:
            self.fake_dr_edit.setPlainText("\n".join(f"{r} = 0" for r in refs))

    def _check_yaml_dirty(self) -> None:
        """Enable/disable Save to Page based on whether YAML differs from the loaded baseline."""
        if not self._source_button_id or not self._source_file_path:
            return
        text = self.yaml_edit.toPlainText().strip()
        try:
            data = yaml.safe_load(text)
            if isinstance(data, dict):
                canonical = yaml.safe_dump(data, sort_keys=False, allow_unicode=False)
                self.btn_save_to_page.setEnabled(canonical != self._loaded_yaml_str)
        except Exception:
            pass  # invalid YAML — leave button state unchanged

    # Form → YAML sync
    def _on_form_changed(self, yaml_text: str) -> None:
        if self._loading:
            return
        self._form_driving = True
        try:
            self.yaml_edit.setPlainText(yaml_text)
        finally:
            self._form_driving = False
        self._check_yaml_dirty()
        self._schedule_preview()

    # YAML → form sync
    def _on_yaml_changed(self) -> None:
        if self._loading or self._form_driving:
            return
        text = self.yaml_edit.toPlainText().strip()
        if text:
            try:
                data = yaml.safe_load(text)
                if isinstance(data, dict):
                    self._loading = True
                    try:
                        self.button_form.load(data)
                    finally:
                        self._loading = False
            except Exception:
                pass
        self._check_yaml_dirty()
        self._schedule_preview()

    def _schedule_preview(self) -> None:
        self._preview_timer.start(300)

    def _render_preview(self) -> None:
        text = self.yaml_edit.toPlainText().strip()
        if not text:
            return
        target_root = self._target_root
        deck_name = self.deck_edit.text().strip()
        if not target_root or not deck_name:
            self._set_status("Set an aircraft root (via the Editor tab) and a deck name above to render.")
            return
        try:
            data = yaml.safe_load(text) or {}
        except Exception as exc:
            self._set_status(f"YAML error: {exc}", error=True)
            return
        if not isinstance(data, dict):
            self._set_status("YAML must be a mapping.", error=True)
            return
        hint = _validate_button_config(data, self._target_root, deck_name)
        if hint:
            self._set_status(hint, error=True)
            return
        data.setdefault("index", 0)
        # Merge context defaults — button keys always win; page > layout for the rest.
        for key, value in self._page_defaults.items():
            data.setdefault(key, value)
        for key, value in self._layout_defaults.items():
            data.setdefault(key, value)
        preview_yaml = yaml.safe_dump(data, sort_keys=False, allow_unicode=False)
        fake_datarefs = _parse_fake_datarefs(self.fake_dr_edit.toPlainText())

        self._set_status("Rendering…")
        generation = self._preview_generation + 1
        self._preview_generation = generation

        def _worker(gen=generation, pr=target_root, dn=deck_name, py=preview_yaml, fd=fake_datarefs):
            image_bytes, _meta, error = _render_preview_with_fallback(pr, dn, py, fake_datarefs=fd or None)
            self._preview_done.emit(image_bytes, {"generation": gen, "error": error})

        threading.Thread(target=_worker, daemon=True).start()

    def _set_status(self, text: str, error: bool = False) -> None:
        self.preview_status.setPlainText(text)
        color = "#dc2626" if error else "#64748b"
        self.preview_status.setStyleSheet(
            f"QPlainTextEdit {{ font-size: 11px; color: {color}; border: none; background: transparent; }}"
        )

    def _on_preview_done(self, image_bytes: object, info: object) -> None:
        payload = info if isinstance(info, dict) else {}
        if payload.get("generation") != self._preview_generation:
            return
        error = str(payload.get("error") or "").strip()
        if isinstance(image_bytes, (bytes, bytearray)) and image_bytes:
            pixmap = QPixmap()
            if pixmap.loadFromData(bytes(image_bytes), "PNG"):
                sz = min(self.preview_label.maximumWidth(), self.preview_label.maximumHeight())
                scaled = pixmap.scaled(
                    sz, sz,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.preview_label.setPixmap(scaled)
                # Show soft warnings (validity issues) in amber even when image rendered
                if error and error != "ok":
                    self._set_status(error, error=True)
                else:
                    self._set_status("")
                return
        self.preview_label.clear()
        msg = _friendly_preview_error(error)
        self._set_status(msg, error=bool(error))

    def _copy_yaml(self) -> None:
        text = self.yaml_edit.toPlainText().strip()
        if text:
            QApplication.clipboard().setText(text)
            self.log_line.emit("YAML copied to clipboard.")
