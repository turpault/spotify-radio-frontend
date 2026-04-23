"""
Load UI layout for MainWindow. The built-in v2 document is
:data:`~ui_layout_v2_document.UI_LAYOUT_V2_DOCUMENT`; optional JSON override: env
``JUKEBOX_UI_LAYOUT``. On disk, v2 ``w,h`` are **whole percent
0–100** of width/height (or **null**; see below). **null** on ``w`` or ``h`` (not both): a
**square**; side = the non-null value on its axis. **null** on ``x``/``y`` = center; negative
``x``/``y`` = inset from the right/bottom. In memory, fractions/``None``; v1: 0–1 floats. The
``overlays`` map uses the same rect rules; keys ``sub_status_modal`` and ``volume_hud`` place the
status dim layer and the volume flash HUD. Optional typography: document ``font`` with
``default`` (family, size, optional bold), ``elements`` / ``overlays`` partials, and optional
``font`` on any rect; ``elements.sub_label`` merges into overlay ``sub_status_modal`` for the status QLabel.
"""
from __future__ import annotations

import json
import os
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional

from ui_layout_v2_document import UI_FONT_DOCUMENT, UI_LAYOUT_V2_DOCUMENT

_log = logging.getLogger("gls-frontend.ui_layout")

_FALLBACK_FAMILY = "Corben"
_FALLBACK_SIZE = 14.0

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
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for k, r in el.items():
        rx = r.get("x")
        ry = r.get("y")
        rw = r.get("w")
        rh = r.get("h")
        out[k] = {
            "x": None if rx is None else _axis_frac_to_pct_int(float(rx)),
            "y": None if ry is None else _axis_frac_to_pct_int(float(ry)),
            "w": None if rw is None else _frac_to_pct_int(rw),
            "h": None if rh is None else _frac_to_pct_int(rh),
            "z": int(r["z"]),
        }
    return out


def _parse_opt_axis(v: Any) -> Optional[float]:
    if v is None:
        return None
    return float(v)


# For validating rectangles when w or h is null (square, side from non-null in own axis).
LAYOUT_REF_W = 2000.0
LAYOUT_REF_H = 1200.0


def _effective_wh_for_rect_fits(
    w: Optional[float], h: Optional[float], lim: float
) -> Optional[tuple[float, float]]:
    """
    In memory, w/h are 0-1 (v1) or 0-100 (v2) per ``lim``. If one of w,h is None, build a
    square: side from h (fraction of H) or w (fraction of W); the other is the other-axis
    extent so _rect_fits can check bounds. Returns (w_eff, h_eff) in same ``lim`` units.
    """
    if w is not None and h is not None:
        return (float(w), float(h))
    if w is None and h is None:
        return None
    a = w if h is None else h
    if a is None or a <= 0 or a > lim:
        return None
    if w is None and h is not None:
        # h drives; w as fraction of W = (h/lim)*Href/Wref in same space as a fraction of lim
        b = a * (LAYOUT_REF_H / LAYOUT_REF_W)
        return (b, float(h))
    b = a * (LAYOUT_REF_W / LAYOUT_REF_H)
    return (float(w), b)


def _rect_fits(
    x: Optional[float], y: Optional[float], w: float, h: float, lim: float
) -> bool:
    """lim is 1.0 (frac) or 100.0 (percent). None = center on that axis."""
    if w <= 0 or h <= 0 or w > lim or h > lim:
        return False
    if x is not None:
        if x >= 0 and x + w > lim + 0.5:
            return False
        if x < 0 and w + abs(x) > lim + 0.5:
            return False
    if y is not None:
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


def default_overlays() -> dict[str, dict[str, Any]]:
    """
    Overlay rects in the same 0-1 **fractions of the central widget** as ``elements``:
    full-window (or any rect) for status text; volume HUD defaults to the artwork region.
    """
    de = default_ui_elements()
    art = de["artwork"]
    return {
        "sub_status_modal": {
            "x": 0.0,
            "y": 0.0,
            "w": 1.0,
            "h": 1.0,
            "z": 0,
        },
        "volume_hud": {
            "x": art["x"],
            "y": art["y"],
            "w": art["w"],
            "h": art["h"],
            "z": 1,
        },
    }


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

