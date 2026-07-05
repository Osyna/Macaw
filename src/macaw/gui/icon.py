"""Macaw brand logo — the app's face across tray, settings, and overlay.

macaw.png is a tightly-cropped, transparent-background bird (no padding, no
backdrop), so scaling it to fill a small icon keeps the parrot large and clean.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap

_ASSETS = Path(__file__).resolve().parent.parent / "assets"
_SOURCE: QPixmap | None = None


def _source() -> QPixmap:
    global _SOURCE
    if _SOURCE is None:
        _SOURCE = QPixmap(str(_ASSETS / "macaw.png"))
    return _SOURCE


def logo_pixmap(size: int) -> QPixmap:
    """The Macaw scaled to fill a ``size``×``size`` transparent square."""
    art = _source().scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.drawPixmap((size - art.width()) // 2, (size - art.height()) // 2, art)
    p.end()
    return out


def logo_icon(size: int = 256) -> QIcon:
    return QIcon(logo_pixmap(size))


def create_tray_icon() -> QIcon:
    """System-tray icon (kept for import compatibility)."""
    return logo_icon(256)
