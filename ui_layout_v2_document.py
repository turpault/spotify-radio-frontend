"""Default UI layout as v2 whole-percent document (``merge`` input / export shape)."""
from __future__ import annotations

from typing import Any

UI_LAYOUT_V2_DOCUMENT: dict[str, Any] = {
    "version": 2,
    "description": (
        "elements + overlays: w,h: 0-100% of width and height (same for overlays). One of w or h may be "
        "null for a square (see null rules). x,y: null = center; <0 = from right/bottom. z: stack order. "
        "overlays sub_status_modal (dim status text) and volume_hud (volume flash) are rects in the same "
        "percent space as elements (central widget). Higher overlay z is drawn on top. All keys x,y,w,h,z required per rect."
    ),
    "elements": {
        "artwork": {"x": None, "y": 10, "w": 34, "h": 57, "z": 0},
        "prev": {"x": 33, "y": 57, "w": 8, "h": 10, "z": 40},
        "seek_back_30": {"x": 42, "y": 57, "w": 8, "h": 10, "z": 40},
        "seek_fwd_30": {"x": 50, "y": 57, "w": 8, "h": 10, "z": 40},
        "next": {"x": 58, "y": 57, "w": 8, "h": 10, "z": 40},
        "volume_up": {"x": 13, "y": 10, "w": 8, "h": 10, "z": 20},
        "volume_down": {"x": 13, "y": 21, "w": 8, "h": 10, "z": 20},
        "shuffle": {"x": 78, "y": 10, "w": 9, "h": 9, "z": 30},
        "repeat": {"x": 78, "y": 21, "w": 9, "h": 9, "z": 30},
        "playlist_0": {"x": 4, "y": 11, "w": 8, "h": 13, "z": 10},
        "playlist_1": {"x": 4, "y": 26, "w": 8, "h": 13, "z": 10},
        "playlist_2": {"x": 4, "y": 40, "w": 8, "h": 13, "z": 10},
        "playlist_3": {"x": 4, "y": 55, "w": 8, "h": 13, "z": 10},
        "playlist_4": {"x": 88, "y": 11, "w": 8, "h": 13, "z": 10},
        "playlist_5": {"x": 88, "y": 26, "w": 8, "h": 13, "z": 10},
        "playlist_6": {"x": 88, "y": 40, "w": 8, "h": 13, "z": 10},
        "playlist_7": {"x": 88, "y": 55, "w": 8, "h": 13, "z": 10},
        "title": {"x": None, "y": -13, "w": 93, "h": 6, "z": 50},
        "artist": {"x": None, "y": -10, "w": 93, "h": 5, "z": 50},
        "album": {"x": None, "y": -7, "w": 93, "h": 5, "z": 50},
        "progress": {"x": None, "y": -1, "w": 93, "h": 5, "z": 60},
    },
    "overlays": {
        "sub_status_modal": {"x": 0, "y": 0, "w": 100, "h": 100, "z": 0},
        "volume_hud": {"x": None, "y": 10, "w": 34, "h": 57, "z": 1},
    },
}