OPTIONAL_ELEMENT_KEYS: tuple[str, ...] = ("sub_label", "playlist_tile")

REQUIRED_OVERLAY_KEYS: tuple[str, ...] = ("sub_status_modal", "volume_hud")


def _merge_font_spec(a: dict[str, Any], b: Any) -> dict[str, Any]:
    """Merge partial font dict ``b`` into ``a`` (family, size, optional bold, optional sub_size)."""
    out = dict(a)
    if not isinstance(b, dict):
        return out
    fam = b.get("family")
    if isinstance(fam, str) and fam.strip():
        out["family"] = fam.strip()
    if b.get("size") is not None:
        try:
            s = float(b["size"])
            if s > 0:
                out["size"] = s
        except (TypeError, ValueError):
            pass
    if b.get("sub_size") is not None:
        try:
            s = float(b["sub_size"])
            if s > 0:
                out["sub_size"] = s
        except (TypeError, ValueError):
            pass
    if isinstance(b.get("bold"), bool):
        out["bold"] = b["bold"]
    return out


def merge_font_document(override: Any) -> dict[str, Any]:
    """Start from built-in :data:`UI_FONT_DOCUMENT`, merge optional layout ``font`` object."""
    doc = deepcopy(UI_FONT_DOCUMENT)
    if not isinstance(override, dict):
        return doc
    if "default" in override:
        doc["default"] = _merge_font_spec(doc["default"], override["default"])
    for bucket in ("elements", "overlays"):
        raw = override.get(bucket)
        if not isinstance(raw, dict):
            continue
        bucket_out = doc.setdefault(bucket, {})
        for name, spec in raw.items():
            if not isinstance(spec, dict):
                continue
            prev = bucket_out.get(name)
            if not isinstance(prev, dict):
                prev = {}
            bucket_out[name] = _merge_font_spec(prev, spec)
    return doc


def _parse_inline_font(raw: Any) -> Optional[dict[str, Any]]:
    """Rect-level ``font`` object; returns None if empty / invalid."""
    if not isinstance(raw, dict):
        return None
    partial: dict[str, Any] = {}
    fam = raw.get("family")
    if isinstance(fam, str) and fam.strip():
        partial["family"] = fam.strip()
    if raw.get("size") is not None:
        try:
            s = float(raw["size"])
            if s > 0:
                partial["size"] = s
        except (TypeError, ValueError):
            pass
    if raw.get("sub_size") is not None:
        try:
            s = float(raw["sub_size"])
            if s > 0:
                partial["sub_size"] = s
        except (TypeError, ValueError):
            pass
    if isinstance(raw.get("bold"), bool):
        partial["bold"] = raw["bold"]
    return partial if partial else None


def _attach_inline_font(out: dict[str, Any], k: str, v: Any) -> None:
    if not isinstance(v, dict) or "font" not in v:
        return
    parsed = _parse_inline_font(v.get("font"))
    if parsed:
        out[k]["font"] = parsed
    elif v.get("font") is not None:
        _log.warning("ui layout: skip invalid font for %r", k)


_PLAYBACK_TRANSPORT_KEYS: frozenset[str] = frozenset(
    ("prev", "next", "seek_back_30", "seek_fwd_30")
)


