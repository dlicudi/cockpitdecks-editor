from __future__ import annotations

import io
import logging
import pkgutil
import threading
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any
import importlib

import yaml
from PIL import Image

from cockpitdecks import Config
from cockpitdecks.cockpit import Cockpit
from cockpitdecks.deck import DeckWithIcons
from cockpitdecks.constant import COCKPITDECKS_INTERNAL_EXTENSIONS, DECK_ACTIONS, DECK_FEEDBACK
from cockpitdecks.simulator import Simulator
from cockpitdecks.buttons.activation.activation import Activation
from cockpitdecks.buttons.representation.representation import Representation
from cockpitdecks.buttons.representation.hardware import HardwareRepresentation


class _PreviewCockpit(Cockpit):
    _SKIP_INTERNAL_EXTENSIONS = {"cockpitdecks_wm", "cockpitdecks_bx"}

    def add_extensions(self, trace_ext_loading: bool = False):
        def import_submodules(package, recursive=True):
            if isinstance(package, str):
                try:
                    package = importlib.import_module(package)
                except ModuleNotFoundError:
                    return {}
            results = {}
            for _loader, name, is_pkg in pkgutil.walk_packages(package.__path__):
                full_name = package.__name__ + "." + name
                try:
                    results[full_name] = importlib.import_module(full_name)
                except ModuleNotFoundError:
                    continue
                except Exception:
                    continue
                if recursive and is_pkg:
                    results.update(import_submodules(full_name))
            return results

        allowed = set(COCKPITDECKS_INTERNAL_EXTENSIONS) - self._SKIP_INTERNAL_EXTENSIONS
        allowed.update(self.requested_extensions)
        self.all_extensions = allowed
        for package in self.all_extensions:
            import_submodules(package)

    def init(self):
        self.add_extensions(trace_ext_loading=False)
        self.all_simulators = {s.name: s for s in Cockpit.all_subclasses(Simulator)}
        self.all_activations = {s.name(): s for s in Cockpit.all_subclasses(Activation) if not s.name().endswith("-base")} | {
            DECK_ACTIONS.NONE.value: Activation
        }
        self.all_representations = {s.name(): s for s in Cockpit.all_subclasses(Representation) if not s.name().endswith("-base")} | {
            DECK_FEEDBACK.NONE.value: Representation
        }
        self.all_hardware_representations = {s.name(): s for s in Cockpit.all_subclasses(HardwareRepresentation)}
        self.load_deck_types()
        if not self.init_simulator():
            raise RuntimeError("preview simulator init failed")
        self.load_icons()
        self.load_sounds()
        self.load_fonts()
        self.load_defaults()


@lru_cache(maxsize=1)
def get_representation_schema_map() -> dict[str, dict[str, Any]]:
    """Return representation editor schemas keyed by representation name."""
    cockpit = _PreviewCockpit({"SIMULATOR_NAME": "NoSimulator"})
    cockpit.add_extensions(trace_ext_loading=False)
    schemas = {}
    nested_block_names = {
        "annunciator",
        "annunciator-animate",
        "chart",
        "circular-switch",
        "compass",
        "data",
        "gauge",
        "knob",
        "push-switch",
        "slider-icon",
        "switch",
        "tape",
        "weather-metar",
        "weather-real",
        "weather-xp",
    }
    for cls in Cockpit.all_subclasses(Representation):
        name = cls.name()
        if name.endswith("-base"):
            continue
        if hasattr(cls, "editor_schema"):
            schemas[name] = cls.editor_schema()
        else:
            schemas[name] = {
                "name": name,
                "label": getattr(cls, "EDITOR_LABEL", None) or cls.__name__,
                "family": getattr(cls, "EDITOR_FAMILY", None) or "Representation",
                "editor_fields": cls.parameters(),
            }
        schemas[name]["storage_mode"] = "nested_block" if name in nested_block_names else "flat"
    if DECK_FEEDBACK.NONE.value not in schemas:
        schemas[DECK_FEEDBACK.NONE.value] = Representation.editor_schema()
        schemas[DECK_FEEDBACK.NONE.value]["storage_mode"] = "flat"
    return schemas


