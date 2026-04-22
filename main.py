#!/usr/bin/env python3
"""
PyQt6 touchscreen UI for a local go-librespot daemon: REST + WebSocket (/events).

Expects the API on http://127.0.0.1:3678 by default. Override with GOLIBRESPOT_BASE.

Layout: built-in v2 in ``ui_layout_v2_document.UI_LAYOUT_V2_DOCUMENT``; override via
``JUKEBOX_UI_LAYOUT`` JSON. ``elements`` and ``overlays`` use the same ``w,h`` / ``x,y`` rules
(percent of the **central** widget; one null on w|h → square). Overlays: ``sub_status_modal`` and
``volume_hud`` (higher ``z`` above lower ``z``). Daemon / error / buffering use ``SubStatusModal``. Eight side tiles (four per side) show the last **eight distinct playlist (context) URIs**; metadata and art
are saved under the data directory (``JUKEBOX_GLS_DATA_DIR`` or
``~/.config/jukebox-frontend-go-librespot/``). Tap a tile to start that URI via the local player
API (no Spotify Web / Connect REST client).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from functools import partial
from pathlib import Path
from typing import Any, Optional

from PyQt6.QtCore import (
    QAbstractAnimation,
    QPoint,
    QPropertyAnimation,
    QSize,
    Qt,
    QTimer,
    QUrl,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import (
    QCloseEvent,
    QCursor,
    QFont,
    QIcon,
    QKeySequence,
    QMouseEvent,
    QPixmap,
    QResizeEvent,
    QShortcut,
)
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWebSockets import QWebSocket
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gls_client import GlsApiError, GlsConfig, get_json, post_json
from icon_utils import svg_colored_icon
from playback_history import _MAX_ENTRIES, HistoryItem, PlaybackHistory
from ui_layout_config import load_ui_layout

_log = logging.getLogger("gls-frontend")

# go-librespot uses {"type": "...", "data": ...} (cmd/daemon/api_server.go ApiEvent).

# Global display scale: 3.0 = 300% of design-time base sizes (fonts, controls, spacing).
UI_DISPLAY_SCALE = 3.0

# Cover: multiply min(width, height) fit. ih/metadata fix is the main size win.
ART_SIZE_MULT = 1.6128  # 1.344 × 1.2 (another 20% larger central artwork)
# Hard cap in window pixels (must match AlbumArtLabel.set_art_viewport max side).
ART_SIDE_MAX = 2400


def _s(n: float) -> int:
    """Scale a layout size (px, pt) by UI_DISPLAY_SCALE; minimum 1 pixel."""
    return max(1, int(round(n * UI_DISPLAY_SCALE)))


def _btn(n: float) -> int:
    """Half of _s(n); all QPushButton / transport rail / mode icon sizes use this (50% vs prior UI)."""
    return max(1, int(round(_s(n) * 0.5)))


_ICONS_DIR = Path(__file__).resolve().parent / "icons"

_PLAYLIST_INSET = 0.03  # fraction of tile; margin on all sides for cover art area


def _center_cover_pixmap(
    source: QPixmap, tw: int, th: int
) -> QPixmap:
    """Scale to **cover** tw×th (crop overflow), center-cropped; smooth."""
    if source.isNull() or tw < 1 or th < 1:
        return source
    scaled = source.scaled(
        tw,
        th,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    sw, sh = scaled.width(), scaled.height()
    if sw < tw or sh < th:
        return source.scaled(
            tw,
            th,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    x0 = (sw - tw) // 2
    y0 = (sh - th) // 2
    return scaled.copy(x0, y0, tw, th)


# Vintage radio: warm walnut shell, cream dial text, brass accents (bakelite-style keys).
_STYLE_ALBUM_PLACEHOLDER = (
    f"background-color: #1a1510; color: #5a5048; border: {_s(3)}px solid #8b7355; "
    f"border-radius: {_s(8)}px; font-size: {_s(18)}px; "
    "font-family: Palatino, 'Times New Roman', serif;"
)


class AlbumArtLabel(QLabel):
    """Album cover; left-click toggles play/pause (same as main play control)."""

    clicked = pyqtSignal()

    def __init__(self, w: int = 400, h: int = 400) -> None:
        super().__init__()
        self._art_w = w
        self._art_h = h
        self._raw_pix: Optional[QPixmap] = None
        self.setFixedSize(w, h)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(_STYLE_ALBUM_PLACEHOLDER)
        self.setText("—")
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._nam = QNetworkAccessManager(self)
        self._active_reply: Optional[QNetworkReply] = None
        self._pause_visible = False
        self._pause_overlay = QLabel(self)
        self._pause_overlay.setObjectName("pauseOverlay")
        self._pause_overlay.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self._pause_overlay.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        )
        self._pause_overlay.setText("\u23f8")  # ⏸
        self._pause_overlay.setStyleSheet(
            "background-color: rgba(8, 6, 4, 0.58); color: #f8f0e0;"
        )
        self._pause_overlay.hide()

    def set_pause_overlay_visible(self, show: bool) -> None:
        self._pause_visible = show
        if show:
            self._pause_overlay.show()
            self._pause_overlay.raise_()
        else:
            self._pause_overlay.hide()
        self._layout_pause_overlay()

    def _layout_pause_overlay(self) -> None:
        self._pause_overlay.setGeometry(0, 0, self.width(), self.height())
        d = int(min(self._art_w, self._art_h))
        ps = max(14, int(d * 0.24))
        f = self._pause_overlay.font()
        f.setPointSize(ps)
        f.setBold(True)
        f.setStyleHint(QFont.StyleHint.SansSerif)
        self._pause_overlay.setFont(f)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._layout_pause_overlay()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def set_art_url(self, url: Optional[str]) -> None:
        # Clear ref before abort(): abort() may synchronously emit finished and clear
        # _active_reply in _on_art_finished, which would make the next deleteLater crash.
        old = self._active_reply
        self._active_reply = None
        if old is not None:
            old.abort()  # finished disposes the reply; do not deleteLater here
        if not url:
            self._raw_pix = None
            self.clear()
            self.setText("—")
            return
        self.setText("")
        req = QNetworkRequest(QUrl(url))
        req.setRawHeader(b"User-Agent", b"JukeboxGoLibrespot/1.0")
        self._active_reply = self._nam.get(req)
        self._active_reply.finished.connect(self._on_art_finished)

    def _on_art_finished(self) -> None:
        reply = self.sender()
        if not isinstance(reply, QNetworkReply):
            return
        if reply is not self._active_reply:
            # Superseded or cleared before a new request; do not clobber the current image
            reply.deleteLater()
            return
        if reply.error() != QNetworkReply.NetworkError.NoError:
            reply.deleteLater()
            self._active_reply = None
            self._raw_pix = None
            self.clear()
            self.setText("—")
            return
        data = reply.readAll()
        reply.deleteLater()
        self._active_reply = None
        pix = QPixmap()
        if not pix.loadFromData(bytes(data)):
            self._raw_pix = None
            self.clear()
            self.setText("—")
            return
        self._raw_pix = pix
        self._redraw_from_raw()

    def _redraw_from_raw(self) -> None:
        if self._raw_pix is None or self._raw_pix.isNull():
            return
        scaled = self._raw_pix.scaled(
            self._art_w,
            self._art_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(scaled)

    def set_art_viewport(self, w: int, h: int) -> None:
        """Set fixed view port for the art (contain). Window-pixel bounds via UI scale."""
        w = max(64, min(ART_SIDE_MAX, int(w)))
        h = max(64, min(ART_SIDE_MAX, int(h)))
        if w == self._art_w and h == self._art_h:
            return
        self._art_w = w
        self._art_h = h
        self.setFixedSize(w, h)
        if self._raw_pix is not None and not self._raw_pix.isNull():
            self._redraw_from_raw()
        self._layout_pause_overlay()
        if self._pause_visible:
            self._pause_overlay.raise_()


class ArtworkFrameHost(QWidget):
    """
    Cover only; size/position from UI layout. A centered min(w,h) square is used
    for the pixmap so a non-square rect still shows correct aspect.
    """

    def __init__(self, parent: QWidget, album: AlbumArtLabel) -> None:
        super().__init__(parent)
        self._album = album
        album.setParent(self)
        self.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        aw, ah = self.width(), self.height()
        if aw < 1 or ah < 1:
            return
        s = int(min(aw, ah))
        s = max(1, s)
        x = (aw - s) // 2
        y = (ah - s) // 2
        self._album.set_art_viewport(s, s)
        self._album.setGeometry(x, y, s, s)


class VolumeOverlay(QFrame):
    """Fullscreen dim + centered macOS-style volume HUD (large level + bar)."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setObjectName("volumeOverlay")
        self.setStyleSheet(
            f"""
            #volumeOverlay {{ background-color: rgba(20, 14, 10, 0.72); }}
            QFrame#volumeHudCard {{
                background-color: rgba(52, 42, 34, 248);
                border-radius: {_s(20)}px;
                border: {_s(3)}px solid #9a7b4a;
            }}
            QLabel#hudPercent {{ color: #f0e6d4; font-weight: 600; }}
            QProgressBar {{
                border: 1px solid #5a4a38; border-radius: {_s(5)}px; background: #1a1410; height: {_s(14)}px;
            }}
            QProgressBar::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #a68428, stop:0.5 #d4a83c, stop:1 #8a6a20); border-radius: {_s(3)}px; }}
            """
        )
        self._icon = QLabel("🔊")
        ic = QFont()
        ic.setPointSize(_s(56))
        self._icon.setFont(ic)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._bar = QProgressBar()
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(_s(14))
        self._bar.setMinimumWidth(_s(420))
        self._bar.setMaximumWidth(_s(520))
        self._pct = QLabel("0")
        self._pct.setObjectName("hudPercent")
        pf = QFont()
        pf.setPointSize(_s(44))
        pf.setBold(True)
        self._pct.setFont(pf)
        self._pct.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sub = QLabel("")
        self._sub.setStyleSheet(
            f"color: rgba(200, 185, 160, 0.75); font-size: {_s(14)}px;"
        )
        self._sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._card = QFrame()
        self._card.setObjectName("volumeHudCard")
        self._inner = QVBoxLayout(self._card)
        self._inner.setContentsMargins(_s(40), _s(36), _s(40), _s(36))
        self._inner.setSpacing(_s(20))
        self._inner.addWidget(self._icon, alignment=Qt.AlignmentFlag.AlignCenter)
        self._inner.addWidget(self._pct, alignment=Qt.AlignmentFlag.AlignCenter)
        self._inner.addWidget(self._bar, alignment=Qt.AlignmentFlag.AlignCenter)
        self._inner.addWidget(self._sub, alignment=Qt.AlignmentFlag.AlignCenter)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self._card, alignment=Qt.AlignmentFlag.AlignCenter)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)

        self._fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fx)
        self._fx.setOpacity(1.0)
        self.hide()
        for w in (self, *self.findChildren(QWidget)):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.refit_to_bounds()

    def refit_to_bounds(self) -> None:
        """Scale HUD card to match artwork-sized overlay (not full window)."""
        w, h = self.width(), self.height()
        if w < 8 or h < 8:
            return
        d = int(min(w, h))
        m = max(_s(8), min(_s(36), d // 8))
        self._inner.setContentsMargins(m, m, m, m)
        sp = max(_s(6), min(_s(20), d // 16))
        self._inner.setSpacing(sp)
        bar_w = max(_s(48), min(int(d * 0.72), w - 2 * m))
        self._bar.setMinimumWidth(int(bar_w * 0.4))
        self._bar.setMaximumWidth(bar_w)
        self._bar.setFixedHeight(max(_s(8), min(_s(16), d // 28)))
        icf = self._icon.font()
        icf.setPointSize(max(10, int(d * 0.1)))
        self._icon.setFont(icf)
        pf = self._pct.font()
        pf.setPointSize(max(12, int(d * 0.12)))
        self._pct.setFont(pf)
        st = max(7, int(d * 0.04))
        self._sub.setStyleSheet(
            f"color: rgba(200, 185, 160, 0.75); font-size: {st}px;"
        )

    def set_level(self, value: int, max_v: int) -> None:
        max_v = max(1, int(max_v))
        value = int(max(0, min(max_v, value)))
        pct = int(round(100.0 * value / max_v)) if max_v else 0
        self._bar.setRange(0, max_v)
        self._bar.setValue(value)
        self._pct.setText(f"{pct}%")
        self._sub.setText(f"{value} / {max_v}")


class SubStatusModal(QFrame):
    """
    Full-window dim layer with centered status / error text, above the main central stack
    and below the volume HUD. Clicks pass through to controls below.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("subStatusModal")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(
            f"#subStatusModal {{ background-color: rgba(10, 8, 6, 0.78); border: none; }}"
        )
        self.label = QLabel("", self)
        self.label.setObjectName("subStatusModalLabel")
        self.label.setWordWrap(True)
        self.label.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        )
        self.label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(_s(24), _s(24), _s(24), _s(24))
        outer.setSpacing(0)
        outer.addStretch(1)
        row = QHBoxLayout()
        row.setSpacing(0)
        row.addStretch(1)
        row.addWidget(self.label, 0, Qt.AlignmentFlag.AlignCenter)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)
        self.hide()
        for w in (self, *self.findChildren(QWidget)):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        w = max(1, self.width())
        self.label.setMaximumWidth(int(w * 0.88))


def _fg_post(path: str, body: Optional[dict[str, Any]], cfg: GlsConfig) -> None:
    post_json(path, body, cfg=cfg)


class HistoryTile(QWidget):
    """Recent playlist: artwork or icon only; tap to play that URI. Tooltip has track/playlist text."""

    play_requested = pyqtSignal(str)

    def __init__(
        self,
        parent: Optional[QWidget],
        tile_icon: QIcon,
        icon_px: int,
    ) -> None:
        super().__init__(parent)
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )
        self._vlay = QVBoxLayout(self)
        self._vlay.setContentsMargins(0, 0, 0, 0)
        self._vlay.setSpacing(0)
        self._play_uri = ""
        self._raw_cover: Optional[QPixmap] = None
        self._btn = QToolButton()
        self._btn.setObjectName("PlaylistTile")
        self._btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._btn.setIcon(tile_icon)
        self._btn.setIconSize(QSize(max(1, int(icon_px)), max(1, int(icon_px))))
        self._btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._btn.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self._btn.clicked.connect(self._on_btn)
        self._vlay.addWidget(self._btn, 1)
        self._fallback_icon = tile_icon
        self._apply_empty()

    def refit(self, col_w: int, row_h: int) -> None:
        """Match icon to layout rect (json-driven geometry)."""
        w = int(max(8, col_w))
        h = int(max(8, row_h))
        self.setFixedSize(w, h)
        m = int(max(0, round(w * _PLAYLIST_INSET)))
        m2 = int(max(0, round(h * _PLAYLIST_INSET)))
        self._vlay.setContentsMargins(m, m2, m, m2)
        # After layout, refresh cover fill size.
        QTimer.singleShot(0, self._refresh_playlist_tile_art)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._refresh_playlist_tile_art()

    def _draw_wh(self) -> tuple[int, int]:
        w, h = self._btn.width(), self._btn.height()
        return (max(1, w), max(1, h))

    def _refresh_playlist_tile_art(self) -> None:
        aw, ah = self._draw_wh()
        if aw < 2 or ah < 2:
            return
        if self._raw_cover is not None and not self._raw_cover.isNull():
            filled = _center_cover_pixmap(self._raw_cover, aw, ah)
            self._btn.setIcon(QIcon(filled))
            self._btn.setIconSize(QSize(aw, ah))
            return
        pm = self._fallback_icon.pixmap(
            QSize(aw, ah),
            QIcon.Mode.Normal,
            QIcon.State.Off,
        )
        if pm is not None and not pm.isNull():
            p2 = _center_cover_pixmap(pm, aw, ah)
            self._btn.setIcon(QIcon(p2))
            self._btn.setIconSize(QSize(aw, ah))

    def _on_btn(self) -> None:
        u = (self._play_uri or "").strip()
        if u.startswith("spotify:"):
            self.play_requested.emit(u)

    def _apply_empty(self) -> None:
        self._play_uri = ""
        self._raw_cover = None
        self._btn.setToolTip("")
        self._btn.setIcon(self._fallback_icon)
        self._btn.setEnabled(False)
        self._refresh_playlist_tile_art()

    def set_history_item(
        self,
        item: Optional[HistoryItem],
        history: PlaybackHistory,
        tile_icon: QIcon,
    ) -> None:
        self._fallback_icon = tile_icon
        if item is None:
            self._apply_empty()
            return
        u = item.play_uri()
        if not u.startswith("spotify:"):
            self._apply_empty()
            return
        self._play_uri = u
        art = ", ".join(item.artist_names) if item.artist_names else ""
        pl = (item.context_uri or "").strip()
        tip = f"{(item.name or '—').strip()}"
        if art:
            tip = f"{tip}\n{art}"
        if (item.album_name or "").strip():
            tip = f"{tip}\n{(item.album_name or '').strip()}"
        if pl:
            tip = f"{tip}\n{pl}"
        self._btn.setToolTip(tip.strip())
        cp = history.resolve_cover(item)
        if cp is not None and cp.is_file():
            pix = QPixmap(str(cp))
            if not pix.isNull():
                self._raw_cover = pix
                self._btn.setEnabled(True)
                self._refresh_playlist_tile_art()
                return
        self._raw_cover = None
        self._btn.setIcon(tile_icon)
        self._btn.setEnabled(True)
        self._refresh_playlist_tile_art()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._cfg = GlsConfig.from_env()
        self._vol_max: int = 100
        self._vol_value: int = 0
        self._last_hud_val: Optional[int] = None
        self._volume_overlay: Optional[VolumeOverlay] = None
        self._hud_hide_timer = QTimer(self)
        self._hud_hide_timer.setSingleShot(True)
        self._hud_hide_timer.setInterval(1800)
        self._hud_hide_timer.timeout.connect(self._begin_hud_fade)
        self._hud_fade: Optional[QPropertyAnimation] = None
        self._history = PlaybackHistory()
        self._last_context_uri: str = ""
        self._last_tr_for_history: Optional[dict[str, Any]] = None
        # repeat: 0=off, 1=one track, 2=whole context
        self._repeat_mode: int = 0
        self._is_playing = False
        self._is_paused = False
        self._duration_ms = 0
        self._position_ms = 0
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(2000)
        self._status_timer.timeout.connect(self._request_status_bg)
        self._tick = QTimer(self)
        self._tick.setInterval(500)
        self._tick.timeout.connect(self._on_tick)

        _log.info(
            "window init: GOLIBRESPOT_BASE=%s (override with env GOLIBRESPOT_BASE)",
            self._cfg.base,
        )
        _log.info(
            "recent tracks: data dir %s (override JUKEBOX_GLS_DATA_DIR)",
            self._history.data_dir(),
        )
        self._build_ui()
        self._wire_shortcuts()
        QTimer.singleShot(0, self._layout_reflow)
        self._apply_history_tiles()

        self._ws = QWebSocket()
        self._ws.connected.connect(self._on_ws_connected)
        self._ws.textMessageReceived.connect(self._on_ws_text)
        self._try_connect_ws_error()
        self._ws.disconnected.connect(self._on_ws_disconnected)
        self._ws_connect_timer = QTimer(self)
        self._ws_connect_timer.setSingleShot(True)
        self._ws_connect_timer.setInterval(2000)
        self._ws_connect_timer.timeout.connect(self._connect_websocket)
        self._connect_websocket()
        self._request_status_bg()
        self._status_timer.start()

    def _try_connect_ws_error(self) -> None:
        # Qt 6.5+ uses errorOccurred; avoid legacy error to prevent duplicate handlers.
        if hasattr(self._ws, "errorOccurred"):
            self._ws.errorOccurred.connect(self._on_ws_error)

    @pyqtSlot()
    def _on_ws_error(self, *_args: object) -> None:
        _log.warning("WebSocket error (will retry)")

    def _build_ui(self) -> None:
        self.setWindowTitle("go-librespot")
        b = _btn
        self.setStyleSheet(
            f"""
            QMainWindow, QWidget {{ background-color: #241a14; color: #e8dcc4; border: none; }}
            QLabel {{ background: transparent; color: #e8dcc4; border: none; font-family: Palatino, Georgia, serif; }}
            QWidget#artFrame {{
                background: transparent;
                border: 1px solid rgba(100, 88, 70, 0.4);
                border-radius: {b(10)}px;
            }}
            QPushButton#ArtTransportBtn {{
                min-width: 0; min-height: 0; max-width: 99999px; max-height: 99999px;
                background: rgba(255, 250, 240, 0.10);
                color: #f5ecd8;
                border: {b(2)}px solid rgba(220, 200, 170, 0.42);
                border-radius: {b(10)}px;
                font-size: {b(18)}px;
                font-weight: bold;
                padding: {b(2)}px {b(4)}px;
            }}
            QPushButton#ArtTransportBtn:hover:enabled {{
                background: rgba(255, 250, 240, 0.18);
                border-color: rgba(230, 210, 180, 0.55);
                color: #fffaf0;
            }}
            QPushButton#ArtTransportBtn:pressed:enabled {{
                background: rgba(0, 0, 0, 0.22);
            }}
            QPushButton#ArtTransportBtn:disabled {{
                color: #5a4a3a;
                background: rgba(255, 250, 240, 0.05);
                border-color: #4a3a2a;
            }}
            QPushButton {{
                background-color: #3a2e22;
                color: #f5ecd8;
                border: {b(3)}px solid #8a7558;
                border-radius: {b(14)}px;
                font-size: {b(22)}px;
                font-weight: bold;
                padding: {b(12)}px {b(20)}px;
                min-height: {b(44)}px;
                min-width: {b(44)}px;
                font-family: Palatino, Georgia, serif;
            }}
            QPushButton:hover {{ background-color: #4a3c30; border-color: #c9a43a; color: #fffaf0; }}
            QPushButton:pressed {{ background-color: #241a10; border-color: #6a5a40; color: #e8dcc4; }}
            QPushButton:disabled {{ color: #6a5a50; background-color: #2a2018; border-color: #4a4034; }}
            QPushButton#VolumeStepBtn {{
                min-width: 0;
                min-height: 0;
                max-width: 99999px;
                max-height: 99999px;
                padding: {b(8)}px;
                background-color: #1e1810;
                color: #e8dcc4;
                border: {b(3)}px solid #6a5a45;
                border-radius: {b(10)}px;
            }}
            QPushButton#VolumeStepBtn:hover:enabled {{
                background-color: #2a2218;
                border-color: #b09050;
            }}
            QPushButton#IconTransport {{
                min-width: 0; min-height: 0; max-width: 99999px; max-height: 99999px; padding: {b(8)}px;
                background-color: #1e1810;
                color: #e8dcc4;
                border: {b(3)}px solid #6a5a45;
                border-radius: {b(10)}px;
            }}
            QPushButton#IconTransport:hover {{ background-color: #2a2218; border-color: #b09050; color: #fff8e8; }}
            QPushButton#IconTransport:checked {{
                background-color: #4a3610;
                color: #ffe8a0;
                border: {b(3)}px solid #e0b020;
            }}
            QPushButton#RepeatCycle {{
                min-width: 0; min-height: 0; max-width: 99999px; max-height: 99999px; padding: {b(8)}px; border-radius: {b(10)}px;
                color: #e8dcc4;
            }}
            QPushButton#RepeatCycle:hover {{ border-color: #b09050; background-color: #2a2218; }}
            QPushButton#RepeatCycle[repeatState="off"] {{
                background-color: #1a140e;
                border: {b(3)}px solid #4a3a2a;
                color: #5a4a3a;
            }}
            QPushButton#RepeatCycle[repeatState="one"] {{
                background-color: #3a2a10;
                border: {b(3)}px solid #c9a43a;
                color: #ffe8a0;
            }}
            QPushButton#RepeatCycle[repeatState="all"] {{
                background-color: #3a2a10;
                border: {b(3)}px solid #d8b85a;
                color: #fff8e0;
            }}
            QToolButton#PlaylistTile {{
                background-color: #2a2218;
                color: #d4c4a8;
                border: {b(2)}px solid #6a5a40;
                border-radius: {b(10)}px;
                padding: 0;
                min-width: 0;
                min-height: 0;
            }}
            QToolButton#PlaylistTile:hover:enabled {{
                background-color: #3a3024;
                border-color: #c9a43a;
                color: #fff8e8;
            }}
            QToolButton#PlaylistTile:pressed:enabled {{
                background-color: #1e1810;
            }}
            QToolButton#PlaylistTile:disabled {{
                color: #5a4a3a;
                background-color: #1e1a16;
                border-color: #3a3028;
            }}
            """
        )
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )

        central = QWidget()
        self.setCentralWidget(central)
        _ui_layout = load_ui_layout()
        self._ui_elements: dict[str, Any] = _ui_layout["elements"]
        self._overlay_layout: dict[str, Any] = _ui_layout["overlays"]
        # Element rects: 0–1 fracs of the central widget; see ui_layout_v2_document.
        self._ui_rect_map: dict[str, QWidget] = {}

        _meta_align = (
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        )
        _meta_w = QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred

        self.prev_btn = QPushButton("⏮", parent=central)
        self.next_btn = QPushButton("⏭", parent=central)
        self.seek_back_30 = QPushButton("−30s", parent=central)
        self.seek_fwd_30 = QPushButton("+30s", parent=central)
        for b in (self.prev_btn, self.next_btn, self.seek_back_30, self.seek_fwd_30):
            b.setObjectName("ArtTransportBtn")
        self.prev_btn.clicked.connect(self._on_prev)
        self.next_btn.clicked.connect(self._on_next)
        self.seek_back_30.clicked.connect(self._on_seek_back_30)
        self.seek_fwd_30.clicked.connect(self._on_seek_fwd_30)

        # Lucide volume-2 / volume-1 (see icons/ATTRIBUTION.txt) — up = louder, down = quieter.
        self.volume_up = QPushButton(parent=central)
        self.volume_up.setObjectName("VolumeStepBtn")
        self.volume_up.setToolTip("Increase volume")
        self.volume_down = QPushButton(parent=central)
        self.volume_down.setObjectName("VolumeStepBtn")
        self.volume_down.setToolTip("Decrease volume")
        self._init_volume_icons()
        self.volume_up.clicked.connect(self._on_volume_up)
        self.volume_down.clicked.connect(self._on_volume_down)

        self.album_art = AlbumArtLabel(500, 500)
        self.album_art.clicked.connect(self._on_playpause)
        self._art_frame = ArtworkFrameHost(central, self.album_art)
        self._art_frame.setObjectName("artFrame")

        self.title_label = QLabel("No track", parent=central)
        self.title_label.setWordWrap(True)
        self.title_label.setAlignment(_meta_align)
        self.title_label.setSizePolicy(_meta_w[0], _meta_w[1])
        tfont = QFont()
        tfont.setPointSize(_s(20))
        tfont.setBold(True)
        self.title_label.setFont(tfont)
        self.artist_label = QLabel("", parent=central)
        self.artist_label.setWordWrap(True)
        self.artist_label.setAlignment(_meta_align)
        self.artist_label.setSizePolicy(_meta_w[0], _meta_w[1])
        af = QFont()
        af.setPointSize(_s(15))
        self.artist_label.setFont(af)
        self.artist_label.setStyleSheet("color: #c4b59a;")
        self.album_label = QLabel("", parent=central)
        self.album_label.setWordWrap(True)
        self.album_label.setAlignment(_meta_align)
        self.album_label.setSizePolicy(_meta_w[0], _meta_w[1])
        bf = QFont()
        bf.setPointSize(_s(14))
        self.album_label.setFont(bf)
        self.album_label.setStyleSheet("color: #8a7a66;")
        self._sub_status_modal = SubStatusModal(self)
        self.sub_label = self._sub_status_modal.label
        sf = QFont()
        sf.setPointSize(_s(12))
        self.sub_label.setFont(sf)
        self.sub_label.setStyleSheet("color: #c8b8a0;")

        self.shuffle_btn = QPushButton(parent=central)
        self.shuffle_btn.setObjectName("IconTransport")
        self.shuffle_btn.setCheckable(True)
        self.shuffle_btn.setToolTip("Shuffle")
        self.shuffle_btn.toggled.connect(self._on_shuffle)
        self.repeat_btn = QPushButton(parent=central)
        self.repeat_btn.setObjectName("RepeatCycle")
        self.repeat_btn.setToolTip("Repeat: off — tap to cycle (one / all)")
        self.repeat_btn.clicked.connect(self._on_repeat_cycle)
        self._init_mode_icons()
        self._apply_repeat_ui()
        self._refresh_shuffle_icon()

        self._history_tile_icon_px = _btn(88)
        self._playlist_tile_icon = self._load_playlist_tile_icon()
        self._history_tiles: list[HistoryTile] = []
        ipx = int(self._history_tile_icon_px)
        for i in range(_MAX_ENTRIES):
            t = HistoryTile(
                central,
                self._playlist_tile_icon,
                ipx,
            )
            t.play_requested.connect(self._on_history_uri_play)
            self._history_tiles.append(t)

        _time_style = (
            "color: #d4c4a8; font-family: 'Courier New', Courier, monospace; "
            f"font-size: {_s(15)}px; font-weight: bold;"
        )
        self._progress_row = QWidget(parent=central)
        pl = QHBoxLayout(self._progress_row)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(_s(8))
        self.elapsed_label = QLabel("0:00")
        self.elapsed_label.setStyleSheet(_time_style)
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setStyleSheet(
            f"""
            QProgressBar {{
                border: 1px solid #5a4a38;
                border-radius: {_s(5)}px;
                background: #14100c;
                height: {_s(12)}px;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #8a6a24, stop:0.5 #c9a43a, stop:1 #6a5220);
                border-radius: {_s(3)}px;
            }}
            """
        )
        self.duration_label = QLabel("0:00")
        self.duration_label.setStyleSheet(_time_style)
        pl.addWidget(self.elapsed_label)
        pl.addWidget(self.progress_bar, 1)
        pl.addWidget(self.duration_label)

        self._ui_rect_map = {
            "artwork": self._art_frame,
            "prev": self.prev_btn,
            "seek_back_30": self.seek_back_30,
            "seek_fwd_30": self.seek_fwd_30,
            "next": self.next_btn,
            "volume_up": self.volume_up,
            "volume_down": self.volume_down,
            "shuffle": self.shuffle_btn,
            "repeat": self.repeat_btn,
            "title": self.title_label,
            "artist": self.artist_label,
            "album": self.album_label,
            "progress": self._progress_row,
        }
        for i, tile in enumerate(self._history_tiles):
            self._ui_rect_map[f"playlist_{i}"] = tile

        self._volume_overlay = VolumeOverlay(self)
        self._volume_overlay.hide()
        self._apply_ui_layout()

    def _load_playlist_tile_icon(self) -> QIcon:
        path = _ICONS_DIR / "list-music.svg"
        if not path.is_file():
            _log.warning("Missing icon: %s", path)
            return QIcon()
        return svg_colored_icon(
            path, "#c9a43a", int(getattr(self, "_history_tile_icon_px", _btn(88)))
        )

    @staticmethod
    def _layout_rect_from_fracs(
        r: dict[str, Any], W: int, H: int
    ) -> tuple[int, int, int, int]:
        """w/h fracs; null w xor h => square (side from non-null % of that axis). x/y null = center."""
        wf = r.get("w")
        hf = r.get("h")
        if wf is not None:
            wf = float(wf)
        if hf is not None:
            hf = float(hf)
        xf = r.get("x")
        yf = r.get("y")
        if xf is not None:
            xf = float(xf)
        if yf is not None:
            yf = float(yf)

        if wf is not None and hf is not None:
            ww = max(1, int(wf * W))
            hh = max(1, int(hf * H))
        elif wf is None and hf is not None:
            s = max(1, int(hf * H))
            s = min(s, W, H)
            ww = hh = s
        elif hf is None and wf is not None:
            s = max(1, int(wf * W))
            s = min(s, W, H)
            ww = hh = s
        else:
            ww = hh = 1

        wfr = ww / float(W)
        hfr = hh / float(H)
        if xf is None:
            x_px = int((W - ww) // 2)
        elif xf >= 0.0:
            x_px = int(xf * W)
        else:
            x_px = int(W * (1.0 - wfr - abs(xf)))
        if yf is None:
            y_px = int((H - hh) // 2)
        elif yf >= 0.0:
            y_px = int(yf * H)
        else:
            y_px = int(H * (1.0 - hfr - abs(yf)))
        x_px = max(0, x_px)
        y_px = max(0, y_px)
        ww = min(ww, max(1, W - x_px))
        hh = min(hh, max(1, H - y_px))
        return (x_px, y_px, ww, hh)

    def _set_sub_status_text(self, s: str) -> None:
        """Status / error line in the full-window modal overlay; empty hides the overlay."""
        self.sub_label.setText(s)
        self._sync_sub_status_modal()

    def _sync_sub_status_modal(self) -> None:
        if self._sub_status_modal is None:
            return
        if not (self.sub_label.text() or "").strip():
            self._sub_status_modal.hide()
            return
        self._sub_status_modal.show()
        self._place_overlay_widgets()

    def _apply_ui_layout(self) -> None:
        """Size/position from self._ui_elements (fractions of central widget); z = stack order."""
        cw = self.centralWidget()
        if cw is None or not self._ui_rect_map:
            return
        W, H = max(1, cw.width()), max(1, cw.height())
        stack: list[tuple[tuple[int, str], QWidget]] = []
        for name, w in self._ui_rect_map.items():
            r = self._ui_elements.get(name)
            if r is None:
                continue
            x_px, y_px, ww, hh = self._layout_rect_from_fracs(r, W, H)
            w.setGeometry(x_px, y_px, ww, hh)
            if name.startswith("playlist_") and isinstance(w, HistoryTile):
                w.refit(ww, hh)
            try:
                z = int(r.get("z", 0))
            except (TypeError, ValueError):
                z = 0
            # Sort by (z, name) so equal-z stacking is stable.
            stack.append(((z, name), w))
        stack.sort(key=lambda t: t[0])
        for _key, w in stack:
            if w is not None:
                w.raise_()
        self._place_overlay_widgets()

    def _place_overlay_widgets(self) -> None:
        """Map overlay rects (central-fracs) to window geometry; respect overlay ``z`` (higher = on top)."""
        cw = self.centralWidget()
        if cw is None or not self._overlay_layout:
            return
        W, H = max(1, cw.width()), max(1, cw.height())
        order: list[tuple[tuple[int, str], QWidget]] = []
        for key, wgt in (
            ("sub_status_modal", self._sub_status_modal),
            ("volume_hud", self._volume_overlay),
        ):
            if wgt is None:
                continue
            r = self._overlay_layout.get(key)
            if r is None:
                continue
            x_px, y_px, ww, hh = self._layout_rect_from_fracs(r, W, H)
            tl = cw.mapTo(self, QPoint(x_px, y_px))
            wgt.setGeometry(tl.x(), tl.y(), ww, hh)
            try:
                z = int(r.get("z", 0))
            except (TypeError, ValueError):
                z = 0
            order.append(((z, key), wgt))
        order.sort(key=lambda t: t[0])
        for _k, wgt in order:
            wgt.raise_()

    @pyqtSlot()
    def _apply_history_tiles(self) -> None:
        rows: list[Optional[HistoryItem]] = list(self._history.items)
        while len(rows) < _MAX_ENTRIES:
            rows.append(None)
        rows = rows[:_MAX_ENTRIES]
        for i, tile in enumerate(self._history_tiles):
            pl = rows[i] if i < len(rows) else None
            tile.set_history_item(pl, self._history, self._playlist_tile_icon)

    def _wire_shortcuts(self) -> None:
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=self._on_playpause)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, activated=self._on_prev)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, activated=self._on_next)
        QShortcut(QKeySequence(Qt.Key.Key_Up), self, activated=self._on_volume_up)
        QShortcut(QKeySequence(Qt.Key.Key_Down), self, activated=self._on_volume_down)
        for qk, fn in (
            (getattr(Qt.Key, "Key_VolumeUp", None), self._on_volume_up),
            (getattr(Qt.Key, "Key_VolumeDown", None), self._on_volume_down),
        ):
            if qk is not None:
                QShortcut(QKeySequence(qk), self, activated=fn)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        QTimer.singleShot(0, self._apply_ui_layout)

    def _layout_reflow(self) -> None:
        self._apply_ui_layout()

    @pyqtSlot()
    def _on_ws_connected(self) -> None:
        self._request_status_bg()

    def _connect_websocket(self) -> None:
        url = self._cfg.events_ws_url()
        _log.info("WebSocket open %s", url)
        self._ws.open(QUrl(url))

    @pyqtSlot()
    def _on_ws_disconnected(self) -> None:
        _log.info("WebSocket disconnected; schedule reconnect")
        if not self._ws_connect_timer.isActive():
            self._ws_connect_timer.start()

    @pyqtSlot(str)
    def _on_ws_text(self, message: str) -> None:
        try:
            ev = json.loads(message)
        except json.JSONDecodeError:
            return
        et = ev.get("type")
        data = ev.get("data")
        if isinstance(data, dict):
            cu = data.get("context_uri")
            if isinstance(cu, str) and cu.strip():
                newc = cu.strip()
                if newc != self._last_context_uri:
                    self._last_context_uri = newc
                    if self._last_tr_for_history is not None:
                        self._record_track_history(self._last_tr_for_history)
        if et in ("playback_ready", "active"):
            self._request_status_bg()
        elif et == "metadata" and isinstance(data, dict):
            self._apply_track(data)
        elif et == "playing":
            self._is_playing = True
            self._is_paused = False
            if not self._tick.isActive():
                self._tick.start()
            self._sync_pause_overlay()
        elif et == "paused":
            self._is_playing = False
            self._is_paused = True
            self._tick.stop()
            self._sync_pause_overlay()
        elif et in ("not_playing", "inactive"):
            self._is_playing = False
            self._is_paused = False
            self._tick.stop()
            self._sync_pause_overlay()
        elif et == "seek" and isinstance(data, dict):
            pos = int(data.get("position", 0))
            dur = int(data.get("duration", 0) or self._duration_ms)
            self._set_progress(pos, dur)
        elif et == "volume" and isinstance(data, dict):
            val = int(data.get("value", 0))
            mx = int(data.get("max", 0) or 0)
            if mx > 0:
                self._sync_volume_display(val, mx, force_hud=False)
        elif et == "stopped":
            self._clear_track()
        elif et in ("shuffle_context", "repeat_context", "repeat_track") and isinstance(data, dict):
            v = data.get("value")
            if et == "shuffle_context" and isinstance(v, bool):
                self._set_shuffle_checked(v)
            else:
                self._request_status_bg()
        if et in ("metadata", "seek", "playing", "paused", "not_playing", "will_play", "volume", "active", "inactive"):
            self._request_status_bg()

    def _set_shuffle_checked(self, on: bool) -> None:
        self.shuffle_btn.blockSignals(True)
        self.shuffle_btn.setChecked(on)
        self.shuffle_btn.blockSignals(False)
        self._refresh_shuffle_icon()

    def _on_tick(self) -> None:
        if not self._is_playing or self._duration_ms <= 0:
            return
        self._position_ms = min(self._duration_ms, self._position_ms + 500)
        self._set_progress(self._position_ms, self._duration_ms)

    def _request_status_bg(self) -> None:
        def work() -> None:
            try:
                root = get_json("/", self._cfg)
                st = get_json("/status", self._cfg)
            except GlsApiError as e:
                QTimer.singleShot(0, partial(self._on_status_failed, str(e)))
                return
            QTimer.singleShot(0, partial(self._on_status_ok, root, st))

        threading.Thread(target=work, daemon=True, name="gls-status").start()

    def _record_track_history(self, tr: dict[str, Any]) -> None:
        def schedule_refresh() -> None:
            QTimer.singleShot(0, self._apply_history_tiles)

        self._history.try_record(
            self._last_context_uri,
            tr,
            on_persisted=schedule_refresh,
            on_art_ready=schedule_refresh,
        )

    @pyqtSlot(str)
    def _on_status_failed(self, msg: str) -> None:
        self._set_sub_status_text("Cannot reach go-librespot: " + msg)

    @pyqtSlot(object, object)
    def _on_status_ok(self, root: object, st: object) -> None:
        ready = True
        if isinstance(root, dict) and "playback_ready" in root:
            ready = bool(root.get("playback_ready"))
        if not ready:
            self._set_sub_status_text("Daemon starting (playback not ready yet)…")
        else:
            self._set_sub_status_text("")

        if not isinstance(st, dict):
            return

        self._set_shuffle_checked(bool(st.get("shuffle_context")))
        self._sync_repeat_mode(
            bool(st.get("repeat_track")),
            bool(st.get("repeat_context")),
        )

        vol = st.get("volume")
        steps = st.get("volume_steps")
        if isinstance(vol, (int, float)) and isinstance(steps, (int, float)) and int(steps) > 0:
            self._sync_volume_display(int(vol), int(steps), force_hud=False)

        paused = bool(st.get("paused"))
        stop = bool(st.get("stopped"))
        buf = bool(st.get("buffering"))
        if buf:
            self._set_sub_status_text("Buffering…")
        can_tick = not paused and not stop and not buf
        self._is_playing = can_tick
        self._is_paused = bool(paused) and not bool(stop) and not bool(buf)
        if can_tick and not self._tick.isActive():
            self._tick.start()
        if not can_tick:
            self._tick.stop()

        tr = st.get("track")
        if isinstance(tr, dict):
            self._apply_track(tr)
        else:
            self._clear_track()

    def _apply_track(self, tr: dict[str, Any]) -> None:
        name = tr.get("name") or "—"
        artists = tr.get("artist_names") or []
        if isinstance(artists, list):
            artist_s = ", ".join(str(x) for x in artists)
        else:
            artist_s = ""
        album = tr.get("album_name") or ""
        art = tr.get("album_cover_url")
        if art is not None and not isinstance(art, str):
            art = str(art) if art else None
        self.title_label.setText(name)
        self.artist_label.setText(artist_s)
        self.album_label.setText(album)
        self.album_art.set_art_url(art)
        self._duration_ms = int(tr.get("duration", 0) or 0)
        self._position_ms = int(tr.get("position", 0) or 0)
        self._set_progress(self._position_ms, self._duration_ms)
        QTimer.singleShot(0, self._apply_ui_layout)
        self._sync_pause_overlay()
        self._last_tr_for_history = dict(tr)
        self._record_track_history(tr)

    def _clear_track(self) -> None:
        self._last_tr_for_history = None
        self._is_paused = False
        self.album_art.set_art_url(None)
        self.title_label.setText("No track")
        self.artist_label.setText("")
        self.album_label.setText("")
        self._duration_ms = 0
        self._position_ms = 0
        self.progress_bar.setValue(0)
        self.elapsed_label.setText("0:00")
        self.duration_label.setText("0:00")
        QTimer.singleShot(0, self._apply_ui_layout)
        self._sync_pause_overlay()

    def _sync_pause_overlay(self) -> None:
        has_track = self.title_label.text() not in ("", "No track")
        self.album_art.set_pause_overlay_visible(self._is_paused and has_track)

    def _set_progress(self, pos_ms: int, dur_ms: int) -> None:
        self._position_ms = max(0, pos_ms)
        self._duration_ms = max(0, dur_ms)
        if self._duration_ms > 0:
            pct = min(100, int(self._position_ms * 100 / self._duration_ms))
            self.progress_bar.setValue(pct)
            self.elapsed_label.setText(self._fmt_ms(self._position_ms))
            self.duration_label.setText(self._fmt_ms(self._duration_ms))
        else:
            self.progress_bar.setValue(0)
            self.elapsed_label.setText("0:00")
            self.duration_label.setText("0:00")

    @staticmethod
    def _fmt_ms(ms: int) -> str:
        s = max(0, int(ms) // 1000)
        m, sec = divmod(s, 60)
        return f"{m}:{sec:02d}"

    def _post_bg(self, path: str, body: Optional[dict[str, Any]] = None) -> None:
        def run() -> None:
            try:
                _fg_post(path, body, self._cfg)
            except GlsApiError as e:
                _log.warning("%s", e)

        threading.Thread(target=run, daemon=True, name="gls-post").start()

    @pyqtSlot(str)
    def _on_history_uri_play(self, uri: str) -> None:
        u = (uri or "").strip()
        if not u.startswith("spotify:"):
            return
        _log.info("play saved URI %s", u)
        self._post_bg("/player/play", {"uri": u, "paused": False})

    def _on_playpause(self) -> None:
        self._post_bg("/player/playpause", {})

    def _on_next(self) -> None:
        self._post_bg("/player/next", {})

    def _on_prev(self) -> None:
        self._post_bg("/player/prev", {})

    def _on_seek_back_30(self) -> None:
        self._post_bg("/player/seek", {"position": -30_000, "relative": True})

    def _on_seek_fwd_30(self) -> None:
        self._post_bg("/player/seek", {"position": 30_000, "relative": True})

    def _vol_step(self) -> int:
        return max(1, self._vol_max // 16)

    def _sync_volume_display(self, val: int, max_v: int, *, force_hud: bool) -> None:
        self._vol_max = max(1, int(max_v))
        val = int(max(0, min(self._vol_max, int(val))))
        self._vol_value = val
        self.volume_down.setEnabled(val > 0)
        self.volume_up.setEnabled(val < self._vol_max)
        should_flash = force_hud or (
            self._last_hud_val is not None and val != self._last_hud_val
        )
        self._last_hud_val = val
        if should_flash:
            self._flash_volume_hud(val, self._vol_max)

    def _flash_volume_hud(self, val: int, max_v: int) -> None:
        if self._volume_overlay is None:
            return
        self._hud_hide_timer.stop()
        if self._hud_fade is not None and self._hud_fade.state() == QAbstractAnimation.State.Running:
            self._hud_fade.stop()
        eff = self._volume_overlay.graphicsEffect()
        if isinstance(eff, QGraphicsOpacityEffect):
            eff.setOpacity(1.0)
        self._volume_overlay.set_level(val, max_v)
        self._volume_overlay.show()
        self._place_overlay_widgets()
        self._volume_overlay.refit_to_bounds()
        self._hud_hide_timer.start()

    def _begin_hud_fade(self) -> None:
        if self._volume_overlay is None or not self._volume_overlay.isVisible():
            return
        eff = self._volume_overlay.graphicsEffect()
        if not isinstance(eff, QGraphicsOpacityEffect):
            self._volume_overlay.hide()
            return
        if self._hud_fade is not None and self._hud_fade.state() == QAbstractAnimation.State.Running:
            return
        self._hud_fade = QPropertyAnimation(eff, b"opacity", self)
        self._hud_fade.setDuration(300)
        self._hud_fade.setStartValue(1.0)
        self._hud_fade.setEndValue(0.0)
        self._hud_fade.finished.connect(self._hud_fade_finished)
        self._hud_fade.start()

    def _hud_fade_finished(self) -> None:
        if self._hud_fade is not None:
            try:
                self._hud_fade.finished.disconnect(self._hud_fade_finished)
            except (TypeError, RuntimeError):
                pass
        self._hud_fade = None
        if self._volume_overlay is not None:
            self._volume_overlay.hide()
            e = self._volume_overlay.graphicsEffect()
            if isinstance(e, QGraphicsOpacityEffect):
                e.setOpacity(1.0)

    def _on_volume_up(self) -> None:
        step = self._vol_step()
        nv = min(self._vol_max, self._vol_value + step)
        self._post_bg("/player/volume", {"volume": nv})
        self._sync_volume_display(nv, self._vol_max, force_hud=True)

    def _on_volume_down(self) -> None:
        step = self._vol_step()
        nv = max(0, self._vol_value - step)
        self._post_bg("/player/volume", {"volume": nv})
        self._sync_volume_display(nv, self._vol_max, force_hud=True)

    def _init_volume_icons(self) -> None:
        """Lucide ``volume-2`` / ``volume-1`` SVGs (https://lucide.dev) — see icons/ATTRIBUTION.txt."""
        px = _btn(40)
        self._volume_icon_px = px
        sz = QSize(px, px)
        self.volume_up.setIconSize(sz)
        self.volume_down.setIconSize(sz)
        u = _ICONS_DIR / "volume-up.svg"
        d = _ICONS_DIR / "volume-down.svg"
        if u.is_file():
            self.volume_up.setIcon(
                svg_colored_icon(u, "#e8dcc4", px)
            )
        else:
            _log.warning("Missing icon: %s", u)
        if d.is_file():
            self.volume_down.setIcon(
                svg_colored_icon(d, "#e8dcc4", px)
            )
        else:
            _log.warning("Missing icon: %s", d)

    def _init_mode_icons(self) -> None:
        """Lucide SVGs (see icons/ATTRIBUTION.txt); stroke colors match retro brass/cream."""
        px = _btn(34)
        self._mode_icon_px = px
        sz = QSize(px, px)
        self.shuffle_btn.setIconSize(sz)
        self.repeat_btn.setIconSize(sz)

        def load(name: str, color: str) -> QIcon:
            path = _ICONS_DIR / name
            if not path.is_file():
                _log.warning("Missing icon file: %s", path)
                return QIcon()
            return svg_colored_icon(path, color, px)

        self._shuffle_icon_off = load("shuffle.svg", "#b89868")
        self._shuffle_icon_on = load("shuffle.svg", "#ffe8a8")
        self._repeat_icon_off = load("repeat.svg", "#6a5848")
        self._repeat_icon_one = load("repeat-1.svg", "#f5edd0")
        self._repeat_icon_all = load("repeat.svg", "#f8f0e0")

    def _refresh_shuffle_icon(self) -> None:
        self.shuffle_btn.setIcon(
            self._shuffle_icon_on
            if self.shuffle_btn.isChecked()
            else self._shuffle_icon_off
        )
        self.shuffle_btn.setText("")

    def _on_shuffle(self, on: bool) -> None:
        self._refresh_shuffle_icon()
        self._post_bg("/player/shuffle_context", {"shuffle_context": on})

    def _apply_repeat_ui(self) -> None:
        m = self._repeat_mode
        if m == 0:
            self.repeat_btn.setIcon(self._repeat_icon_off)
            self.repeat_btn.setProperty("repeatState", "off")
        elif m == 1:
            self.repeat_btn.setIcon(self._repeat_icon_one)
            self.repeat_btn.setProperty("repeatState", "one")
        else:
            self.repeat_btn.setIcon(self._repeat_icon_all)
            self.repeat_btn.setProperty("repeatState", "all")
        self.repeat_btn.setText("")
        tips = ("Repeat: off", "Repeat one", "Repeat all")
        self.repeat_btn.setToolTip(tips[m])
        self._polish_repeat_btn()

    def _polish_repeat_btn(self) -> None:
        st = self.repeat_btn.style()
        st.unpolish(self.repeat_btn)
        st.polish(self.repeat_btn)

    def _sync_repeat_mode(self, repeat_track: bool, repeat_context: bool) -> None:
        if repeat_track:
            m = 1
        elif repeat_context:
            m = 2
        else:
            m = 0
        if m == self._repeat_mode:
            return
        self._repeat_mode = m
        self._apply_repeat_ui()

    def _on_repeat_cycle(self) -> None:
        self._repeat_mode = (self._repeat_mode + 1) % 3
        self._post_repeat_state(self._repeat_mode)
        self._apply_repeat_ui()

    def _post_repeat_state(self, mode: int) -> None:
        def run() -> None:
            try:
                if mode == 0:
                    post_json(
                        "/player/repeat_track",
                        {"repeat_track": False},
                        cfg=self._cfg,
                    )
                    post_json(
                        "/player/repeat_context",
                        {"repeat_context": False},
                        cfg=self._cfg,
                    )
                elif mode == 1:
                    post_json(
                        "/player/repeat_context",
                        {"repeat_context": False},
                        cfg=self._cfg,
                    )
                    post_json(
                        "/player/repeat_track",
                        {"repeat_track": True},
                        cfg=self._cfg,
                    )
                else:
                    post_json(
                        "/player/repeat_track",
                        {"repeat_track": False},
                        cfg=self._cfg,
                    )
                    post_json(
                        "/player/repeat_context",
                        {"repeat_context": True},
                        cfg=self._cfg,
                    )
            except GlsApiError as e:
                _log.warning("%s", e)

        threading.Thread(target=run, daemon=True, name="gls-repeat").start()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._status_timer.stop()
        self._tick.stop()
        self._hud_hide_timer.stop()
        if self._hud_fade is not None and self._hud_fade.state() == QAbstractAnimation.State.Running:
            self._hud_fade.stop()
        self._ws.close()
        super().closeEvent(event)


def _configure_logging() -> None:
    level_name = (os.environ.get("GLS_LOG_LEVEL") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    log_file = (os.environ.get("GLS_LOG_FILE") or "").strip()
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    datefmt = "%H:%M:%S"
    if sys.version_info >= (3, 8):
        logging.basicConfig(
            level=level,
            format=fmt,
            datefmt=datefmt,
            stream=sys.stderr,
            force=True,
        )
    else:
        logging.basicConfig(
            level=level, format=fmt, datefmt=datefmt, stream=sys.stderr
        )
    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter(fmt, datefmt))
        logging.getLogger().addHandler(fh)
    for name in ("gls-frontend", "gls-client"):
        logging.getLogger(name).setLevel(level)


def main() -> None:
    _configure_logging()
    _log.info(
        "logging: level=%s (set GLS_LOG_LEVEL=DEBUG, optional GLS_LOG_FILE=…)",
        (os.environ.get("GLS_LOG_LEVEL") or "INFO").upper(),
    )
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    w = MainWindow()
    w.showFullScreen()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
