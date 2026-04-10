from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cockpitdecks_editor.services.desktop_settings import _split_paths, load as load_settings


@dataclass
class LaunchTargetInfo:
    aircraft_name: str
    path: str
    root: str
    deck_count: int
    deck_names: list[str]
    config_ok: bool
    config_error: str = ""
    has_manifest: bool = False
    config_name: str = ""
    version: str = ""
    icao: str = ""
    manifest_status: str = ""
    description: str = ""
    layout_infos: list[tuple[str, str]] | None = None

    def __post_init__(self) -> None:
        if self.layout_infos is None:
            self.layout_infos = []


def shorten_filesystem_path(path: Path | str, *, max_len: int = 72) -> str:
    try:
        s = str(Path(path).expanduser().resolve())
    except OSError:
        s = str(Path(path).expanduser())
    home = str(Path.home())
    if s.startswith(home):
        s = "~" + s[len(home):]
    if len(s) <= max_len:
        return s
    head = max_len // 2 - 2
    tail = max_len - head - 3
    return s[:head] + "…" + s[-tail:]


def configured_launch_target() -> str:
    return (load_settings().get("COCKPITDECKS_TARGET") or "").strip()


def cockpitdecks_search_roots() -> list[Path]:
    raw = (load_settings().get("COCKPITDECKS_PATH") or "").strip()
    roots: list[Path] = []
    seen: set[str] = set()
    for chunk in _split_paths(raw):
        s = chunk.strip()
        if not s:
            continue
        p = Path(s).expanduser()
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        if p.is_dir():
            roots.append(p)
    return roots


def parse_simple_yaml_meta(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    current_multiline: str | None = None
    multiline_parts: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for raw in lines:
        if current_multiline is not None:
            if raw.startswith(" ") or raw.startswith("\t"):
                multiline_parts.append(raw.strip())
                continue
            out[current_multiline] = " ".join(part for part in multiline_parts if part).strip()
            current_multiline = None
            multiline_parts = []
        s = raw.strip()
        if not s or s.startswith("#") or ":" not in s:
            continue
        key, value = s.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value in {">", "|"}:
            current_multiline = key
            multiline_parts = []
            continue
        out[key] = value.strip("'\"")
    if current_multiline is not None:
        out[current_multiline] = " ".join(part for part in multiline_parts if part).strip()
    return out


def parse_manifest_layouts(manifest_path: Path) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    in_layouts = False
    current_id = ""
    current_status = ""
    try:
        lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return results
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        stripped = raw.strip()
        if stripped == "layouts:":
            in_layouts = True
            continue
        if in_layouts:
            if indent == 0:
                if current_id:
                    results.append((current_id, current_status))
                    current_id = ""
                    current_status = ""
                in_layouts = False
            elif stripped.startswith("- id:"):
                if current_id:
                    results.append((current_id, current_status))
                current_id = stripped.split(":", 1)[1].strip().strip("'\"")
                current_status = ""
            elif current_id and stripped.startswith("status:"):
                current_status = stripped.split(":", 1)[1].strip().strip("'\"")
    if current_id:
        results.append((current_id, current_status))
    return results


def parse_target_metadata(aircraft_dir: Path, root: Path) -> LaunchTargetInfo:
    config_path = aircraft_dir / "deckconfig" / "config.yaml"
    aircraft_name = aircraft_dir.name
    deck_names: list[str] = []
    config_ok = True
    config_error = ""
    version = ""
    icao = ""
    manifest_status = ""
    description = ""
    try:
        lines = config_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return LaunchTargetInfo(
            aircraft_name=aircraft_name,
            path=str(aircraft_dir),
            root=str(root),
            deck_count=0,
            deck_names=[],
            config_ok=False,
            config_error=str(exc),
        )
    inside_decks = False
    decks_indent = 0
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        if stripped.startswith("aircraft:"):
            value = stripped.split(":", 1)[1].strip()
            if value:
                aircraft_name = value.strip("'\"")
            continue
        if stripped == "decks:":
            inside_decks = True
            decks_indent = indent
            continue
        if inside_decks and indent <= decks_indent and not stripped.startswith("- "):
            inside_decks = False
        if inside_decks and stripped.startswith("- name:"):
            value = stripped.split(":", 1)[1].strip()
            if value:
                deck_names.append(value.strip("'\""))
    if not deck_names:
        config_ok = False
        config_error = "no deck entries found"
    has_manifest = False
    config_name = ""
    layout_infos: list[tuple[str, str]] = []
    manifest_path = aircraft_dir / "manifest.yaml"
    if manifest_path.is_file():
        has_manifest = True
        meta = parse_simple_yaml_meta(manifest_path)
        version = (meta.get("version") or "").strip()
        icao = (meta.get("icao") or "").strip()
        manifest_status = (meta.get("status") or "").strip()
        description = (meta.get("description") or "").strip()
        config_name = (meta.get("name") or "").strip()
        manifest_aircraft = (meta.get("aircraft") or config_name or "").strip()
        if manifest_aircraft:
            aircraft_name = manifest_aircraft
        layout_infos = parse_manifest_layouts(manifest_path)
    return LaunchTargetInfo(
        aircraft_name=aircraft_name,
        path=str(aircraft_dir),
        root=str(root),
        deck_count=len(deck_names),
        deck_names=deck_names,
        config_ok=config_ok,
        config_error=config_error,
        has_manifest=has_manifest,
        config_name=config_name,
        version=version,
        icao=icao,
        manifest_status=manifest_status,
        description=description,
        layout_infos=layout_infos,
    )


def launch_target_label(info: LaunchTargetInfo) -> str:
    root = Path(info.root)
    path = Path(info.path)
    try:
        rel_disp = path.relative_to(root).as_posix()
    except ValueError:
        rel_disp = path.name
    return f"{info.aircraft_name}  ·  {shorten_filesystem_path(root / rel_disp, max_len=78)}"


def discover_launch_targets() -> list[LaunchTargetInfo]:
    targets: list[LaunchTargetInfo] = []
    seen: set[str] = set()
    for root in cockpitdecks_search_roots():
        try:
            deckconfigs = sorted(root.rglob("deckconfig"))
        except OSError:
            continue
        for deckconfig_dir in deckconfigs:
            if not deckconfig_dir.is_dir() or not (deckconfig_dir / "config.yaml").exists():
                continue
            aircraft_dir = deckconfig_dir.parent
            try:
                resolved = str(aircraft_dir.resolve())
            except OSError:
                resolved = str(aircraft_dir)
            if resolved in seen:
                continue
            seen.add(resolved)
            targets.append(parse_target_metadata(Path(resolved), root))
    targets.sort(key=lambda item: (item.aircraft_name.lower(), item.path.lower()))
    return targets
