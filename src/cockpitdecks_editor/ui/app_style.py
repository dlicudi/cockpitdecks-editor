"""Shared Qt stylesheets (Fusion + QSS for consistent buttons on macOS/Windows)."""

from __future__ import annotations

# Fusion + QSS: native "macintosh" style often ignores most button QSS, yielding flat ugly controls.
MAIN_WINDOW_QSS = """
QFrame#actionBar {
    background-color: #fafafb;
    border: 1px solid #e2e5eb;
    border-radius: 10px;
}

QPushButton {
    background-color: #ffffff;
    border: 1px solid #c5c9d3;
    border-radius: 6px;
    padding: 5px 14px;
    min-width: 80px;
    min-height: 0;
    font-weight: 500;
    color: #1c1c1e;
}

QPushButton:hover {
    background-color: #f2f3f6;
    border-color: #aeb4bf;
}

QPushButton:pressed {
    background-color: #e8eaef;
    border-color: #9da3af;
}

QPushButton:focus {
    outline: none;
}

QPushButton:disabled {
    color: #a1a6b0;
    background-color: #f4f5f7;
    border-color: #dde0e6;
}

QPushButton#primaryButton {
    background-color: #2563eb;
    color: #ffffff;
    border: 1px solid #1d4ed8;
    font-weight: 600;
}

QPushButton#primaryButton:hover {
    background-color: #1d4ed8;
    border-color: #1e40af;
}

QPushButton#primaryButton:pressed {
    background-color: #1e40af;
}

QPushButton#primaryButton:disabled {
    background-color: #93b4f7;
    border-color: #93b4f7;
    color: #e8eefc;
}

QPushButton#stopButton {
    color: #b91c1c;
    font-weight: 600;
}

QPushButton#stopButton:hover {
    background-color: #fef2f2;
    border-color: #aeb4bf;
}

QPushButton#stopButton:pressed {
    background-color: #fee2e2;
}

QPushButton#stopButton:disabled {
    color: #a1a6b0;
    background-color: #f4f5f7;
    border-color: #dde0e6;
}
"""
