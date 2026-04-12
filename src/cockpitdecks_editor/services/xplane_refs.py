"""Parse X-Plane DataRefs.txt and Commands.txt into searchable records."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(slots=True)
class DatarefRecord:
    name: str
    dtype: str          # e.g. "float", "int", "byte[40]"
    writable: bool
    units: str
    description: str
    is_array: bool      # True when dtype contains "[…]"


@dataclass(slots=True)
class CommandRecord:
    name: str
    description: str


_ARRAY_RE = re.compile(r"\[")


def parse_datarefs(path: str | Path) -> list[DatarefRecord]:
    """Return all dataref records from a DataRefs.txt file."""
    records: list[DatarefRecord] = []
    p = Path(path)
    if not p.is_file():
        return records
    with p.open(encoding="utf-8", errors="replace") as fh:
        for lineno, raw in enumerate(fh):
            if lineno == 0:
                # Skip header line (version info)
                continue
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            parts = line.split("\t")
            name = parts[0].strip() if len(parts) > 0 else ""
            if not name:
                continue
            dtype = parts[1].strip() if len(parts) > 1 else ""
            writable_raw = parts[2].strip() if len(parts) > 2 else ""
            units = parts[3].strip() if len(parts) > 3 else ""
            description = parts[4].strip() if len(parts) > 4 else ""
            writable = writable_raw.lower() == "y"
            is_array = bool(_ARRAY_RE.search(dtype))
            records.append(DatarefRecord(
                name=name,
                dtype=dtype,
                writable=writable,
                units=units,
                description=description,
                is_array=is_array,
            ))
    return records


def parse_commands(path: str | Path) -> list[CommandRecord]:
    """Return all command records from a Commands.txt file."""
    records: list[CommandRecord] = []
    p = Path(path)
    if not p.is_file():
        return records
    with p.open(encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            # Commands.txt uses whitespace separation (not necessarily a single tab)
            # Format: name<whitespace>description
            m = re.match(r"^(\S+)\s+(.*)", line)
            if not m:
                continue
            name = m.group(1).strip()
            description = m.group(2).strip()
            if name:
                records.append(CommandRecord(name=name, description=description))
    return records


def parse_drt_datarefs(path: str | Path) -> list[str]:
    """Return dataref names from a drt_last_run_datarefs.txt file (names only, one per line)."""
    names: list[str] = []
    p = Path(path)
    if not p.is_file():
        return names
    with p.open(encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            name = raw.strip()
            if name:
                names.append(name)
    return names


def parse_drt_commands(path: str | Path) -> list[str]:
    """Return command names from a drt_last_run_commandrefs.txt file (names only, one per line)."""
    names: list[str] = []
    p = Path(path)
    if not p.is_file():
        return names
    with p.open(encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            name = raw.strip()
            if name:
                names.append(name)
    return names


def default_datarefs_path() -> Path | None:
    """Try common X-Plane install locations for DataRefs.txt."""
    candidates = [
        Path.home() / "X-Plane 12" / "Resources" / "plugins" / "DataRefs.txt",
        Path.home() / "X-Plane 11" / "Resources" / "plugins" / "DataRefs.txt",
        Path("/Applications/X-Plane 12/Resources/plugins/DataRefs.txt"),
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def default_commands_path() -> Path | None:
    """Try common X-Plane install locations for Commands.txt."""
    candidates = [
        Path.home() / "X-Plane 12" / "Resources" / "plugins" / "Commands.txt",
        Path.home() / "X-Plane 11" / "Resources" / "plugins" / "Commands.txt",
        Path("/Applications/X-Plane 12/Resources/plugins/Commands.txt"),
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def default_drt_datarefs_path() -> Path | None:
    """Try common X-Plane output locations for drt_last_run_datarefs.txt."""
    candidates = [
        Path.home() / "X-Plane 12" / "Output" / "preferences" / "drt_last_run_datarefs.txt",
        Path.home() / "X-Plane 11" / "Output" / "preferences" / "drt_last_run_datarefs.txt",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def default_drt_commands_path() -> Path | None:
    """Try common X-Plane output locations for drt_last_run_commandrefs.txt."""
    candidates = [
        Path.home() / "X-Plane 12" / "Output" / "preferences" / "drt_last_run_commandrefs.txt",
        Path.home() / "X-Plane 11" / "Output" / "preferences" / "drt_last_run_commandrefs.txt",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None
