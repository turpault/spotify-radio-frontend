"""Load bundled UI fonts (OFL) via Qt; helpers for stylesheet family names."""
from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtGui import QFontDatabase

_log = logging.getLogger("gls-frontend.fonts")

FONTS_DIR = Path(__file__).resolve().parent / "fonts"

# Filenames from https://github.com/google/fonts (OFL); see fonts/OFL-*.txt
BUNDLED_FONT_FILES: tuple[str, ...] = (
    "Limelight-Regular.ttf",
    "Corben-Regular.ttf",
    "ShareTechMono-Regular.ttf",
)


def load_bundled_fonts() -> list[str]:
    """
    Register all bundled TTFs with Qt. Call after QApplication exists.
    Returns PostScript / family names reported by Qt (for logging).
    """
    reported: list[str] = []
    for name in BUNDLED_FONT_FILES:
        path = FONTS_DIR / name
        if not path.is_file():
            _log.warning("bundled font missing: %s", path)
            continue
        fid = QFontDatabase.addApplicationFont(str(path))
        if fid < 0:
            _log.warning("addApplicationFont failed for %s", path)
            continue
        families = QFontDatabase.applicationFontFamilies(fid)
        if families:
            reported.append(families[0])
            _log.info("loaded font %s -> %s", path.name, families[0])
        else:
            _log.warning("no families for %s", path)
    return reported


def qss_font_family(name: str) -> str:
    """Quote a family name for Qt stylesheets (spaces, punctuation)."""
    n = (name or "").strip().replace("\\", "\\\\").replace('"', '\\"')
    return f'"{n}"'
