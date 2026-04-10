from __future__ import annotations

import io
import pkgutil
import threading
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


class _PreviewDeck(DeckWithIcons):
    DECK_NAME = "desktop-preview"
    DEVICE_MANAGER = None

    def make_default_page(self, b: str | None = None):
        return None

    def render(self, button):
        return None

    def start(self):
        return None


class _NativePreviewContext:
    def __init__(self, target_root: Path) -> None:
        self.target_root = target_root
        self.lock = threading.RLock()
        self.cockpit = _PreviewCockpit({"SIMULATOR_NAME": "NoSimulator"})
        self.cockpit.aircraft.acpath = str(target_root)
        self.cockpit.aircraft._config = Config(str(target_root / "deckconfig" / "config.yaml"))
        self.cockpit.aircraft.load_deck_types()
        self.cockpit.aircraft.load_resources()
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


def render_button_preview_native(
    target_root: str | Path,
    deck_name: str,
    button_yaml: str,
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

    try:
        pool = _get_pool(root)
        ctx = pool.acquire()
        with ctx.lock:
            deck = ctx.get_deck(deck_name)
            button = deck.make_button(config=config)
            if button is None:
                return None, None, "button not created"
            image = button.get_representation()
            if image is None:
                return None, None, "button representation not created"
            target_size = deck.get_spanned_image_size(button) or deck.get_image_size(button.index)
            if target_size and getattr(image, "size", None) != target_size:
                image = image.resize(target_size, resample=Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            meta = {
                "error": "ok",
                "activation-valid": button._activation.is_valid(),
                "representation-valid": button._representation.is_valid(),
                "activation-desc": button._activation.describe(),
                "representation-desc": button._representation.describe(),
            }
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
