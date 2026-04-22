"""
Load UI layout for MainWindow. On disk (v2) ``w,h`` and positive ``x,y`` are **whole
percent 0–100** of the central (rounded). **Negative** ``x`` or ``y`` is the distance
from the right or bottom edge to the widget’s right or bottom, as a **positive** percent
(value stored negative in the file, e.g. ``-5`` = 5% from the edge). In memory,
``load_ui_layout()`` feeds fractions to the window. Version 1 uses 0–1 floats (same sign rule).
"""
from __future__ import annotations

import json
import os
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional

_log = logging.getLogger("gls-frontend.ui_layout")

# Keep in sync with main.py
UI_DISPLAY_SCALE = 3.0
ART_SIZE_MULT = 1.6128


def _s(n: float) -> int:
    return max(1, int(round(n * UI_DISPLAY_SCALE)))


def _btn(n: float) -> int:
    return max(1, int(round(_s(n) * 0.5)))


def _n(W: float, H: float, x: float, y: float, w: float, h: float) -> dict[str, float]:
    return {
        "x": x / W,
        "y": y / H,
        "w": w / W,
        "h": h / H,
    }


def _frac_to_pct_int(v: float) -> int:
    """Round positive fraction in [0,1] to nearest whole percent in [0, 100] (w, h)."""
    return int(max(0, min(100, round(float(v) * 100.0))))


def _axis_frac_to_pct_int(v: float) -> int:
    """x / y: round to whole percent; negative = anchor to right (x) or bottom (y). Clamped to ±100."""
    return int(max(-100, min(100, round(float(v) * 100.0))))


def _elements_to_json_percent(
    el: dict[str, dict[str, Any]],
) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for k, r in el.items():
        out[k] = {
            "x": _axis_frac_to_pct_int(r["x"]),
            "y": _axis_frac_to_pct_int(r["y"]),
            "w": _frac_to_pct_int(r["w"]),
            "h": _frac_to_pct_int(r["h"]),
            "z": int(r["z"]),
        }
    return out


def _rect_fits(
    x: float, y: float, w: float, h: float, lim: float
) -> bool:
    """lim is 1.0 (frac) or 100.0 (percent). Negative x/y = inset from R/B: |x|+w <= lim, |y|+h <= lim."""
    if w <= 0 or h <= 0 or w > lim or h > lim:
        return False
    if x >= 0 and x + w > lim + 0.5:
        return False
    if x < 0 and w + abs(x) > lim + 0.5:
        return False
    if y >= 0 and y + h > lim + 0.5:
        return False
    if y < 0 and h + abs(y) > lim + 0.5:
        return False
    return True


# Stacking: lower z is painted first (further back); higher z is on top. Ties: sorted by name.
def _Z_ORDER() -> dict[str, int]:
    return {
        "artwork": 0,
        **{f"playlist_{i}": 10 for i in range(8)},
        "volume_up": 20,
        "volume_down": 20,
        "shuffle": 30,
        "repeat": 30,
        "prev": 40,
        "seek_back_30": 40,
        "seek_fwd_30": 40,
        "next": 40,
        "title": 50,
        "artist": 50,
        "album": 50,
        "sub_label": 55,
        "progress": 60,
    }


