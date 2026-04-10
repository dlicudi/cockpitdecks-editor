"""Load the application window icon from package resources or PyInstaller bundle."""

from __future__ import annotations

import importlib.resources
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPixmap


def _pixmap_to_square(pix: QPixmap, *, max_side: int = 1024) -> QPixmap:
    """Pad non-square artwork to a square and clip to a macOS squircle with transparent corners."""
    if pix.isNull():
        return pix
    
    w, h = pix.width(), pix.height()
    side = max(w, h)
    
    # Base canvas is ALWAYS transparent to ensure clean corners
    canvas = QPixmap(side, side)
    canvas.fill(Qt.GlobalColor.transparent)
    
    # If the original isn't square, we center it.
    # But since we're clipping to a squircle anyway, we'll draw it centered.
    scaled = pix.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    
    # --- Clip to Rounded Square (Squircle) ---
    # 82% is the standard macOS "Big Sur" style icon scale relative to the canvas.
    icon_side = int(side * 0.82)
    radius = int(icon_side * 0.18) # Standard macOS squircleish radius
    
    out = QPixmap(side, side)
    out.fill(Qt.GlobalColor.transparent)
    
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    
    offset = (side - icon_side) // 2
    path = QPainterPath()
    path.addRoundedRect(offset, offset, icon_side, icon_side, radius, radius)
    painter.setClipPath(path)
    
    # Draw the scaled image into the clipped area
    img_x = (side - scaled.width()) // 2
    img_y = (side - scaled.height()) // 2
    painter.drawPixmap(img_x, img_y, scaled)
    painter.end()

    if out.width() > max_side:
        return out.scaled(max_side, max_side, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    return out


def _read_icon_bytes() -> bytes | None:
    """Resolve PNG bytes from checkout / bundle (avoid stale importlib.resources from old installs)."""
    here = Path(__file__).resolve().parent
    candidates = [
        here / "resources" / "app_icon.png",
    ]
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        meipass = Path(sys._MEIPASS)  # noqa: SLF001
        candidates.extend(
            [
                meipass / "cockpitdecks_editor" / "resources" / "app_icon.png",
                meipass / "resources" / "app_icon.png",
            ]
        )

    for path in candidates:
        if path.is_file():
            try:
                return path.read_bytes()
            except OSError:
                continue

    try:
        ref = importlib.resources.files("cockpitdecks_editor.resources").joinpath("app_icon.png")
        return ref.read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, TypeError, OSError):
        return None


def load_app_icon() -> QIcon | None:
    """Return QIcon for dock / window chrome, or None if asset missing."""
    data = _read_icon_bytes()
    if data is None:
        return None
    pix = QPixmap()
    if not pix.loadFromData(data):
        return None
    pix = _pixmap_to_square(pix)
    return QIcon(pix)
