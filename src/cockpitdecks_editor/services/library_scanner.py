"""Scan cockpitdecks config folders and build a searchable button library."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_DATAREF_RE = re.compile(r"\$\{([^}]+)\}")
_SIM_PREFIX_RE = re.compile(r"^(sim/|fa:)")

_CATEGORY_RULES: list[tuple[str, str]] = [
    (r"radio|com\d|nav\d|transponder|xpdr|audio", "Radio"),
    (r"engine|fuel|mixture|throttle|rpm|egt|oil|chd", "Engine"),
    (r"fcu|autopilot|ap_|mcp", "Autopilot"),
    (r"switches_master|switches_elec|electrical", "Electrical"),
    (r"switches_light|lights", "Lights"),
    (r"switches_ic|icing|anti.?ice|tks|pitot", "Anti-Ice"),
    (r"fms|gcu|fms_nav", "FMS/Nav"),
    (r"weather|metar", "Weather"),
    (r"pager|index|views", "Navigation"),
    (r"switches_ground|ground", "Ground"),
    (r"pfi|pfd|mfd|g1000", "Displays"),
]

# Cache: deckconfig/config.yaml path → {layout_id: deck_name}
_deck_name_cache: dict[Path, dict[str, str]] = {}


def _infer_category(page_stem: str) -> str:
    lower = page_stem.lower()
    for pattern, category in _CATEGORY_RULES:
        if re.search(pattern, lower):
            return category
    return "Other"


def _extract_datarefs(obj: object) -> list[str]:
    found: list[str] = []
    if isinstance(obj, str):
        for m in _DATAREF_RE.finditer(obj):
            found.append(m.group(1))
    elif isinstance(obj, dict):
        for v in obj.values():
            found.extend(_extract_datarefs(v))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_extract_datarefs(item))
    return list(dict.fromkeys(found))


def _portability(datarefs: list[str]) -> str:
    for dr in datarefs:
        if not _SIM_PREFIX_RE.match(dr):
            return "aircraft-specific"
    return "universal"


def _button_label(button: dict) -> str:
    """Extract the best human-readable label, digging into nested structures."""
    name = str(button.get("name") or "")
    rep = button.get("representation") or {}
    label = ""

    if isinstance(rep, dict):
        label = str(rep.get("label") or rep.get("text") or "").strip()
        if not label:
            # Dig into annunciator/switch/gauge parts for text
            for key in ("annunciator", "switch", "circular-switch", "gauge"):
                sub = rep.get(key)
                if isinstance(sub, dict):
                    parts = sub.get("parts") or []
                    for part in parts if isinstance(parts, list) else []:
                        if isinstance(part, dict):
                            text = str(part.get("text") or "").strip()
                            if text and not text.startswith("${"):
                                label = text
                                break
                if label:
                    break

    # Fall back to command path tail
    if not label and not name:
        activation = button.get("activation") or {}
        if isinstance(activation, dict):
            cmds = activation.get("commands") or {}
            cmd = ""
            if isinstance(cmds, dict):
                cmd = str(cmds.get("press") or cmds.get("toggle-on") or "").strip()
            if not cmd:
                cmd = str(activation.get("command") or "").strip()
            if cmd:
                label = cmd.split("/")[-1].replace("_", " ").title()

    return (label or name).strip()


def _lookup_deck_name(target_root: Path, layout_id: str) -> str:
    """Read deckconfig/config.yaml and return the deck name for the given layout folder."""
    config_path = target_root / "deckconfig" / "config.yaml"
    if config_path not in _deck_name_cache:
        try:
            cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            mapping: dict[str, str] = {}
            for deck in cfg.get("decks", []):
                if isinstance(deck, dict):
                    lid = str(deck.get("layout") or "").strip()
                    dname = str(deck.get("name") or "").strip()
                    if lid and dname:
                        mapping[lid] = dname
            _deck_name_cache[config_path] = mapping
        except Exception:
            _deck_name_cache[config_path] = {}
    return _deck_name_cache.get(config_path, {}).get(layout_id, "")


def _entry_id(source_file: Path, button: dict) -> str:
    key = f"{source_file}:{button.get('index', '')}:{button.get('name', '')}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


@dataclass
class LibraryEntry:
    id: str
    name: str
    label: str
    category: str
    source_aircraft: str
    source_page: str
    source_file: Path
    target_root: Path       # aircraft folder containing deckconfig/
    deck_name: str          # cockpitdecks deck name (e.g. "LoupedeckLive")
    button_data: dict
    portability: str        # "universal" | "aircraft-specific"
    dataref_deps: list[str] = field(default_factory=list)


def scan_library(deck_paths: list[str | Path]) -> list[LibraryEntry]:
    """Return all button entries found under the given deck config roots."""
    entries: list[LibraryEntry] = []
    seen: set[str] = set()

    for root in deck_paths:
        root = Path(root).expanduser().resolve()
        if not root.is_dir():
            continue
        for yaml_file in sorted(root.rglob("*.yaml")):
            parts = yaml_file.parts
            if "deckconfig" not in parts:
                continue
            if "includes" in parts or "resources" in parts:
                continue
            try:
                raw = yaml.safe_load(yaml_file.read_text(encoding="utf-8", errors="replace")) or {}
            except Exception:
                continue
            if not isinstance(raw, dict):
                continue
            buttons = raw.get("buttons")
            if not isinstance(buttons, list) or not buttons:
                continue

            deckconfig_idx = parts.index("deckconfig")
            aircraft = parts[deckconfig_idx - 1] if deckconfig_idx > 0 else "unknown"
            target_root = Path(*parts[:deckconfig_idx])
            layout_id = parts[deckconfig_idx + 1] if len(parts) > deckconfig_idx + 1 else ""
            deck_name = _lookup_deck_name(target_root, layout_id)
            page_stem = yaml_file.stem

            for button in buttons:
                if not isinstance(button, dict):
                    continue
                eid = _entry_id(yaml_file, button)
                if eid in seen:
                    continue
                seen.add(eid)
                datarefs = _extract_datarefs(button)
                entries.append(LibraryEntry(
                    id=eid,
                    name=str(button.get("name") or ""),
                    label=_button_label(button),
                    category=_infer_category(page_stem),
                    source_aircraft=aircraft,
                    source_page=page_stem,
                    source_file=yaml_file,
                    target_root=target_root,
                    deck_name=deck_name,
                    button_data=button,
                    portability=_portability(datarefs),
                    dataref_deps=datarefs,
                ))
    return entries