def default_ui_elements() -> dict[str, dict[str, Any]]:
    """
    Reproduce the pre-config layout as fractions of a reference 2000×1200
    client (used only to convert former pixel math to 0-1; scales with any size).
    """
    W, H = 2000.0, 1200.0
    m = float(_s(24))
    iw = W - 2.0 * m
    ih = H - 2.0 * m
    h5 = 0.05 * ih
    h65 = 0.65 * ih
    h20 = 0.20 * ih
    h10 = max(1.0, ih - h5 - h65 - h20)

    y0 = m
    y_hero = y0 + h5
    y_info = y0 + h5 + h65
    y_prog = y0 + h5 + h65 + h20

    pl_w = float(_btn(108))
    vol_w = float(_btn(100))
    mode_w = float(max(_btn(100), _btn(70) + 2 * _btn(8) + 2 * _btn(3) + _s(12)))
    gap = float(_btn(20))
    ihw = iw

    x_pl = m
    x_vol = m + pl_w + gap
    x_c = m + pl_w + gap + vol_w + gap
    cw = ihw - 2.0 * pl_w - vol_w - mode_w - 4.0 * gap
    x_mode = x_c + cw + gap
    x_pl_r = x_mode + mode_w + gap

    side_rails = int(vol_w + mode_w)
    p_cols = int(2 * pl_w)
    hero_gaps = 4 * int(gap)
    max_w = int(W - 2 * m) - p_cols - side_rails - hero_gaps
    hero_h = int(h65)
    cap_w2 = int(cw)
    cap_w = min(max_w, hero_h) if cap_w2 == 0 else min(max_w, hero_h, cap_w2)
    fit = max(0, cap_w)
    bmin = 2 * _s(8) + 4 * _btn(72) + 3 * _s(4)
    side = int(round(fit * float(ART_SIZE_MULT)))
    side = max(bmin, side) if bmin else side
    side = min(side, max_w, hero_h, cap_w2, 2400) if cap_w2 else min(side, max_w, hero_h, 2400)
    side = max(120, int(side))
    if hero_h and side > hero_h:
        side = int(hero_h)
    if cap_w2 and side > cap_w2:
        side = int(cap_w2)

    art_x = x_c + (cw - float(side)) / 2.0
    art_y = y_hero + (h65 - float(side)) / 2.0

    out: dict[str, dict[str, Any]] = {}
    out["artwork"] = _n(W, H, art_x, art_y, float(side), float(side))

    tpad = float(_s(8))
    trans_h = float(2 * _s(8) + _btn(50))
    y_bar = art_y + float(side) - trans_h
    sp = float(_s(4))
    inner_w = float(side) - 2.0 * tpad - 3.0 * sp
    btn_w4 = max(1.0, inner_w / 4.0)
    names = ("prev", "seek_back_30", "seek_fwd_30", "next")
    for i, name in enumerate(names):
        xb = art_x + tpad + i * (btn_w4 + sp)
        out[name] = _n(W, H, xb, y_bar, btn_w4, trans_h)

    vbtn_h = float(_btn(78))
    vsp = float(_btn(10))
    out["volume_up"] = _n(W, H, x_vol, y_hero, vol_w, vbtn_h)
    out["volume_down"] = _n(W, H, x_vol, y_hero + vbtn_h + vsp, vol_w, vbtn_h)

    msp = float(_btn(12))
    mbtn = float(_btn(70))
    out["shuffle"] = _n(W, H, x_mode, y_hero, mode_w, mbtn)
    out["repeat"] = _n(W, H, x_mode, y_hero + mbtn + msp, mode_w, mbtn)

    m_top = float(_s(2))
    g8 = float(_s(8))
    n = 4
    body = h65 - m_top
    avail = max(0.0, body - (n - 1) * g8)
    per = int(avail // n) if n else 0
    per = max(_s(20), int(per))
    if n * per + (n - 1) * g8 + m_top > h65:
        per = max(16, int((body - (n - 1) * g8) // n) if n else 16)
    per = int(min(int(per), int(pl_w)))
    for i in range(4):
        yy = y_hero + m_top + i * (float(per) + g8)
        out[f"playlist_{i}"] = _n(W, H, x_pl, yy, pl_w, float(per))
    for i in range(4):
        yy = y_hero + m_top + i * (float(per) + g8)
        out[f"playlist_{4 + i}"] = _n(W, H, x_pl_r, yy, pl_w, float(per))

    h_info = h20
    t_h = 0.20 * h_info
    a_h = 0.18 * h_info
    al_h = 0.16 * h_info
    sub_h = 0.12 * h_info
    yp = y_info + 0.10 * h_info
    out["title"] = _n(W, H, m, yp, iw, t_h)
    yp += t_h
    out["artist"] = _n(W, H, m, yp, iw, a_h)
    yp += a_h
    out["album"] = _n(W, H, m, yp, iw, al_h)
    yp += al_h
    out["sub_label"] = _n(W, H, m, yp, iw, sub_h)

    ph = max(0.1 * h10, 20.0)
    py0 = y_prog + max(0.0, 0.5 * h10 - 0.5 * ph)
    out["progress"] = _n(W, H, m, py0, iw, ph)

    zord = _Z_ORDER()
    for k in out:
        out[k]["z"] = zord.get(k, 0)

    return out


REQUIRED_ELEMENT_KEYS: tuple[str, ...] = (
    *(f"playlist_{i}" for i in range(8)),
    "volume_up",
    "volume_down",
    "shuffle",
    "repeat",
    "artwork",
    "prev",
    "seek_back_30",
    "seek_fwd_30",
    "next",
    "title",
    "artist",
    "album",
    "progress",
)

OPTIONAL_ELEMENT_KEYS: tuple[str, ...] = ("sub_label",)


def _rect_ok_v1_fracs(r: dict[str, Any]) -> bool:
    try:
        x = float(r["x"])
        y = float(r["y"])
        w = float(r["w"])
        h = float(r["h"])
    except (KeyError, TypeError, ValueError):
        return False
    return _rect_fits(x, y, w, h, 1.0)


def _rect_ok_v2_percent(r: dict[str, Any]) -> bool:
    """0–100; negative x = offset from right, negative y = from bottom (same as v1 in percent)."""
    try:
        x = float(r["x"])
        y = float(r["y"])
        w = float(r["w"])
        h = float(r["h"])
    except (KeyError, TypeError, ValueError):
        return False
    return _rect_fits(x, y, w, h, 100.0)


def merge_ui_elements(
    data: Any, defaults: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    out = deepcopy(defaults)
    if not isinstance(data, dict):
        return out
    version = 1
    try:
        vraw = data.get("version", 1)
        version = int(vraw) if vraw is not None else 1
    except (TypeError, ValueError):
        version = 1
    if version < 1:
        version = 1
    raw = data.get("elements") if "elements" in data else data
    if not isinstance(raw, dict):
        return out
    is_v2 = version >= 2
    for k, v in raw.items():
        if k not in out or not isinstance(v, dict):
            continue
        valid = _rect_ok_v2_percent(v) if is_v2 else _rect_ok_v1_fracs(v)
        if not valid:
            _log.warning("ui layout: skip invalid rect for %r", k)
            continue
        z_prev = int(out[k].get("z", 0))
        try:
            z_merged = int(v["z"])
        except (KeyError, TypeError, ValueError):
            z_merged = z_prev
        if is_v2:
            out[k] = {
                "x": float(v["x"]) / 100.0,
                "y": float(v["y"]) / 100.0,
                "w": float(v["w"]) / 100.0,
                "h": float(v["h"]) / 100.0,
                "z": z_merged,
            }
        else:
            out[k] = {
                "x": float(v["x"]),
                "y": float(v["y"]),
                "w": float(v["w"]),
                "h": float(v["h"]),
                "z": z_merged,
            }
    return out


LAYOUT_PATH_ENV = "JUKEBOX_UI_LAYOUT"


def default_json_document() -> dict[str, Any]:
    d = default_ui_elements()
    for k in REQUIRED_ELEMENT_KEYS:
        if k not in d:
            _log.error("default_ui_elements missing %s", k)
    pct = _elements_to_json_percent(d)
    return {
        "version": 2,
        "description": (
            "w,h: 0-100% of width/height. "
            "x: >=0 = offset from left; <0 = |x|% from the right to this widget’s right edge. "
            "y: >=0 = from top; <0 = |y|% from bottom to this widget’s bottom. "
            "z: stack order (lower = back)."
        ),
        "elements": pct,
    }


def config_search_paths() -> list[Path]:
    base = Path(__file__).resolve().parent
    return [
        Path(os.environ.get(LAYOUT_PATH_ENV, "") or base / "ui_layout.json").expanduser(),
    ]


def load_ui_layout() -> dict[str, dict[str, Any]]:
    defaults = default_ui_elements()
    p = config_search_paths()[0]
    if p.is_file():
        try:
            with open(p, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            _log.warning("ui layout: cannot read %s (%s); using code defaults", p, e)
        else:
            return merge_ui_elements(doc, defaults)
    else:
        _log.info("ui layout: no file at %s; using code defaults", p)
    return deepcopy(defaults)
