"""Persistent curated button library — stored as JSON in the app data directory."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from cockpitdecks_editor.services.desktop_settings import _desktop_config_dir


def _library_path() -> Path:
    return _desktop_config_dir() / "button_library.json"


@dataclass
class CuratedEntry:
    id: str
    name: str               # user-given name
    category: str
    tags: list[str]         # free-form tags e.g. ["toggle", "annunciator", "electrical"]
    source_aircraft: str
    source_page: str
    button_data: dict
    portability: str        # "universal" | "aircraft-specific"

    @staticmethod
    def make_id(button_data: dict, source_aircraft: str) -> str:
        key = json.dumps(button_data, sort_keys=True) + source_aircraft
        return hashlib.md5(key.encode()).hexdigest()[:12]


def load_library() -> list[CuratedEntry]:
    path = _library_path()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        entries = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            entries.append(CuratedEntry(
                id=item.get("id", ""),
                name=item.get("name", ""),
                category=item.get("category", "Other"),
                tags=item.get("tags", []),
                source_aircraft=item.get("source_aircraft", ""),
                source_page=item.get("source_page", ""),
                button_data=item.get("button_data", {}),
                portability=item.get("portability", "unknown"),
            ))
        return entries
    except Exception:
        return []


def save_library(entries: list[CuratedEntry]) -> None:
    path = _library_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(e) for e in entries], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def add_entry(entry: CuratedEntry) -> None:
    entries = load_library()
    entries = [e for e in entries if e.id != entry.id]
    entries.append(entry)
    save_library(entries)


def remove_entry(entry_id: str) -> None:
    entries = load_library()
    save_library([e for e in entries if e.id != entry_id])