class _PreviewDeck(DeckWithIcons):
    DECK_NAME = "desktop-preview"
    DEVICE_MANAGER = None

    def preprocess_buttons(self, buttons: list, page) -> list:
        deck_type_name = str(self._config.get("type") or "").strip().lower()
        buttons = [self.normalize_button_config(button) if isinstance(button, dict) else button for button in buttons]
        if deck_type_name != "loupedecklive":
            return buttons

        left_encoders = ["e0", "e1", "e2"]
        right_encoders = ["e3", "e4", "e5"]
        all_encoders = set(left_encoders + right_encoders)
        side_display_keys = {
            "label",
            "label-color",
            "label-size",
            "label-font",
            "label-position",
            "text",
            "text-color",
            "text-size",
            "text-font",
            "text-position",
            "text-format",
            "formula",
        }

        def _rep_type(button: dict) -> str:
            """Return the representation type string regardless of whether it is stored
            as a plain string or as a nested dict with a 'type' key."""
            rep_val = button.get("representation") or ""
            if isinstance(rep_val, dict):
                return str(rep_val.get("type") or "").strip()
            return str(rep_val).strip()

        has_display = False
        indices: set[str] = set()
        for button in buttons:
            idx = str(button.get("index") or "")
            indices.add(idx)
            if idx in all_encoders and ("display" in button or _rep_type(button) in {"side-display", "side"}):
                has_display = True
        if not has_display or "left" in indices or "right" in indices:
            return buttons

        merged = getattr(page, "_defaults", {})
        screen_config = merged.get("screen") or page._config.get("screen") or {}
        icon_color = screen_config.get("background", "Black")
        render_cooldown = screen_config.get("render-cooldown-ms")

        left_displays: dict[int, dict] = {}
        right_displays: dict[int, dict] = {}
        new_buttons: list = []

        for button in buttons:
            idx = str(button.get("index") or "")
            if idx in all_encoders:
                button = dict(button)
                display = button.pop("display", None)
                if not isinstance(display, dict):
                    display = {}
                rep = _rep_type(button)
                if rep in {"side-display", "side"}:
                    if not display:
                        rep_val = button.get("representation")
                        if isinstance(rep_val, dict):
                            # Dict-style: display config lives inside the representation dict
                            display = {key: rep_val.get(key) for key in side_display_keys if rep_val.get(key) not in (None, "")}
                        else:
                            display = {key: button.get(key) for key in side_display_keys if button.get(key) not in (None, "")}
                    button.pop("representation", None)
                    for key in side_display_keys:
                        button.pop(key, None)
                if idx in left_encoders:
                    left_displays[left_encoders.index(idx)] = display
                else:
                    right_displays[right_encoders.index(idx)] = display
            new_buttons.append(button)

        def make_screen_button(index: str, name: str, labels: list[dict]) -> dict[str, Any]:
            out: dict[str, Any] = {
                "index": index,
                "name": name,
                "activation": "none",
                "representation": "side-display",
                "side": {"icon-color": icon_color, "labels": labels},
            }
            if render_cooldown is not None:
                out["render-cooldown-ms"] = render_cooldown
            return out

        if left_displays:
            new_buttons.append(make_screen_button("left", "left_screen", [left_displays.get(i, {}) for i in range(len(left_encoders))]))
        if right_displays:
            new_buttons.append(make_screen_button("right", "right_screen", [right_displays.get(i, {}) for i in range(len(right_encoders))]))
        return new_buttons

    def make_default_page(self, b: str | None = None):
        return None

    def render(self, button):
        return None

    def start(self):
        return None


