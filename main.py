#!/usr/bin/env python3
"""
PyQt6 touchscreen UI for a local go-librespot daemon: REST + WebSocket (/events).

Expects the API on http://127.0.0.1:3678 by default. Override with GOLIBRESPOT_BASE, e.g.:
  GOLIBRESPOT_BASE=http://127.0.0.1:3678
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from functools import partial
from typing import Any, Optional

from PyQt6.QtCore import (
    QAbstractAnimation,
    Qt,
    QPropertyAnimation,
    QTimer,
    QUrl,
    pyqtSlot,
)
from PyQt6.QtGui import QCloseEvent, QFont, QKeySequence, QPixmap, QResizeEvent, QShortcut
from PyQt6.QtNetwork import QAbstractSocket, QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWebSockets import QWebSocket
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gls_client import GlsApiError, GlsConfig, get_json, post_json

_log = logging.getLogger("gls-frontend")

# go-librespot uses {"type": "...", "data": ...} (cmd/daemon/api_server.go ApiEvent).


class AlbumArtLabel(QLabel):
    def __init__(self, size: int = 280) -> None:
        super().__init__()
        self._art_size = size
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background-color: #111111; border: none; border-radius: 12px; color: #666666;")
        self.setText("—")
        self._nam = QNetworkAccessManager(self)
        self._active_reply: Optional[QNetworkReply] = None

    def set_art_url(self, url: Optional[str]) -> None:
        # Clear ref before abort(): abort() may synchronously emit finished and clear
        # _active_reply in _on_art_finished, which would make the next deleteLater crash.
        old = self._active_reply
        self._active_reply = None
        if old is not None:
            old.abort()  # finished disposes the reply; do not deleteLater here
        if not url:
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
            self.clear()
            self.setText("—")
            return
        data = reply.readAll()
        reply.deleteLater()
        self._active_reply = None
        pix = QPixmap()
        if not pix.loadFromData(bytes(data)):
            self.clear()
            self.setText("—")
            return
        scaled = pix.scaled(
            self._art_size,
            self._art_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(scaled)


class VolumeOverlay(QFrame):
    """Fullscreen dim + centered macOS-style volume HUD (large level + bar)."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setObjectName("volumeOverlay")
        self.setStyleSheet(
            """
            #volumeOverlay { background-color: rgba(0, 0, 0, 0.55); }
            QFrame#volumeHudCard {
                background-color: rgba(40, 40, 40, 245);
                border-radius: 28px;
                border: none;
            }
            QLabel#hudPercent { color: #FFFFFF; font-weight: 600; }
            QProgressBar {
                border: none; border-radius: 4px; background: rgba(255,255,255,0.15);
                height: 14px; text-align: center;
            }
            QProgressBar::chunk { background-color: #FFFFFF; border-radius: 3px; }
            """
        )
        self._icon = QLabel("🔊")
        ic = QFont()
        ic.setPointSize(56)
        self._icon.setFont(ic)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._bar = QProgressBar()
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(14)
        self._bar.setMinimumWidth(420)
        self._bar.setMaximumWidth(520)
        self._pct = QLabel("0")
        self._pct.setObjectName("hudPercent")
        pf = QFont()
        pf.setPointSize(44)
        pf.setBold(True)
        self._pct.setFont(pf)
        self._pct.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sub = QLabel("")
        self._sub.setStyleSheet("color: rgba(255,255,255,0.55); font-size: 14px;")
        self._sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card = QFrame()
        card.setObjectName("volumeHudCard")
        inner = QVBoxLayout(card)
        inner.setContentsMargins(40, 36, 40, 36)
        inner.setSpacing(20)
        inner.addWidget(self._icon, alignment=Qt.AlignmentFlag.AlignCenter)
        inner.addWidget(self._pct, alignment=Qt.AlignmentFlag.AlignCenter)
        inner.addWidget(self._bar, alignment=Qt.AlignmentFlag.AlignCenter)
        inner.addWidget(self._sub, alignment=Qt.AlignmentFlag.AlignCenter)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(card, alignment=Qt.AlignmentFlag.AlignCenter)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)

        self._fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fx)
        self._fx.setOpacity(1.0)
        self.hide()
        for w in (self, *self.findChildren(QWidget)):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_level(self, value: int, max_v: int) -> None:
        max_v = max(1, int(max_v))
        value = int(max(0, min(max_v, value)))
        pct = int(round(100.0 * value / max_v)) if max_v else 0
        self._bar.setRange(0, max_v)
        self._bar.setValue(value)
        self._pct.setText(f"{pct}%")
        self._sub.setText(f"{value} / {max_v}")


