"""Default UI layout as v2 whole-percent document (``merge`` input / export shape)."""
from __future__ import annotations

from typing import Any

playback_button_y=60
playback_button_x=33
playback_button_seek_offset_x=8.5
align_top = 3
playlist_x = 3
playlist_width = 10
playlist_height_offset = 21
volume_x = playback_shuffle_x = 15
volume_offset_y= 11

UI_LAYOUT_V2_DOCUMENT: dict[str, Any] = {
    "version": 2,
    "description": (
        "elements + overlays: w,h: 0-100% of width and height (same for overlays). One of w or h may be "
        "null for a square (see null rules). x,y: null = center; <0 = from right/bottom. z: stack order. "
        "overlays sub_status_modal (dim status text) and volume_hud (volume flash) are rects in the same "
        "percent space as elements (central widget). Higher overlay z is drawn on top. All keys x,y,w,h,z required per rect."
    ),
    "elements": {
        "artwork": {"x": None, "y": align_top, "w": None, "h": 72, "z": 0},
        "prev": {"x": playback_button_x, "y": playback_button_y, "w": 8, "h": 10, "z": 40},
        "seek_back_30": {"x": playback_button_x+playback_button_seek_offset_x, "y": playback_button_y, "w": 8, "h": 10, "z": 40},
        "seek_fwd_30": {"x": -playback_button_x-playback_button_seek_offset_x, "y": playback_button_y, "w": 8, "h": 10, "z": 40},
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
        "title": {"x": None, "y": -17, "w": 93, "h": 6, "z": 50},
        "artist": {"x": None, "y": -10.5, "w": 93, "h": 5, "z": 50},
        "album": {"x": None, "y": -6, "w": 93, "h": 5, "z": 50},
        "progress": {"x": None, "y": -1, "w": 93, "h": 5, "z": 60},
    },
    "overlays": {
        "sub_status_modal": {"x": 0, "y": 0, "w": 100, "h": 100, "z": 0},
        "volume_hud": {"x": None, "y": 10, "w": 34, "h": 57, "z": 1},
    },
}
