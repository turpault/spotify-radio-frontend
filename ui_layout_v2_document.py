"""Default UI layout as v2 whole-percent document (``merge`` input / export shape)."""
from __future__ import annotations

from typing import Any

playback_button_y=54
playback_button_x=18
playback_button_seek_offset_x=8.5
playback_button_seek_offset_y=11
align_top = 3
playlist_x = 1.5
playlist_width = 13
playlist_height_offset = 23.5
volume_x = playback_shuffle_x = 18
volume_offset_y= 11

# Typography defaults (merged with optional layout JSON ``font`` and per-rect ``font``).
# Bundled TTFs: Limelight, Corben, Share Tech Mono (see font_loader / fonts/).
UI_FONT_DOCUMENT: dict[str, Any] = {
    "default": {"family": "Corben", "size": 14},
    "elements": {
        "title": {"family": "Limelight", "size": 20, "bold": True},
        "artist": {"size": 15},
        "album": {"size": 14},
        # Applies to the status / error line (same QLabel as overlay sub_status_modal).
        "sub_label": {"size": 12},
        "progress": {"family": "Share Tech Mono", "size": 15, "bold": True},
        # prev / next / seek_back_30 / seek_fwd_30 (QPushButton#ArtTransportBtn); per-button rect can override.
        "playback_buttons": {"family": "Share Tech Mono", "size": 18, "bold": True},
        "artwork": {"size": 18},
        # Merged into each ``playlist_0``…``playlist_7`` rect (override per rect with inline ``font``).
        "playlist_tile": {"size": 11, "bold": True},
    },
    "overlays": {
        "volume_hud": {"family": "Share Tech Mono", "size": 44},
        "sub_status_modal": {},
    },
}

UI_LAYOUT_V2_DOCUMENT: dict[str, Any] = {
    "version": 2,
    "description": (
        "elements + overlays: w,h: 0-100% of width and height (same for overlays). One of w or h may be "
        "null for a square (see null rules). x,y: null = center; <0 = from right/bottom. z: stack order. "
        "overlays sub_status_modal (dim status text) and volume_hud (volume flash) are rects in the same "
        "percent space as elements (central widget). Higher overlay z is drawn on top. All keys x,y,w,h,z required per rect. "
        "Optional top-level \"font\": { \"default\": { \"family\", \"size\", optional \"bold\" }, "
        "\"elements\" / \"overlays\": { \"<name>\": partial font } } — e.g. "
        "``playback_buttons`` applies to ``prev`` / ``next`` / ``seek_back_30`` / ``seek_fwd_30``. "
        "Each rect may include optional "
        "\"font\" to override. Sizes are design-time units scaled like other UI px (see main UI_DISPLAY_SCALE)."
    ),
    "font": {},
    "elements": {
        "artwork": {"x": None, "y": align_top, "w": None, "h": 72, "z": 0},
        "prev": {"x": playback_button_x, "y": playback_button_y, "w": 8, "h": 10, "z": 40},
        "seek_back_30": {"x": playback_button_x, "y": playback_button_y+playback_button_seek_offset_y, "w": 8, "h": 10, "z": 40},
        "seek_fwd_30": {"x": -playback_button_x, "y": playback_button_y+playback_button_seek_offset_y, "w": 8, "h": 10, "z": 40},
        "next": {"x": -playback_button_x, "y": playback_button_y, "w": 8, "h": 10, "z": 40},
        "volume_up": {"x": volume_x, "y": align_top, "w": 8, "h": 10, "z": 20},
        "volume_down": {"x": volume_x, "y": align_top+volume_offset_y, "w": 8, "h": 10, "z": 20},
        "shuffle": {"x": -playback_shuffle_x, "y": align_top, "w": 9, "h": 9, "z": 30},
        "repeat": {"x": -playback_shuffle_x, "y": align_top+volume_offset_y, "w": 9, "h": 9, "z": 30},
        "playlist_0": {"x": playlist_x, "y": align_top, "w": playlist_width, "h": None, "z": 10},
        "playlist_1": {"x": playlist_x, "y": align_top+playlist_height_offset, "w": playlist_width, "h": None, "z": 10},
        "playlist_2": {"x": playlist_x, "y": align_top+playlist_height_offset*2, "w": playlist_width, "h": None, "z": 10},
        "playlist_3": {"x": playlist_x, "y": align_top+playlist_height_offset*3, "w": playlist_width, "h": None, "z": 10},
        "playlist_4": {"x": -playlist_x, "y": align_top, "w": playlist_width, "h": None, "z": 10},
        "playlist_5": {"x": -playlist_x, "y": align_top+playlist_height_offset, "w": playlist_width, "h": None, "z": 10},
        "playlist_6": {"x": -playlist_x, "y": align_top+playlist_height_offset*2, "w": playlist_width, "h": None, "z": 10},
        "playlist_7": {"x": -playlist_x, "y": align_top+playlist_height_offset*3, "w": playlist_width, "h": None, "z": 10},
        "title": {"x": None, "y": -15, "w": 65, "h": 12, "z": 50},
        "artist": {"x": None, "y": -10.5, "w": 65, "h": 8, "z": 50},
        "album": {"x": None, "y": -6, "w": 65, "h": 8, "z": 50},
        "progress": {"x": None, "y": -1, "w": 65, "h": 5, "z": 60},
    },
    "overlays": {
        "sub_status_modal": {"x": 0, "y": 0, "w": 100, "h": 100, "z": 0},
        "volume_hud": {"x": None, "y": 10, "w": 34, "h": 57, "z": 1},
    },
}
