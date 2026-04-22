"""
Persist last N distinct **playlist (context)** URIs: track metadata as snapshot + art on disk.

``context_uri`` from go-librespot WebSocket events is the playback context (playlist, album, …);
we dedupe by that URI and only add when it is not already in the last six entries. Uses
``/status`` + WebSocket only (no Spotify Web API).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_log = logging.getLogger("gls-frontend.history")

_MAX_ENTRIES = 6

# Data dir: override with JUKEBOX_GLS_DATA_DIR
def default_data_dir() -> Path:
    o = (os.environ.get("JUKEBOX_GLS_DATA_DIR") or "").strip()
    if o:
        return Path(o).expanduser()
    if (os.environ.get("XDG_CONFIG_HOME") or "").strip():
        return Path(os.environ["XDG_CONFIG_HOME"].strip()) / "jukebox-frontend-go-librespot"
    return Path.home() / ".config" / "jukebox-frontend-go-librespot"


@dataclass
class HistoryItem:
    """One row: a distinct playlist/context URI (newest first). Snapshot of one track for art/label."""

    entry_id: str
    context_uri: str
    track_uri: str
    name: str
    artist_names: list[str]
    album_name: str
    album_cover_url: Optional[str]
    cover_path: Optional[str]  # relative to data dir, e.g. covers/abc.jpg
    recorded_at: float

    def play_uri(self) -> str:
        """Use playlist/context URI for POST /player/play."""
        c = (self.context_uri or "").strip()
        if c.startswith("spotify:"):
            return c
        t = (self.track_uri or "").strip()
        return t if t.startswith("spotify:") else c


def _safe_cover_key(uri: str) -> str:
    return hashlib.sha256(uri.encode("utf-8", errors="replace")).hexdigest()[:32]


def _guess_ext(content_type: str) -> str:
    ct = (content_type or "").lower()
    if "png" in ct:
        return ".png"
    if "webp" in ct:
        return ".webp"
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    return ".img"


def _as_str_list(x: Any) -> list[str]:
    if not isinstance(x, list):
        return []
    return [str(v) for v in x if v is not None and str(v).strip()]


class PlaybackHistory:
    """
    Append-only style recent list (max 6), JSON index + cover files under data dir.
    """

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self._dir = (data_dir or default_data_dir()).resolve()
        self._covers = self._dir / "covers"
        self._index_path = self._dir / "recent_tracks.json"
        self._items: list[HistoryItem] = []
        self._lock = threading.Lock()
        self._load()

    @property
    def items(self) -> list[HistoryItem]:
        with self._lock:
            return list(self._items)

    def data_dir(self) -> Path:
        return self._dir

    def resolve_cover(self, item: HistoryItem) -> Optional[Path]:
        rel = (item.cover_path or "").strip()
        if not rel:
            return None
        p = (self._dir / rel).resolve()
        try:
            p.relative_to(self._dir)
        except ValueError:
            return None
        return p if p.is_file() else None

    def _load(self) -> None:
        if not self._index_path.is_file():
            return
        try:
            raw = self._index_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as e:
            _log.warning("Failed to load %s: %s", self._index_path, e)
            return
        if not isinstance(data, list):
            return
        out: list[HistoryItem] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            try:
                eid = str(row.get("entry_id") or "")
                if not eid:
                    eid = str(uuid.uuid4())
                out.append(
                    HistoryItem(
                        entry_id=eid,
                        context_uri=str(row.get("context_uri") or ""),
                        track_uri=str(row.get("track_uri") or ""),
                        name=str(row.get("name") or "—"),
                        artist_names=_as_str_list(row.get("artist_names")),
                        album_name=str(row.get("album_name") or ""),
                        album_cover_url=(str(row["album_cover_url"])
                                         if row.get("album_cover_url") else None),
                        cover_path=(str(row["cover_path"])
                                    if row.get("cover_path") else None),
                        recorded_at=float(row.get("recorded_at") or 0.0),
                    )
                )
            except (TypeError, ValueError) as e:
                _log.debug("Skip bad history row: %s", e)
        with self._lock:
            self._items = out[:_MAX_ENTRIES]

    def _save_locked(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        payload: list[dict[str, Any]] = []
        for it in self._items:
            d = asdict(it)
            payload.append(d)
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        tmp = self._index_path.with_suffix(self._index_path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self._index_path)

    def try_record(
        self,
        context_uri: str,
        tr: dict[str, Any],
        *,
        on_persisted: Optional[Callable[[], None]] = None,
        on_art_ready: Optional[Callable[[], None]] = None,
    ) -> Optional[HistoryItem]:
        """
        Record a new row only if ``context_uri`` (playlist / playback context) is a Spotify URI
        and is **not** already among the last six. Track fields are a display snapshot; cover
        file is keyed by context URI. Returns a new :class:`HistoryItem` or None.
        """
        pl = (context_uri or "").strip()
        if not pl.startswith("spotify:"):
            return None
        uri = (tr.get("uri") or "").strip()
        if not uri:
            return None
        with self._lock:
            existing = {it.context_uri for it in self._items if it.context_uri}
            if pl in existing:
                return None
            name = str(tr.get("name") or "—")
            artists = tr.get("artist_names")
            if not isinstance(artists, list):
                artists = []
            artist_s = [str(x) for x in artists if x is not None and str(x).strip()]
            album = str(tr.get("album_name") or "")
            art_url = tr.get("album_cover_url")
            art_s = str(art_url).strip() if art_url is not None else None
            if not art_s:
                art_s = None
            item = HistoryItem(
                entry_id=str(uuid.uuid4()),
                context_uri=pl,
                track_uri=uri,
                name=name,
                artist_names=artist_s,
                album_name=album,
                album_cover_url=art_s,
                cover_path=None,
                recorded_at=time.time(),
            )
            self._items.insert(0, item)
            self._items = self._items[:_MAX_ENTRIES]
            try:
                self._save_locked()
            except OSError as e:
                _log.warning("Failed saving history index: %s", e)
            eid = item.entry_id
            cover_url = art_s
            cover_key_uri = pl

        if on_persisted is not None:
            on_persisted()
        if cover_url:
            threading.Thread(
                target=self._download_cover_bg,
                args=(eid, cover_key_uri, cover_url, on_art_ready),
                daemon=True,
                name="gls-history-art",
            ).start()
        return item

    def _download_cover_bg(
        self,
        entry_id: str,
        playlist_or_context_uri: str,
        url: str,
        on_art_ready: Optional[Callable[[], None]],
    ) -> None:
        key = _safe_cover_key(playlist_or_context_uri)
        rel: Optional[str] = None
        try:
            req = Request(
                url,
                method="GET",
                headers={"User-Agent": "JukeboxGoLibrespot/1.0"},
            )
            with urlopen(req, timeout=25) as resp:  # noqa: S310
                data = resp.read()
                ct = (resp.headers.get("Content-Type") or "").lower() if resp.headers else ""
        except (OSError, HTTPError, URLError) as e:
            _log.debug("Cover download failed: %s", e)
            return
        if not data:
            return
        self._covers.mkdir(parents=True, exist_ok=True)
        ext = _guess_ext(ct)
        fname = f"{key}{ext}"
        path = self._covers / fname
        tmp = path.with_suffix(path.suffix + ".part")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, path)
            rel = f"covers/{fname}"
        except OSError as e:
            _log.debug("Failed writing cover: %s", e)
            try:
                if tmp.is_file():
                    tmp.unlink()
            except OSError:
                pass
            return
        with self._lock:
            for i, it in enumerate(self._items):
                if it.entry_id == entry_id and rel is not None:
                    self._items[i] = replace(it, cover_path=rel)
                    try:
                        self._save_locked()
                    except OSError as e:
                        _log.debug("Re-save after cover: %s", e)
                    if on_art_ready is not None:
                        on_art_ready()
                    break
