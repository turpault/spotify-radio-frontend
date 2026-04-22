"""
Spotify Web API (official HTTPS) for playlist metadata.

- Reference: https://developer.spotify.com/documentation/web-api
- Get current user playlists: ``GET https://api.spotify.com/v1/me/playlists``
  https://developer.spotify.com/documentation/web-api/reference/get-a-list-of-current-users-playlists

Uses OAuth2 access tokens (env ``SPOTIFY_ACCESS_TOKEN`` or a JSON file from PKCE
flow; same shape as ``frontend-python/spotify_web.py``). Required scopes
include ``playlist-read-private`` (and ``playlist-read-collaborative`` for
collaborative lists). Set ``SPOTIFY_CLIENT_ID`` to allow refresh.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from gls_client import (
    GlsConfig,
    MePlaylist,
    _log_http_error_response,
    parse_me_playlist_items,
)

_log = logging.getLogger("gls-client")

ACCOUNTS_API = "https://accounts.spotify.com/api"
SPOTIFY_API = "https://api.spotify.com/v1"


class SpotifyWebApiError(Exception):
    """Spotify Web API (api.spotify.com) or token error."""


def default_token_path() -> Path:
    if (os.environ.get("XDG_CONFIG_HOME") or "").strip():
        root = Path(os.environ["XDG_CONFIG_HOME"].strip())
    else:
        root = Path.home() / ".config"
    return root / "jukebox-frontend-python" / "spotify_tokens.json"


def token_path() -> Path:
    p = (os.environ.get("SPOTIFY_TOKEN_PATH") or "").strip()
    if p:
        return Path(p).expanduser()
    return default_token_path()


def is_configured() -> bool:
    if (os.environ.get("SPOTIFY_ACCESS_TOKEN") or "").strip():
        return True
    p = token_path()
    try:
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


def _load_token_file() -> dict[str, Any]:
    p = token_path()
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def _save_token_file(data: dict[str, Any]) -> None:
    p = token_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token"),
                "expires_at": float(data["expires_at"]),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _log.info("Spotify: wrote refreshed token to %s", p)


def _refresh_access_token(client_id: str, refresh_tok: str) -> dict[str, Any]:
    body = urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_tok,
            "client_id": client_id,
        }
    )
    url = f"{ACCOUNTS_API}/token"
    req = Request(
        url,
        data=body.encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urlopen(req, timeout=30) as resp:  # noqa: S310
            code = resp.getcode() or 0
            raw = resp.read()
    except HTTPError as e:
        raw = e.read() or b""
        _log_http_error_response("POST", url, e, raw)
        raise SpotifyWebApiError(
            f"Token refresh: HTTP {e.code} {raw[:300]!r}"
        ) from e
    except URLError as e:
        raise SpotifyWebApiError(str(e)) from e
    if code != 200:
        raise SpotifyWebApiError(f"Token refresh: HTTP {code} {raw[:300]!r}")
    j = json.loads(raw.decode("utf-8"))
    expires_in = int(j.get("expires_in", 3600))
    return {
        "access_token": j["access_token"],
        "refresh_token": j.get("refresh_token"),
        "expires_at": time.time() + expires_in - 60.0,
    }


def get_effective_access_token() -> str:
    override = (os.environ.get("SPOTIFY_ACCESS_TOKEN") or "").strip()
    if override:
        return override

    data = _load_token_file()
    if not data.get("access_token"):
        raise SpotifyWebApiError("No access_token; set SPOTIFY_ACCESS_TOKEN or token file (SPOTIFY_TOKEN_PATH)")

    at = str(data["access_token"])
    exp = float(data.get("expires_at", 0.0))
    rt = data.get("refresh_token")
    cid = (os.environ.get("SPOTIFY_CLIENT_ID") or "").strip()

    if time.time() < exp - 30:
        return at
    if isinstance(rt, str) and rt and cid:
        new = _refresh_access_token(cid, rt)
        data["access_token"] = new["access_token"]
        data["expires_at"] = new["expires_at"]
        if new.get("refresh_token"):
            data["refresh_token"] = new["refresh_token"]
        _save_token_file(data)
        return str(data["access_token"])

    if time.time() >= exp - 30:
        raise SpotifyWebApiError(
            "Access token expired; set SPOTIFY_CLIENT_ID for refresh, or run OAuth, "
            "or set SPOTIFY_ACCESS_TOKEN"
        )
    return at


def _spotify_get(url: str, access_token: str) -> tuple[int, bytes]:
    req = Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=20) as resp:  # noqa: S310
            return resp.getcode() or 0, resp.read()
    except HTTPError as e:
        raw = e.read() or b""
        _log_http_error_response("GET", url, e, raw)
        return e.code, raw
    except URLError as e:
        raise SpotifyWebApiError(str(e)) from e


def fetch_current_user_playlists(
    _cfg: Optional[GlsConfig] = None, *, limit: int = 6, offset: int = 0
) -> list[MePlaylist]:
    """
    Call ``GET /v1/me/playlists`` on ``api.spotify.com`` with a Bearer token.
    The ``_cfg`` argument is unused (reserved for future use).
    """
    _ = _cfg
    access = get_effective_access_token()
    q = urlencode({"limit": int(limit), "offset": int(offset)})
    url = f"{SPOTIFY_API}/me/playlists?{q}"
    code, raw = _spotify_get(url, access)
    if code != 200:
        text = raw.decode("utf-8", errors="replace")
        raise SpotifyWebApiError(
            f"GET /v1/me/playlists: HTTP {code} {text[:800]}"
        )
    if not raw:
        return []
    j = json.loads(raw.decode("utf-8"))
    if not isinstance(j, dict):
        return []
    items = j.get("items")
    if not isinstance(items, list):
        return []
    out = parse_me_playlist_items(items)
    _log.info(
        "me/playlists (Spotify Web API): returning %d row(s) (limit=%s offset=%s)",
        len(out),
        limit,
        offset,
    )
    for i, p in enumerate(out):
        _log.debug(
            "  [%d] name=%r uri=%s",
            i,
            p.name,
            p.uri[:48] + "…" if len(p.uri) > 48 else p.uri,
        )
    return out