def resolve_font_for_key(
    key: str,
    rect: Optional[dict[str, Any]],
    merged_doc: dict[str, Any],
    *,
    overlay: bool,
) -> dict[str, Any]:
    spec = dict(merged_doc["default"])
    bucket = merged_doc["overlays"] if overlay else merged_doc["elements"]
    if not overlay and key.startswith("playlist_"):
        pto = bucket.get("playlist_tile")
        if isinstance(pto, dict):
            spec = _merge_font_spec(spec, pto)
    if not overlay and key in _PLAYBACK_TRANSPORT_KEYS:
        pbs = bucket.get("playback_buttons")
        if isinstance(pbs, dict):
            spec = _merge_font_spec(spec, pbs)
    if overlay and key == "sub_status_modal":
        el_b = merged_doc.get("elements")
        if isinstance(el_b, dict) and "sub_label" in el_b:
            spec = _merge_font_spec(spec, el_b["sub_label"])
    if key in bucket:
        spec = _merge_font_spec(spec, bucket[key])
    if rect is not None and isinstance(rect.get("font"), dict):
        spec = _merge_font_spec(spec, rect["font"])
    if not isinstance(spec.get("family"), str) or not spec["family"].strip():
        spec["family"] = _FALLBACK_FAMILY
    try:
        sz = float(spec.get("size", 0))
        if sz <= 0:
            raise ValueError
    except (TypeError, ValueError):
        spec["size"] = _FALLBACK_SIZE
    return spec


def attach_resolved_fonts(
    elements: dict[str, dict[str, Any]],
    merged_doc: dict[str, Any],
    *,
    overlay: bool,
) -> None:
    for key, rect in elements.items():
        rect["font"] = resolve_font_for_key(key, rect, merged_doc, overlay=overlay)