def _fg_post(path: str, body: Optional[dict[str, Any]], cfg: GlsConfig) -> None:
    post_json(path, body, cfg=cfg)


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
        self._is_playing = False
        self._duration_ms = 0
        self._position_ms = 0
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(2000)
        self._status_timer.timeout.connect(self._request_status_bg)
        self._tick = QTimer(self)
        self._tick.setInterval(500)
        self._tick.timeout.connect(self._on_tick)

        self._build_ui()
        self._wire_shortcuts()

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
        self.conn_label.setText("WebSocket error — retrying…")

    def _build_ui(self) -> None:
        self.setWindowTitle("go-librespot")
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background-color: #000000; color: #FFFFFF; border: none; }
            QLabel { background-color: #000000; color: #FFFFFF; border: none; }
            QPushButton {
                background-color: #1a1a1a; color: #FFFFFF;
                border: none; border-radius: 14px;
                font-size: 22px; font-weight: bold; padding: 12px 20px; min-height: 44px; min-width: 44px;
            }
            QPushButton:hover { background-color: #2a2a2a; }
            QPushButton:disabled { color: #666666; background-color: #111111; }
            QCheckBox { font-size: 16px; spacing: 10px; }
            QCheckBox::indicator { width: 28px; height: 28px; }
            """
        )
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(18)

        title = QLabel("Spotify (go-librespot)")
        tf = QFont()
        tf.setPointSize(22)
        tf.setBold(True)
        title.setFont(tf)
        root.addWidget(title)

        self.conn_label = QLabel("")
        self.conn_label.setStyleSheet("color: #888888;")
        root.addWidget(self.conn_label)

        track_row = QHBoxLayout()
        track_row.setSpacing(20)
        self.album_art = AlbumArtLabel(300)
        track_row.addWidget(self.album_art, 0, Qt.AlignmentFlag.AlignTop)

        info = QVBoxLayout()
        self.title_label = QLabel("No track")
        self.title_label.setWordWrap(True)
        tfont = QFont()
        tfont.setPointSize(20)
        tfont.setBold(True)
        self.title_label.setFont(tfont)
        info.addWidget(self.title_label)
        self.artist_label = QLabel("")
        self.artist_label.setWordWrap(True)
        af = QFont()
        af.setPointSize(15)
        self.artist_label.setFont(af)
        self.artist_label.setStyleSheet("color: #cccccc;")
        info.addWidget(self.artist_label)
        self.album_label = QLabel("")
        self.album_label.setWordWrap(True)
        self.album_label.setStyleSheet("color: #888888;")
        info.addWidget(self.album_label)
        self.sub_label = QLabel("")
        self.sub_label.setStyleSheet("color: #888888;")
        info.addWidget(self.sub_label)
        info.addStretch()
        track_row.addLayout(info, 1)
        root.addLayout(track_row)

        vol = QHBoxLayout()
        vol.setSpacing(16)
        vol.addWidget(QLabel("Volume"), 0, Qt.AlignmentFlag.AlignVCenter)
        self.volume_down = QPushButton("−")
        self.volume_up = QPushButton("+")
        for b in (self.volume_down, self.volume_up):
            b.setFixedSize(72, 72)
        self.volume_down.clicked.connect(self._on_volume_down)
        self.volume_up.clicked.connect(self._on_volume_up)
        vol.addWidget(self.volume_down)
        vol.addWidget(self.volume_up)
        self.vol_meta = QLabel("")
        self.vol_meta.setStyleSheet("color: #666666;")
        vol.addWidget(self.vol_meta, 0, Qt.AlignmentFlag.AlignVCenter)
        vol.addStretch(1)
        root.addLayout(vol)

        self._volume_overlay = VolumeOverlay(self)
        self._volume_overlay.hide()

        prog = QHBoxLayout()
        self.elapsed_label = QLabel("0:00")
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setStyleSheet(
            """
            QProgressBar { border: none; border-radius: 4px; background: #1a1a1a; height: 12px; }
            QProgressBar::chunk { background-color: #1db954; border-radius: 2px; }
            """
        )
        self.duration_label = QLabel("0:00")
        prog.addWidget(self.elapsed_label)
        prog.addWidget(self.progress_bar, 1)
        prog.addWidget(self.duration_label)
        root.addLayout(prog)

        toggles = QHBoxLayout()
        self.shuffle_check = QCheckBox("Shuffle")
        self.repeat_track_check = QCheckBox("Repeat track")
        self.repeat_context_check = QCheckBox("Repeat context")
        self.shuffle_check.toggled.connect(self._on_shuffle)
        self.repeat_track_check.toggled.connect(self._on_repeat_track)
        self.repeat_context_check.toggled.connect(self._on_repeat_context)
        toggles.addWidget(self.shuffle_check)
        toggles.addWidget(self.repeat_track_check)
        toggles.addWidget(self.repeat_context_check)
        toggles.addStretch()
        root.addLayout(toggles)

        ctrl = QHBoxLayout()
        self.prev_btn = QPushButton("⏮")
        self.prev_btn.setFixedSize(88, 88)
        self.prev_btn.clicked.connect(self._on_prev)
        self.play_btn = QPushButton("▶")
        self.play_btn.setFixedSize(96, 96)
        self.play_btn.clicked.connect(self._on_playpause)
        self.next_btn = QPushButton("⏭")
        self.next_btn.setFixedSize(88, 88)
        self.next_btn.clicked.connect(self._on_next)
        for b in (self.prev_btn, self.play_btn, self.next_btn):
            ctrl.addWidget(b)
        root.addLayout(ctrl)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.NoFrame)
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: #333333;")
        root.addWidget(sep)

        hint = QLabel("API: " + self._cfg.base)
        hint.setStyleSheet("color: #555555;")
        root.addWidget(hint)

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
        if self._volume_overlay is not None:
            self._volume_overlay.setGeometry(0, 0, self.width(), self.height())

    @pyqtSlot()
    def _on_ws_connected(self) -> None:
        self.conn_label.setText("WebSocket connected to " + self._cfg.events_ws_url())
        self._request_status_bg()

    def _connect_websocket(self) -> None:
        url = self._cfg.events_ws_url()
        _log.info("WebSocket open %s", url)
        self.conn_label.setText("Connecting to " + url + "…")
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
        if et in ("playback_ready", "active"):
            self._request_status_bg()
        elif et == "metadata" and isinstance(data, dict):
            self._apply_track(data)
        elif et == "playing":
            self._is_playing = True
            self.play_btn.setText("⏸")
            if not self._tick.isActive():
                self._tick.start()
        elif et in ("paused", "not_playing", "inactive"):
            self._is_playing = False
            self._tick.stop()
            self.play_btn.setText("▶")
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
                self._block_toggle(self.shuffle_check, v)
            elif et == "repeat_context" and isinstance(v, bool):
                self._block_toggle(self.repeat_context_check, v)
            elif et == "repeat_track" and isinstance(v, bool):
                self._block_toggle(self.repeat_track_check, v)
        if et in ("metadata", "seek", "playing", "paused", "not_playing", "will_play", "volume", "active", "inactive"):
            self._request_status_bg()

    def _block_toggle(self, box: QCheckBox, on: bool) -> None:
        box.blockSignals(True)
        box.setChecked(on)
        box.blockSignals(False)

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

    @pyqtSlot(str)
    def _on_status_failed(self, msg: str) -> None:
        self.sub_label.setText("Cannot reach go-librespot: " + msg)
        self.conn_label.setText("REST unreachable — is the daemon on " + self._cfg.base + "?")

    @pyqtSlot(object, object)
    def _on_status_ok(self, root: object, st: object) -> None:
        ready = True
        if isinstance(root, dict) and "playback_ready" in root:
            ready = bool(root.get("playback_ready"))
        if not ready:
            self.sub_label.setText("Daemon starting (playback not ready yet)…")
        else:
            self.sub_label.setText("")

        ws_ok = self._ws.state() == QAbstractSocket.SocketState.ConnectedState
        self.conn_label.setText(
            "Connected · WebSocket " + ("open" if ws_ok else "reconnecting…")
        )

        if not isinstance(st, dict):
            return

        name = st.get("device_name") or st.get("device_id") or "device"
        uname = st.get("username") or ""
        self._block_toggle(
            self.shuffle_check,
            bool(st.get("shuffle_context")),
        )
        self._block_toggle(
            self.repeat_context_check,
            bool(st.get("repeat_context")),
        )
        self._block_toggle(
            self.repeat_track_check,
            bool(st.get("repeat_track")),
        )
        st_line = name + (f" · {uname}" if uname else "")

        vol = st.get("volume")
        steps = st.get("volume_steps")
        if isinstance(vol, (int, float)) and isinstance(steps, (int, float)) and int(steps) > 0:
            self._sync_volume_display(int(vol), int(steps), force_hud=False)

        paused = bool(st.get("paused"))
        stop = bool(st.get("stopped"))
        buf = bool(st.get("buffering"))
        if buf:
            self.sub_label.setText("Buffering…")
        can_tick = not paused and not stop and not buf
        self._is_playing = can_tick
        self.play_btn.setText("⏸" if can_tick else "▶")
        if can_tick and not self._tick.isActive():
            self._tick.start()
        if not can_tick:
            self._tick.stop()

        tr = st.get("track")
        if isinstance(tr, dict):
            self._apply_track(tr, hint=st_line)
        else:
            self._clear_track(hint=st_line)

    def _apply_track(self, tr: dict[str, Any], *, hint: str = "") -> None:
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
        if hint:
            self.conn_label.setText(f"{hint} · track")

    def _clear_track(self, *, hint: str = "") -> None:
        self.album_art.set_art_url(None)
        self.title_label.setText("No track")
        self.artist_label.setText("")
        self.album_label.setText("")
        self._duration_ms = 0
        self._position_ms = 0
        self.progress_bar.setValue(0)
        self.elapsed_label.setText("0:00")
        self.duration_label.setText("0:00")
        if hint:
            self.conn_label.setText(hint)

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

    def _on_playpause(self) -> None:
        self._post_bg("/player/playpause", {})

    def _on_next(self) -> None:
        self._post_bg("/player/next", {})

    def _on_prev(self) -> None:
        self._post_bg("/player/prev", {})

    def _vol_step(self) -> int:
        return max(1, self._vol_max // 16)

    def _sync_volume_display(self, val: int, max_v: int, *, force_hud: bool) -> None:
        self._vol_max = max(1, int(max_v))
        val = int(max(0, min(self._vol_max, int(val))))
        self._vol_value = val
        self.volume_down.setEnabled(val > 0)
        self.volume_up.setEnabled(val < self._vol_max)
        self.vol_meta.setText(f"{val} / {self._vol_max}")
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
        self._volume_overlay.setGeometry(0, 0, self.width(), self.height())
        self._volume_overlay.show()
        self._volume_overlay.raise_()
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

    def _on_shuffle(self, on: bool) -> None:
        self._post_bg("/player/shuffle_context", {"shuffle_context": on})

    def _on_repeat_track(self, on: bool) -> None:
        self._post_bg("/player/repeat_track", {"repeat_track": on})

    def _on_repeat_context(self, on: bool) -> None:
        self._post_bg("/player/repeat_context", {"repeat_context": on})

    def closeEvent(self, event: QCloseEvent) -> None:
        self._status_timer.stop()
        self._tick.stop()
        self._hud_hide_timer.stop()
        if self._hud_fade is not None and self._hud_fade.state() == QAbstractAnimation.State.Running:
            self._hud_fade.stop()
        self._ws.close()
        super().closeEvent(event)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = QApplication(sys.argv)
    w = MainWindow()
    w.showFullScreen()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
