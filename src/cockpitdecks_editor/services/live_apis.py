"""Lightweight HTTP probes for live status (X-Plane Web API, Cockpitdecks web UI)."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

DEFAULT_XPLANE_BASE = "http://127.0.0.1:8086"
DEFAULT_COCKPIT_WEB = "http://127.0.0.1:7777/"


def _unwrap_v3_payload(payload: dict[str, Any]) -> dict[str, Any]:
    inner = payload.get("data")
    if isinstance(inner, dict):
        return inner
    return payload


def _fetch_json(url: str, *, timeout: float) -> tuple[dict[str, Any] | None, str | None]:
    req = Request(url, headers={"Accept": "application/json"})
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        return None, "capabilities: not a JSON object"
    return data, None


def _xplane_capability_paths(api_version: str = "v3") -> list[str]:
    """Candidate capability endpoints (newest first, then compatibility fallbacks)."""
    primary = f"/api/{api_version}/capabilities"
    candidates = [
        primary,
        "/api/v3/capabilities",
        "/api/v2/capabilities",
        "/api/v1/capabilities",
        "/api/capabilities",
        "/capabilities",
    ]
    seen: set[str] = set()
    out: list[str] = []
    for c in candidates:
        if c not in seen:
            out.append(c)
            seen.add(c)
    return out


def fetch_xplane_capabilities_json(
    *,
    base_url: str = DEFAULT_XPLANE_BASE,
    api_version: str = "v3",
    timeout: float = 2.0,
) -> tuple[dict[str, Any] | None, str | None]:
    base = base_url.rstrip("/")
    tried_404: list[str] = []
    for path in _xplane_capability_paths(api_version=api_version):
        url = f"{base}{path}"
        try:
            data, err = _fetch_json(url, timeout=timeout)
            if err is not None:
                return None, f"{err} ({path})"
            return _unwrap_v3_payload(data), None
        except HTTPError as exc:
            if exc.code == 404:
                tried_404.append(path)
                continue
            return None, f"{exc} ({path})"
        except URLError as exc:
            return None, str(exc.reason)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            return None, str(exc)
    checked = ", ".join(tried_404) if tried_404 else "no endpoints"
    return None, f"HTTP Error 404: Not Found (checked {checked})"


def summarize_xplane_capabilities(caps: dict[str, Any]) -> str:
    parts: list[str] = []
    api = caps.get("api")
    if isinstance(api, dict):
        versions = api.get("versions")
        if isinstance(versions, list) and versions:
            parts.append("REST " + ",".join(str(v) for v in versions))
    xp = caps.get("x-plane") or caps.get("xplane")
    if isinstance(xp, dict):
        ver = xp.get("version")
        if ver is not None:
            parts.append(f"X-Plane {ver}")
        host = xp.get("hostname") or xp.get("host")
        if host:
            parts.append(str(host))
    if not parts:
        keys = sorted(caps.keys())
        if keys:
            parts.append("keys: " + ",".join(keys[:6]) + ("…" if len(keys) > 6 else ""))
        else:
            parts.append("(empty capabilities)")
    return " | ".join(parts)


def xplane_capabilities_status_line(
    *,
    base_url: str = DEFAULT_XPLANE_BASE,
    api_version: str = "v3",
    timeout: float = 2.0,
) -> tuple[str, str | None]:
    """Return (display_line, error_or_none)."""
    caps, err = fetch_xplane_capabilities_json(base_url=base_url, api_version=api_version, timeout=timeout)
    if err is not None:
        return f"unreachable ({err})", err
    return summarize_xplane_capabilities(caps), None


@dataclass
class SessionInfo:
    """Structured session data from /api/status."""
    version: str
    aircraft: str
    decks: str
    config_path: str
    error: str
    aircraft_path: str = ""
    decks_detail: list[dict] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.decks_detail is None:
            self.decks_detail = []

    @property
    def ok(self) -> bool:
        return not self.error

    def one_line(self) -> str:
        if self.error:
            return f"— ({self.error})"
        ver = f"v{self.version} | " if self.version else ""
        return f"{ver}{self.aircraft} | {self.decks} | {self.config_path}"


def fetch_session_info(*, base_url: str = "http://127.0.0.1:7777", timeout: float = 1.5) -> SessionInfo:
    """GET /api/status and return structured session info."""
    url = f"{base_url.rstrip('/')}/api/status"
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return SessionInfo("", "", "", "", "invalid JSON")
        name = (data.get("aircraft_name") or "").strip() or "—"
        dcp = (data.get("deckconfig_path") or "").strip() or "—"
        ver = (data.get("cockpitdecks_version") or "").strip()
        aircraft_path = (data.get("aircraft_path") or "").strip()
        # Prefer richer decks array; fall back to deck_names list
        decks_detail: list[dict] = []
        if isinstance(data.get("decks"), list):
            decks_detail = [d for d in data["decks"] if isinstance(d, dict)]
        deck_names = [d.get("name", "") for d in decks_detail] or data.get("deck_names") or []
        if deck_names:
            deck_part = f"{len(deck_names)} deck(s): {', '.join(str(d) for d in deck_names[:4])}" + ("…" if len(deck_names) > 4 else "")
        else:
            deck_part = "no decks"
        return SessionInfo(ver, name, deck_part, dcp, "", aircraft_path, decks_detail)
    except HTTPError as exc:
        if exc.code == 404:
            return SessionInfo("", "", "", "", "update Cockpitdecks: /api/status missing")
        return SessionInfo("", "", "", "", f"HTTP {exc.code}")
    except URLError:
        return SessionInfo("", "", "", "", "Cockpitdecks not running")
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return SessionInfo("", "", "", "", "could not read session")


def cockpitdecks_session_status_line(*, base_url: str = "http://127.0.0.1:7777", timeout: float = 1.5) -> str:
    """GET /desktop-status. Returns one-line summary or placeholder."""
    return fetch_session_info(base_url=base_url, timeout=timeout).one_line()


def cockpitdecks_metrics_json(*, base_url: str = "http://127.0.0.1:7777", timeout: float = 1.5) -> tuple[dict[str, Any] | None, str | None]:
    """GET /api/metrics and return parsed object."""
    url = f"{base_url.rstrip('/')}/api/metrics"
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None, "metrics: not a JSON object"
        return data, None
    except HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except URLError:
        return None, "Cockpitdecks not running"
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None, "could not read metrics"


def cockpitdecks_metrics_status_line(*, base_url: str = "http://127.0.0.1:7777", timeout: float = 1.5) -> str:
    """GET /desktop-metrics and summarize runtime/perf in one line."""
    data, err = cockpitdecks_metrics_json(base_url=base_url, timeout=timeout)
    if err is None and isinstance(data, dict):
        p = data.get("process") if isinstance(data.get("process"), dict) else {}
        c = data.get("cockpit") if isinstance(data.get("cockpit"), dict) else {}
        s = data.get("simulator") if isinstance(data.get("simulator"), dict) else {}
        cpu = p.get("cpu_percent")
        rss = p.get("max_rss_mb")
        thr = p.get("thread_count")
        vars_n = c.get("registered_variables")
        drefs = s.get("datarefs_monitored")
        parts: list[str] = []
        if isinstance(cpu, (int, float)):
            parts.append(f"CPU {cpu:.1f}%")
        if isinstance(rss, (int, float)):
            parts.append(f"RSS {rss:.1f} MB")
        if isinstance(thr, int):
            parts.append(f"threads {thr}")
        if isinstance(vars_n, int):
            parts.append(f"vars {vars_n}")
        if isinstance(drefs, int):
            parts.append(f"drefs {drefs}")
        return " | ".join(parts) if parts else "— (no metrics yet)"
    if err == "HTTP 404":
        return "— (update Cockpitdecks: /api/metrics missing)"
    return f"— ({err})" if err else "— (could not read metrics)"


def reload_decks(*, base_url: str = "http://127.0.0.1:7777", timeout: float = 5.0) -> tuple[bool, str]:
    """GET /api/reload to trigger a full config reload. Returns (ok, message)."""
    url = f"{base_url.rstrip('/')}/api/reload"
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
        status = data.get("status", "") if isinstance(data, dict) else ""
        if status == "ok":
            return True, "Decks reloaded successfully"
        return True, f"Reload responded: {status or raw[:120]}"
    except HTTPError as exc:
        if exc.code == 404:
            return False, "Cockpitdecks too old: /api/reload endpoint missing"
        return False, f"HTTP {exc.code}"
    except URLError:
        return False, "Cockpitdecks not running"
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        return False, str(exc)


def set_target(target: str, *, base_url: str = "http://127.0.0.1:7777", timeout: float = 5.0) -> tuple[bool, str]:
    """POST /api/target to switch aircraft. Returns (ok, message)."""
    url = f"{base_url.rstrip('/')}/api/target"
    body = json.dumps({"target": target}).encode("utf-8")
    try:
        req = Request(url, data=body, headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
        status = data.get("status", "") if isinstance(data, dict) else ""
        message = data.get("message", "") if isinstance(data, dict) else ""
        if status in ("ok", "saved"):
            return True, message or f"Target set to {target}"
        return False, message or f"Unexpected response: {raw[:120]}"
    except HTTPError as exc:
        if exc.code == 400:
            try:
                err = json.loads(exc.read().decode("utf-8"))
                return False, err.get("message", f"HTTP 400")
            except Exception:
                return False, "Invalid target path"
        if exc.code == 404:
            return False, "Cockpitdecks too old: /api/target endpoint missing"
        return False, f"HTTP {exc.code}"
    except URLError:
        return False, "Cockpitdecks not running"
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        return False, str(exc)

def reload_deck(deck_name: str, *, base_url: str = "http://127.0.0.1:7777", timeout: float = 5.0) -> tuple[bool, str]:
    """POST /api/deck/<name>/reload. Returns (ok, message)."""
    encoded_deck_name = quote(deck_name, safe="")
    url = f"{base_url.rstrip('/')}/api/deck/{encoded_deck_name}/reload"
    try:
        req = Request(url, data=b"", headers={"Accept": "application/json"}, method="POST")
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
        status = data.get("status", "") if isinstance(data, dict) else ""
        if status == "ok":
            return True, f"Deck {deck_name} reloaded"
        return False, data.get("message", f"Unexpected response: {raw[:120]}")
    except HTTPError as exc:
        if exc.code == 404:
            return False, f"Deck {deck_name} not found or API missing"
        return False, f"HTTP {exc.code}"
    except URLError:
        return False, "Cockpitdecks not running"
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        return False, str(exc)


def render_button_preview(
    deck_name: str,
    button_yaml: str,
    *,
    base_url: str = "http://127.0.0.1:7777",
    timeout: float = 5.0,
) -> tuple[bytes | None, dict[str, Any] | None, str | None]:
    """POST /preview and return decoded PNG bytes plus render metadata."""
    url = f"{base_url.rstrip('/')}/preview"
    body = json.dumps({"deck": deck_name, "code": button_yaml}).encode("utf-8")
    try:
        req = Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None, None, "preview: not a JSON object"
        image_data = data.get("image")
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        if not isinstance(image_data, str) or not image_data.strip():
            if isinstance(meta, dict) and meta.get("error"):
                return None, meta, str(meta.get("error"))
            return None, meta, "preview: no image returned"
        try:
            decoded = base64.b64decode(image_data.encode("ascii"), validate=False)
        except (ValueError, UnicodeEncodeError) as exc:
            return None, meta, f"preview decode failed: {exc}"
        return decoded, meta, None
    except HTTPError as exc:
        if exc.code == 404:
            return None, None, "Cockpitdecks preview API missing"
        return None, None, f"HTTP {exc.code}"
    except URLError:
        return None, None, "Cockpitdecks not running"
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        return None, None, str(exc)


def cockpitdecks_web_status_line(*, url: str = DEFAULT_COCKPIT_WEB, timeout: float = 1.5) -> tuple[str, str | None]:
    """Cheap check: GET / and discard body (Flask returns HTML)."""
    try:
        req = Request(url, headers={"Accept": "*/*"})
        with urlopen(req, timeout=timeout) as resp:
            _ = resp.read(512)
        code = getattr(resp, "status", None) or resp.getcode()
        return f"OK (HTTP {code})", None
    except HTTPError as exc:
        return f"unreachable (HTTP {exc.code})", str(exc)
    except URLError as exc:
        return f"unreachable ({exc.reason})", str(exc.reason)
    except (OSError, ValueError) as exc:
        return f"unreachable ({exc})", str(exc)
