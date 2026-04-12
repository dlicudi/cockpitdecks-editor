"""Persisted settings for Cockpitdecks Desktop.

Shared runtime settings are stored in Cockpitdecks' canonical config.yaml so
CLI and Desktop mode use the same source of truth. Desktop-only launcher/UI
preferences remain in settings.json.
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path

import yaml

SHARED_DEFAULTS: dict[str, str] = {
    "COCKPITDECKS_PATH": "",
    "COCKPITDECKS_TARGET": "",
    "SIMULATOR_HOST": "",
    "API_HOST": "127.0.0.1",
    "API_PORT": "8086",
    "COCKPIT_WEB_HOST": "127.0.0.1",
    "COCKPIT_WEB_PORT": "7777",
}

DESKTOP_ONLY_DEFAULTS: dict[str, str] = {
    "COCKPITDECKS_LAUNCHER_PATH": "",
    "COCKPITDECKS_LAUNCHER_USE_CUSTOM": "0",
    "COCKPITDECKS_LAUNCH_LOG_PATH": "",
    "COCKPITDECKS_LOG_LEVEL": "INFO",
    "EDITOR_EXPERT_MODE": "0",
}

DEFAULTS: dict[str, str] = {**SHARED_DEFAULTS, **DESKTOP_ONLY_DEFAULTS}


def _desktop_config_dir() -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "CockpitdecksEditor"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local"))) / "CockpitdecksEditor"
    return home / ".config" / "cockpitdecks-editor"


def cockpitdecks_config_dir() -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Cockpitdecks"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local"))) / "Cockpitdecks"
    return home / ".config" / "cockpitdecks"


def settings_path() -> Path:
    return _desktop_config_dir() / "settings.json"


def runtime_config_path() -> Path:
    return cockpitdecks_config_dir() / "config.yaml"


def managed_decks_dir() -> Path:
    return _desktop_config_dir() / "decks"


def _normalize_port(raw: str, default: str) -> int:
    try:
        return int((raw or "").strip() or default)
    except (TypeError, ValueError):
        return int(default)


def _split_paths(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        items = [str(item or "").strip() for item in raw if str(item or "").strip()]
        if sys.platform == "win32" and len(items) == 2 and re.fullmatch(r"[A-Za-z]", items[0]) and items[1].startswith("\\"):
            return [f"{items[0]}:{items[1]}"]
        return items
    text = str(raw).strip()
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = ast.literal_eval(text)
        except (SyntaxError, ValueError):
            parsed = None
        if isinstance(parsed, (list, tuple)):
            return _split_paths(parsed)
    if sys.platform == "win32":
        parts = [chunk.strip() for chunk in text.split(";") if chunk.strip()]
        if len(parts) > 1:
            return parts
        m = re.match(r"^\['([A-Za-z])',\s*'\\(.*)'\]$", text)
        if m:
            return [f"{m.group(1)}:\\{m.group(2)}"]
        return [text]
    return [chunk.strip() for chunk in text.replace(";", ":").split(":") if chunk.strip()]


def _join_paths(paths: list[str]) -> str:
    return os.pathsep.join(str(p).strip() for p in paths if str(p).strip())


def _load_desktop_only() -> dict[str, str]:
    path = settings_path()
    data: dict[str, str] = {k: str(v) for k, v in DESKTOP_ONLY_DEFAULTS.items()}
    if not path.exists():
        return data
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            for k in DESKTOP_ONLY_DEFAULTS:
                if k in raw and raw[k] is not None:
                    data[k] = str(raw[k]).strip()
            old_mode = str(raw.get("COCKPITDECKS_LAUNCHER_MODE") or "").strip().lower()
            if old_mode in ("dev", "custom"):
                data["COCKPITDECKS_LAUNCHER_USE_CUSTOM"] = "1"
            old_dev = str(raw.get("COCKPITDECKS_LAUNCHER_PATH_DEV") or "").strip()
            if old_dev and not data["COCKPITDECKS_LAUNCHER_PATH"]:
                data["COCKPITDECKS_LAUNCHER_PATH"] = old_dev
    except (OSError, json.JSONDecodeError):
        pass
    return data


def _load_runtime_raw() -> tuple[dict, bool]:
    path = runtime_config_path()
    if not path.exists():
        return {}, False
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return (raw if isinstance(raw, dict) else {}), True
    except Exception:
        return {}, True


def _flatten_runtime(raw: dict) -> dict[str, str]:
    xplane_api = raw.get("xplane_api") if isinstance(raw.get("xplane_api"), dict) else {}
    server = raw.get("cockpitdecks_server") if isinstance(raw.get("cockpitdecks_server"), dict) else {}
    return {
        "COCKPITDECKS_PATH": _join_paths(_split_paths(raw.get("deck_paths"))),
        "COCKPITDECKS_TARGET": str(raw.get("target") or "").strip(),
        "SIMULATOR_HOST": str(raw.get("simulator_host") or "").strip(),
        "API_HOST": str(xplane_api.get("host") or SHARED_DEFAULTS["API_HOST"]).strip() or SHARED_DEFAULTS["API_HOST"],
        "API_PORT": str(xplane_api.get("port") or SHARED_DEFAULTS["API_PORT"]).strip(),
        "COCKPIT_WEB_HOST": str(server.get("host") or SHARED_DEFAULTS["COCKPIT_WEB_HOST"]).strip() or SHARED_DEFAULTS["COCKPIT_WEB_HOST"],
        "COCKPIT_WEB_PORT": str(server.get("port") or SHARED_DEFAULTS["COCKPIT_WEB_PORT"]).strip(),
    }


def _save_desktop_only(values: dict[str, str]) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {k: (values.get(k) or "").strip() for k in DESKTOP_ONLY_DEFAULTS}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _save_runtime(values: dict[str, str]) -> None:
    path = runtime_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing, _ = _load_runtime_raw()
    doc = dict(existing) if isinstance(existing, dict) else {}
    doc["deck_paths"] = _split_paths(values.get("COCKPITDECKS_PATH", ""))
    doc["target"] = (values.get("COCKPITDECKS_TARGET") or "").strip() or None
    doc["simulator_host"] = (values.get("SIMULATOR_HOST") or "").strip() or None

    xplane_api = dict(doc.get("xplane_api") or {}) if isinstance(doc.get("xplane_api"), dict) else {}
    xplane_api["host"] = (values.get("API_HOST") or SHARED_DEFAULTS["API_HOST"]).strip() or SHARED_DEFAULTS["API_HOST"]
    xplane_api["port"] = _normalize_port(values.get("API_PORT", SHARED_DEFAULTS["API_PORT"]), SHARED_DEFAULTS["API_PORT"])
    doc["xplane_api"] = xplane_api

    server = dict(doc.get("cockpitdecks_server") or {}) if isinstance(doc.get("cockpitdecks_server"), dict) else {}
    server["host"] = (values.get("COCKPIT_WEB_HOST") or SHARED_DEFAULTS["COCKPIT_WEB_HOST"]).strip() or SHARED_DEFAULTS["COCKPIT_WEB_HOST"]
    server["port"] = _normalize_port(values.get("COCKPIT_WEB_PORT", SHARED_DEFAULTS["COCKPIT_WEB_PORT"]), SHARED_DEFAULTS["COCKPIT_WEB_PORT"])
    doc["cockpitdecks_server"] = server

    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def load() -> dict[str, str]:
    desktop = _load_desktop_only()
    runtime_raw, runtime_exists = _load_runtime_raw()
    runtime = _flatten_runtime(runtime_raw)
    data: dict[str, str] = {k: str(v) for k, v in DEFAULTS.items()}
    data.update(desktop)
    data.update(runtime)

    if not runtime_exists:
        path = settings_path()
        try:
            raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, json.JSONDecodeError):
            raw = {}
        if isinstance(raw, dict):
            legacy_found = False
            for key in SHARED_DEFAULTS:
                if key in raw and raw[key] is not None:
                    data[key] = str(raw[key]).strip()
                    legacy_found = True
            if legacy_found:
                try:
                    _save_runtime(data)
                except OSError:
                    pass
    return data


def save(values: dict[str, str]) -> None:
    merged = load()
    for key in DEFAULTS:
        if key in values and values[key] is not None:
            merged[key] = str(values[key]).strip()
    _save_desktop_only(merged)
    _save_runtime(merged)


def launch_env_overlay(values: dict[str, str] | None = None) -> dict[str, str]:
    """Environment variables to merge when spawning cockpitdecks."""
    v = values or load()
    out: dict[str, str] = {"COCKPITDECKS_ENGINE": "1"}
    log_level = (v.get("COCKPITDECKS_LOG_LEVEL") or "INFO").strip().upper()
    if log_level:
        out["COCKPITDECKS_LOG_LEVEL"] = log_level
    return out


def xplane_rest_base(values: dict[str, str] | None = None) -> str:
    v = values or load()
    host = (v.get("API_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port = (v.get("API_PORT") or "8086").strip() or "8086"
    return f"http://{host}:{port}"


def cockpit_web_base(values: dict[str, str] | None = None) -> str:
    v = values or load()
    host = (v.get("COCKPIT_WEB_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port = (v.get("COCKPIT_WEB_PORT") or "7777").strip() or "7777"
    return f"http://{host}:{port}"


def launcher_binary_path(values: dict[str, str] | None = None) -> Path | None:
    v = values or load()
    if (v.get("COCKPITDECKS_LAUNCHER_USE_CUSTOM") or "0").strip() != "1":
        return None
    raw = (v.get("COCKPITDECKS_LAUNCHER_PATH") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()