class _LogCapture(logging.Handler):
    """Captures WARNING+ log records from cockpitdecks during a render."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(self.format(record))


@contextmanager
def _capture_logs():
    """Temporarily attach a capturing handler to the cockpitdecks root logger."""
    handler = _LogCapture()
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("cockpitdecks")
    root.addHandler(handler)
    try:
        yield handler
    finally:
        root.removeHandler(handler)


class _NativePreviewContext:
    def __init__(self, target_root: Path) -> None:
        self.target_root = target_root
        self.lock = threading.RLock()
        self.cockpit = _PreviewCockpit({"SIMULATOR_NAME": "NoSimulator"})
        self.cockpit.aircraft.acpath = str(target_root)
        self.cockpit.aircraft._config = Config(str(target_root / "deckconfig" / "config.yaml"))
        self.cockpit.aircraft.load_deck_types()
        self.cockpit.aircraft.load_resources()
        self.cockpit.load_fonts()  # merges cockpitdecks bundled fonts + aircraft fonts into cockpit.fonts
        self._config_data = yaml.safe_load((target_root / "deckconfig" / "config.yaml").read_text(encoding="utf-8")) or {}
        self._decks: dict[str, _PreviewDeck] = {}

    def _deck_config(self, deck_name: str) -> dict[str, Any] | None:
        for deck in self._config_data.get("decks", []):
            if isinstance(deck, dict) and str(deck.get("name") or "").strip() == deck_name:
                return dict(deck)
        return None

    def get_deck(self, deck_name: str) -> _PreviewDeck:
        cached = self._decks.get(deck_name)
        if cached is not None:
            return cached
        deck_config = self._deck_config(deck_name)
        if deck_config is None:
            raise ValueError(f"deck {deck_name} not found")
        deck_config.setdefault("serial", f"preview-{deck_name}")
        deck = _PreviewDeck(deck_name, deck_config, self.cockpit)
        deck.set_deck_type()
        layout_name = str(deck_config.get("layout") or "").strip()
        if layout_name:
            layout_config = self.target_root / "deckconfig" / layout_name / "config.yaml"
            if layout_config.is_file():
                cfg = Config(str(layout_config))
                if cfg.store is None:
                    cfg.store = {}
                deck._layout_config = cfg
        self._decks[deck_name] = deck
        return deck


class _NativePreviewPool:
    def __init__(self, target_root: Path, pool_size: int = 3) -> None:
        self.target_root = target_root
        self.pool_size = max(1, pool_size)
        self.lock = threading.RLock()
        self._contexts: list[_NativePreviewContext] = []
        self._next_index = 0

    def primary(self) -> _NativePreviewContext:
        with self.lock:
            if not self._contexts:
                self._contexts.append(_NativePreviewContext(self.target_root))
            return self._contexts[0]

    def acquire(self) -> _NativePreviewContext:
        with self.lock:
            if len(self._contexts) < self.pool_size:
                ctx = _NativePreviewContext(self.target_root)
                self._contexts.append(ctx)
                return ctx
            ctx = self._contexts[self._next_index % len(self._contexts)]
            self._next_index += 1
            return ctx

    def warm(self) -> None:
        while True:
            with self.lock:
                if len(self._contexts) >= self.pool_size:
                    return
            self.acquire()


_CONTEXTS: dict[str, _NativePreviewPool] = {}
_CONTEXTS_LOCK = threading.RLock()


def _get_pool(target_root: Path) -> _NativePreviewPool:
    key = str(target_root.resolve())
    with _CONTEXTS_LOCK:
        pool = _CONTEXTS.get(key)
        if pool is None:
            pool = _NativePreviewPool(target_root)
            _CONTEXTS[key] = pool
        return pool


def warm_preview_pool(target_root: str | Path) -> str | None:
    try:
        root = Path(target_root).expanduser().resolve()
    except OSError as exc:
        return str(exc)
    if not (root / "deckconfig" / "config.yaml").is_file():
        return "target has no deckconfig/config.yaml"
    try:
        pool = _get_pool(root)
        pool.warm()
        return None
    except Exception as exc:
        return str(exc)


_SIDE_DISPLAY_KEYS = {
    "label", "label-color", "label-size", "label-font", "label-position",
    "text", "text-color", "text-size", "text-font", "text-position",
    "text-format", "formula",
}
_LEFT_ENCODERS = ["e0", "e1", "e2"]
_RIGHT_ENCODERS = ["e3", "e4", "e5"]


def _side_display_slot_config(config: dict) -> tuple[dict, int] | None:
    """If *config* is a side-display encoder button (index eN), return a tuple of
    (screen_button_config, slot_index) suitable for rendering the full "left"/"right"
    strip with only that encoder's slot populated.  Returns None for all other buttons.
    """
    idx = str(config.get("index") or "").strip().lower()
    if idx not in _LEFT_ENCODERS and idx not in _RIGHT_ENCODERS:
        return None

    rep_val = config.get("representation") or ""
    if isinstance(rep_val, dict):
        rep_type = str(rep_val.get("type") or "").strip()
    else:
        rep_type = str(rep_val).strip()
    if rep_type not in {"side-display", "side"}:
        return None

    # Extract display properties from wherever they live (dict representation or top-level).
    if isinstance(rep_val, dict):
        display = {k: rep_val[k] for k in _SIDE_DISPLAY_KEYS if rep_val.get(k) not in (None, "")}
    else:
        display = {k: config[k] for k in _SIDE_DISPLAY_KEYS if config.get(k) not in (None, "")}

    if idx in _LEFT_ENCODERS:
        slot = _LEFT_ENCODERS.index(idx)
        screen_index = "left"
        screen_name = "left_screen"
    else:
        slot = _RIGHT_ENCODERS.index(idx)
        screen_index = "right"
        screen_name = "right_screen"

    labels: list[dict] = [{}, {}, {}]
    labels[slot] = display

    screen_config: dict[str, Any] = {
        "index": screen_index,
        "name": screen_name,
        "activation": "none",
        "representation": "side-display",
        "side": {"icon-color": "Black", "labels": labels},
    }
    return screen_config, slot


def render_button_preview_native(
    target_root: str | Path,
    deck_name: str,
    button_yaml: str,
    fake_datarefs: dict[str, Any] | None = None,
) -> tuple[bytes | None, dict[str, Any] | None, str | None]:
    try:
        root = Path(target_root).expanduser().resolve()
    except OSError as exc:
        return None, None, str(exc)
    if not (root / "deckconfig" / "config.yaml").is_file():
        return None, None, "target has no deckconfig/config.yaml"

    try:
        config = yaml.safe_load(button_yaml) or {}
    except Exception as exc:
        return None, None, f"preview yaml invalid: {exc}"
    if not isinstance(config, dict):
        return None, None, "preview config must be a YAML mapping"

    # Side-display encoder buttons (eN) must be rendered as "left"/"right" strip buttons.
    # We build a full strip config with only the relevant slot filled, then crop afterwards.
    side_slot: int | None = None
    slot_result = _side_display_slot_config(config)
    if slot_result is not None:
        config, side_slot = slot_result

    try:
        pool = _get_pool(root)
        ctx = pool.acquire()
        with ctx.lock, _capture_logs() as captured:
            deck = ctx.get_deck(deck_name)
            button = deck.make_button(config=config)
            if button is None:
                log_detail = "\n".join(captured.records)
                msg = "button not created"
                if log_detail:
                    msg = f"{msg}\n\n{log_detail}"
                return None, None, msg
            if fake_datarefs:
                for dr_name, dr_value in fake_datarefs.items():
                    var = ctx.cockpit.variable_database.get(dr_name)
                    if var is not None:
                        var.value = dr_value
            image = button.get_representation()
            if image is None:
                log_detail = "\n".join(captured.records)
                msg = "button representation not created"
                if log_detail:
                    msg = f"{msg}\n\n{log_detail}"
                return None, None, msg
            target_size = deck.get_spanned_image_size(button) or deck.get_image_size(button.index)
            if target_size and not all(d > 0 for d in target_size):
                return None, None, "span extends beyond deck boundary"
            if target_size and getattr(image, "size", None) != target_size:
                image = image.resize(target_size, resample=Image.Resampling.LANCZOS)
            # For side-display encoder previews, crop the full strip to the relevant 1/3 slot.
            if side_slot is not None:
                h = image.size[1]
                slot_h = h // 3
                top = side_slot * slot_h
                bottom = top + slot_h if side_slot < 2 else h
                image = image.crop((0, top, image.size[0], bottom))
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            act_valid = button._activation.is_valid()
            rep_valid = button._representation.is_valid()
            warnings = "\n".join(captured.records)
            meta = {
                "error": "ok",
                "activation-valid": act_valid,
                "representation-valid": rep_valid,
                "activation-desc": button._activation.describe(),
                "representation-desc": button._representation.describe(),
                "warnings": warnings,
            }
            # Surface validity issues as a soft warning (image still rendered)
            if not act_valid or not rep_valid or warnings:
                parts = []
                if not act_valid:
                    parts.append("activation invalid")
                if not rep_valid:
                    parts.append("representation invalid")
                if warnings:
                    parts.append(warnings)
                meta["error"] = " · ".join(parts)
            return buf.getvalue(), meta, None
    except Exception as exc:
        return None, None, str(exc)


def list_preview_fonts(target_root: str | Path) -> list[str]:
    """Return sorted font names available in the cockpitdecks preview context for target_root."""
    try:
        root = Path(target_root).expanduser().resolve()
        pool = _get_pool(root)
        ctx = pool.primary()
        with ctx.lock:
            return sorted(ctx.cockpit.fonts.keys())
    except Exception:
        return []


def describe_slot_native(
    target_root: str | Path,
    deck_name: str,
    index: int | str,
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        root = Path(target_root).expanduser().resolve()
    except OSError as exc:
        return None, str(exc)
    if not (root / "deckconfig" / "config.yaml").is_file():
        return None, "target has no deckconfig/config.yaml"
    try:
        pool = _get_pool(root)
        ctx = pool.primary()
        with ctx.lock:
            deck = ctx.get_deck(deck_name)
            button_def = deck.deck_type.get_button_definition(index)
            if button_def is None:
                return None, f"slot {index} not found"
            return {
                "index": str(index),
                "activations": sorted(deck.valid_activations(index)),
                "representations": sorted(deck.valid_representations(index)),
                "has_icon": bool(button_def.has_icon()),
                "is_encoder": bool(button_def.is_encoder()),
                "prefix": deck.get_index_prefix(index),
            }, None
    except Exception as exc:
        return None, str(exc)
