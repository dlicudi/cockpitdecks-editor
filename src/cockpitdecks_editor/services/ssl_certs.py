"""Helpers to make HTTPS certificate verification work in frozen builds."""

from __future__ import annotations

import os
import ssl
import sys
from pathlib import Path

import certifi


def configure_default_ssl_ca_bundle() -> str | None:
    """Point Python's default SSL verification at certifi's CA bundle.

    Frozen Windows builds often do not locate a usable trust store automatically.
    Setting these environment variables early keeps urllib-based services working.
    """
    try:
        cafile = Path(certifi.where()).resolve()
    except Exception:
        return None

    if not cafile.is_file():
        return None

    cafile_str = str(cafile)
    os.environ.setdefault("SSL_CERT_FILE", cafile_str)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", cafile_str)

    try:
        ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=cafile_str)  # type: ignore[attr-defined]
    except Exception:
        if getattr(sys, "frozen", False):
            print(f"[ssl] warning: unable to override default SSL context using {cafile_str}", file=sys.stderr)

    return cafile_str
