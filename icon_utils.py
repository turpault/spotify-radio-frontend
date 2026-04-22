"""Render Lucide SVG (ISC) icons as QIcons with a fixed stroke color."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QByteArray, QRectF, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer


def svg_colored_icon(svg_path: Path, color_hex: str, logical_size: int) -> QIcon:
    """Load an SVG, replace currentColor with ``color_hex``, rasterize to a square pixmap."""
    raw = svg_path.read_text(encoding="utf-8")
    raw = raw.replace("currentColor", color_hex)
    renderer = QSvgRenderer(QByteArray(raw.encode("utf-8")))
    d = max(1, int(logical_size))
    pm = QPixmap(d, d)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(p, QRectF(0, 0, float(d), float(d)))
    p.end()
    return QIcon(pm)