def auxiliary_font_specs(
    merged_doc: dict[str, Any], elements: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Keys listed under ``font.elements`` that have no layout rect (e.g. sub_label)."""
    aux: dict[str, dict[str, Any]] = {}
    bucket = merged_doc.get("elements")
    if not isinstance(bucket, dict):
        return aux
    for name in bucket:
        if name not in elements:
            aux[name] = resolve_font_for_key(name, None, merged_doc, overlay=False)
    return aux


def _rect_ok_v1_fracs(r: dict[str, Any]) -> bool:
    if not all(k in r for k in ("x", "y", "w", "h", "z")):
        return False
    try:
        w = _parse_opt_axis(r["w"])
        h = _parse_opt_axis(r["h"])
    except (TypeError, ValueError):
        return False
    if w is None and h is None:
        return False
    if w is not None and (w <= 0 or w > 1.0):
        return False
    if h is not None and (h <= 0 or h > 1.0):
        return False
    try:
        x = _parse_opt_axis(r["x"])
        y = _parse_opt_axis(r["y"])
    except (TypeError, ValueError):
        return False
    eff = _effective_wh_for_rect_fits(w, h, 1.0)
    if eff is None:
        return False
    we, he = eff
    return _rect_fits(x, y, we, he, 1.0)


def _rect_ok_v2_percent(r: dict[str, Any]) -> bool:
    """0–100; null w/h = square; null x/y = center; negative = from R/B."""
    if not all(k in r for k in ("x", "y", "w", "h", "z")):
        return False
    try:
        w = _parse_opt_axis(r["w"])
        h = _parse_opt_axis(r["h"])
    except (TypeError, ValueError):
        return False
    if w is None and h is None:
        return False
    if w is not None and (w <= 0 or w > 100.0):
        return False
    if h is not None and (h <= 0 or h > 100.0):
        return False
    try:
        x = _parse_opt_axis(r["x"])
        y = _parse_opt_axis(r["y"])
    except (TypeError, ValueError):
        return False
    eff = _effective_wh_for_rect_fits(w, h, 100.0)
    if eff is None:
        return False
    we, he = eff
    return _rect_fits(x, y, we, he, 100.0)


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
                "x": None
                if v["x"] is None
                else float(v["x"]) / 100.0,
                "y": None
                if v["y"] is None
                else float(v["y"]) / 100.0,
                "w": None if v.get("w") is None else float(v["w"]) / 100.0,
                "h": None if v.get("h") is None else float(v["h"]) / 100.0,
                "z": z_merged,
            }
        else:
            out[k] = {
                "x": None if v["x"] is None else float(v["x"]),
                "y": None if v["y"] is None else float(v["y"]),
                "w": None if v.get("w") is None else float(v["w"]),
                "h": None if v.get("h") is None else float(v["h"]),
                "z": z_merged,
            }
        _attach_inline_font(out, k, v)
    return out


def merge_ui_overlays(
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
    raw = data.get("overlays")
    if not isinstance(raw, dict):
        return out
    is_v2 = version >= 2
    for k, v in raw.items():
        if k not in out or not isinstance(v, dict):
            continue
        valid = _rect_ok_v2_percent(v) if is_v2 else _rect_ok_v1_fracs(v)
        if not valid:
            _log.warning("ui layout: skip invalid overlay for %r", k)
            continue
        z_prev = int(out[k].get("z", 0))
        try:
            z_merged = int(v["z"])
        except (KeyError, TypeError, ValueError):
            z_merged = z_prev
        if is_v2:
            out[k] = {
                "x": None
                if v["x"] is None
                else float(v["x"]) / 100.0,
                "y": None
                if v["y"] is None
                else float(v["y"]) / 100.0,
                "w": None if v.get("w") is None else float(v["w"]) / 100.0,
                "h": None if v.get("h") is None else float(v["h"]) / 100.0,
                "z": z_merged,
            }
        else:
            out[k] = {
                "x": None if v["x"] is None else float(v["x"]),
                "y": None if v["y"] is None else float(v["y"]),
                "w": None if v.get("w") is None else float(v["w"]),
                "h": None if v.get("h") is None else float(v["h"]),
                "z": z_merged,
            }
        _attach_inline_font(out, k, v)
    return out


LAYOUT_PATH_ENV = "JUKEBOX_UI_LAYOUT"


def default_json_document() -> dict[str, Any]:
    for k in REQUIRED_ELEMENT_KEYS:
        if k not in UI_LAYOUT_V2_DOCUMENT["elements"]:
            _log.error("UI_LAYOUT_V2_DOCUMENT missing %s", k)
    odoc = UI_LAYOUT_V2_DOCUMENT.get("overlays")
    if not isinstance(odoc, dict):
        _log.error("UI_LAYOUT_V2_DOCUMENT missing overlays")
    else:
        for k in REQUIRED_OVERLAY_KEYS:
            if k not in odoc:
                _log.error("UI_LAYOUT_V2_DOCUMENT overlays missing %s", k)
    return deepcopy(UI_LAYOUT_V2_DOCUMENT)


def load_ui_layout() -> dict[str, Any]:
    """Return ``elements`` and ``overlays`` (0-1 fracs in memory), merged from the built-in or file."""
    defaults = default_ui_elements()
    odef = default_overlays()
    doc_builtin = deepcopy(UI_LAYOUT_V2_DOCUMENT)
    env = (os.environ.get(LAYOUT_PATH_ENV) or "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            try:
                with open(p, encoding="utf-8") as f:
                    doc = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                _log.warning("ui layout: cannot read %s (%s); using built-in", p, e)
            else:
                merged_font = merge_font_document(
                    doc.get("font") if isinstance(doc, dict) else None
                )
                elements = merge_ui_elements(doc, defaults)
                overlays = merge_ui_overlays(doc, odef)
                attach_resolved_fonts(elements, merged_font, overlay=False)
                attach_resolved_fonts(overlays, merged_font, overlay=True)
                return {
                    "elements": elements,
                    "overlays": overlays,
                    "font": merged_font,
                    "font_aux": auxiliary_font_specs(merged_font, elements),
                }
    merged_font = merge_font_document(doc_builtin.get("font"))
    elements = merge_ui_elements(doc_builtin, defaults)
    overlays = merge_ui_overlays(doc_builtin, odef)
    attach_resolved_fonts(elements, merged_font, overlay=False)
    attach_resolved_fonts(overlays, merged_font, overlay=True)
    return {
        "elements": elements,
        "overlays": overlays,
        "font": merged_font,
        "font_aux": auxiliary_font_specs(merged_font, elements),
    }
