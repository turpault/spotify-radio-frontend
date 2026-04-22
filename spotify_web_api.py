"""
Spotify Web API (official HTTPS) for playlist metadata.

- Reference: https://developer.spotify.com/documentation/web-api
- Get current user playlists: ``GET https://api.spotify.com/v1/me/playlists``
  https://developer.spotify.com/documentation/web-api/reference/get-a-list-of-current-users-playlists

Uses OAuth2 access tokens (env ``SPOTIFY_ACCESS_TOKEN`` or a JSON file from PKCE
flow; same shape as ``frontend-python/spotify_web.py``). Required scopes
include ``playlist-read-private`` (and ``playlist-read-collaborative`` for
collaborative lists). Set ``SPOTIFY_CLIENT_ID`` to allow refresh.

App-only (**client credentials** / 2LO) tokens: set ``SPOTIFY_CLIENT_ID`` and
``SPOTIFY_CLIENT_SECRET`` (e.g. from a ``.env`` file) to call public catalog
endpoints such as ``GET /v1/playlists/{id}`` without a user session.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from gls_client import GlsConfig, _log_http_error_response, get_json

_log = logging.getLogger("gls-client")

ACCOUNTS_API = "https://accounts.spotify.com/api"
SPOTIFY_API = "https://api.spotify.com/v1"


@dataclass
class MePlaylist:
    """One row from ``GET /v1/me/playlists``."""

    name: str
    uri: str  # e.g. spotify:playlist:… — use with go-librespot POST /player/play


def parse_me_playlist_items(items: list[Any]) -> list[MePlaylist]:
    """
    Map Spotify `items` array to :class:`MePlaylist`.
    https://developer.spotify.com/documentation/web-api/reference/get-a-list-of-current-users-playlists
    """
    out: list[MePlaylist] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        name = it.get("name")
        name_s = str(name) if name is not None else "—"
        raw_uri = it.get("uri")
        if raw_uri is not None and str(raw_uri).strip():
            uri = str(raw_uri).strip()
        else:
            pid = it.get("id")
            if pid is not None and str(pid).strip():
                uri = f"spotify:playlist:{str(pid).strip()}"
            else:
                uri = ""
        if not uri:
            _log.debug("me/playlists: skip row without id/uri: %r", it)
            continue
        out.append(MePlaylist(name=name_s, uri=uri))
    if not out and items:
        _log.warning(
            "me/playlists: %d item(s) but 0 parsable; sample keys: %s",
            len(items),
            list(items[0].keys()) if items and isinstance(items[0], dict) else None,
        )
    return out


def get_me_playlists_gls_proxy(
    cfg: Optional[GlsConfig] = None, *, limit: int = 6, offset: int = 0
) -> list[MePlaylist]:
    """
    Playlists via go-librespot ``/web-api/v1/...`` (session proxy, not a public API contract).
    """
    c = cfg or GlsConfig.from_env()
    path = f"/web-api/v1/me/playlists?limit={int(limit)}&offset={int(offset)}"
    data = get_json(path, cfg=c)
    if data is None:
        _log.warning("me/playlists: empty body (proxy)")
        return []
    if isinstance(data, list):
        items: Any = data
    elif isinstance(data, dict):
        items = data.get("items")
    else:
        _log.warning("me/playlists: unexpected top-level type %s", type(data))
        return []
    if not isinstance(items, list):
        _log.warning(
            "me/playlists: items not a list, keys=%s",
            list(data.keys()) if isinstance(data, dict) else None,
        )
        return []
    out = parse_me_playlist_items(items)
    _log.info("me/playlists (proxy): returning %d playlist(s) for limit=%s", len(out), limit)
    return out


_playlist_oauth_missing_logged: bool = False


def _log_spotify_oauth_missing_once(cfg: Optional[GlsConfig]) -> None:
    global _playlist_oauth_missing_logged
    if _playlist_oauth_missing_logged:
        return
    _playlist_oauth_missing_logged = True
    base = (cfg or GlsConfig.from_env()).base
    _log.warning(
        "me/playlists: NOT calling https://api.spotify.com (no OAuth). "
        "Set env SPOTIFY_ACCESS_TOKEN, or add a token file (see %s). "
        "Using go-librespot HTTP proxy at %s/web-api/… instead.",
        default_token_path(),
        base,
    )


def get_me_playlists(
    cfg: Optional[GlsConfig] = None, *, limit: int = 6, offset: int = 0
) -> list[MePlaylist]:
    """OAuth → api.spotify.com, else go-librespot /web-api/ proxy."""
    if (os.environ.get("GOLIBRESPOT_FORCE_LIBRESPOT_PLAYLISTS") or "").strip() in (
        "1",
        "true",
        "yes",
    ):
        _log.info(
            "me/playlists: GOLIBRESPOT_FORCE_LIBRESPOT_PLAYLISTS — using daemon /web-api/ only"
        )
        return get_me_playlists_gls_proxy(cfg, limit=limit, offset=offset)
    if is_configured():
        _log.info("me/playlists: GET https://api.spotify.com/v1/me/playlists (OAuth)")
        try:
            return fetch_current_user_playlists(cfg, limit=limit, offset=offset)
        except Exception as e:
            _log.warning(
                "me/playlists: Web API call failed (%s), falling back to go-librespot proxy", e
            )
            return get_me_playlists_gls_proxy(cfg, limit=limit, offset=offset)
    _log_spotify_oauth_missing_once(cfg)
    return get_me_playlists_gls_proxy(cfg, limit=limit, offset=offset)


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


def client_credentials_configured() -> bool:
    """True when both app id and secret are set (suitable for client-credentials grant)."""
    cid = (os.environ.get("SPOTIFY_CLIENT_ID") or "").strip()
    secret = (os.environ.get("SPOTIFY_CLIENT_SECRET") or "").strip()
    return bool(cid and secret)


_cc_lock = threading.Lock()
_cc_token: Optional[str] = None
_cc_expires_at: float = 0.0


def _fetch_client_credentials_token_and_ttl() -> tuple[str, int]:
    """
    Return ``(access_token, expires_in)`` from the token endpoint.
    Raises :class:`SpotifyWebApiError` on failure.

    https://developer.spotify.com/documentation/web-api/tutorials/client-credentials-flow
    """
    cid = (os.environ.get("SPOTIFY_CLIENT_ID") or "").strip()
    secret = (os.environ.get("SPOTIFY_CLIENT_SECRET") or "").strip()
    if not cid or not secret:
        raise SpotifyWebApiError(
            "Client credentials: set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET"
        )
    basic = base64.b64encode(f"{cid}:{secret}".encode("utf-8")).decode("ascii")
    body = urlencode({"grant_type": "client_credentials"})
    url = f"{ACCOUNTS_API}/token"
    req = Request(
        url,
        data=body.encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urlopen(req, timeout=30) as resp:  # noqa: S310
            code = resp.getcode() or 0
            raw = resp.read()
    except HTTPError as e:
        raw = e.read() or b""
        _log_http_error_response("POST", url, e, raw)
        raise SpotifyWebApiError(
            f"Client credentials: HTTP {e.code} {raw[:300]!r}"
        ) from e
    except URLError as e:
        raise SpotifyWebApiError(str(e)) from e
    if code != 200:
        raise SpotifyWebApiError(f"Client credentials: HTTP {code} {raw[:300]!r}")
    j = json.loads(raw.decode("utf-8"))
    tok = j.get("access_token")
    if not isinstance(tok, str) or not tok:
        raise SpotifyWebApiError("Client credentials: no access_token in response")
    expires_in = int(j.get("expires_in", 3600))
    return tok, expires_in


def fetch_client_credentials_access_token() -> str:
    """Obtain a fresh app-only token (always contacts accounts.spotify.com)."""
    tok, _ = _fetch_client_credentials_token_and_ttl()
    return tok


def get_client_credentials_access_token_cached() -> Optional[str]:
    """
    Return an app-only token, using an in-memory cache until shortly before
    ``expires_in`` from Spotify's response.
    """
    global _cc_token, _cc_expires_at
    if not client_credentials_configured():
        return None
    with _cc_lock:
        now = time.time()
        if _cc_token and now < _cc_expires_at:
            return _cc_token
        try:
            tok, expires_in = _fetch_client_credentials_token_and_ttl()
        except SpotifyWebApiError as e:
            _log.warning("Spotify client-credentials (2LO): %s", e)
            return None
        _cc_token = tok
        _cc_expires_at = time.time() + float(expires_in) - 60.0
        _log.info(
            "Spotify client-credentials (2LO): obtained app access token (expires in ~%ds)",
            int(expires_in),
        )
        return _cc_token


def try_client_credentials_access_token() -> Optional[str]:
    """Return app-only token, or ``None`` if not configured or request fails."""
    return get_client_credentials_access_token_cached()


def parse_spotify_uri(uri: str) -> Optional[tuple[str, str]]:
    """
    Parse ``spotify:<type>:<id>`` into ``(type, id)``.
    Returns ``None`` if the string is not a three-part Spotify URI.
    """
    u = (uri or "").strip()
    parts = u.split(":", 2)
    if len(parts) != 3 or parts[0] != "spotify":
        return None
    kind, sid = parts[1], parts[2]
    if not kind or not sid:
        return None
    return kind, sid


def _catalog_path_for_uri_type(uri_type: str, resource_id: str) -> Optional[str]:
    """Relative path under ``/v1`` for a public catalog GET, or ``None`` if unsupported."""
    u = uri_type.lower()
    if u == "playlist":
        return f"/playlists/{resource_id}"
    if u == "track":
        return f"/tracks/{resource_id}"
    if u == "album":
        return f"/albums/{resource_id}"
    if u == "artist":
        return f"/artists/{resource_id}"
    if u == "show":
        return f"/shows/{resource_id}"
    if u == "episode":
        return f"/episodes/{resource_id}"
    return None


def summarize_catalog_json(uri_type: str, j: dict[str, Any]) -> dict[str, Any]:
    """Small dict for logging; shape depends on ``uri_type``."""
    u = uri_type.lower()
    out: dict[str, Any] = {"kind": u, "id": j.get("id"), "name": j.get("name")}
    if u == "playlist":
        owner = j.get("owner")
        if isinstance(owner, dict):
            out["owner"] = owner.get("display_name")
        out["public"] = j.get("public")
        out["snapshot_id"] = j.get("snapshot_id")
    elif u in ("track", "album"):
        arts = j.get("artists")
        if isinstance(arts, list):
            names = [
                a.get("name")
                for a in arts
                if isinstance(a, dict) and a.get("name") is not None
            ]
            if names:
                out["artists"] = names
    elif u == "show":
        out["publisher"] = j.get("publisher")
    return out


def fetch_public_catalog_summary(access_token: str, spotify_uri: str) -> dict[str, Any]:
    """
    ``GET`` one public catalog object and return a short summary for logging.

    Unsupported URI types return ``{"error": "unsupported_uri_type", ...}``.
    """
    parsed = parse_spotify_uri(spotify_uri)
    if not parsed:
        return {"error": "not_a_spotify_uri", "uri": spotify_uri}
    uri_type, resource_id = parsed
    rel = _catalog_path_for_uri_type(uri_type, resource_id)
    if not rel:
        return {"error": "unsupported_uri_type", "uri": spotify_uri, "type": uri_type}
    url = f"{SPOTIFY_API}{rel}"
    code, raw = _spotify_get(url, access_token)
    if code != 200:
        return {
            "error": f"HTTP_{code}",
            "uri": spotify_uri,
            "body_preview": raw.decode("utf-8", errors="replace")[:400],
        }
    try:
        j = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {"error": "invalid_json", "uri": spotify_uri}
    if not isinstance(j, dict):
        return {"error": "unexpected_shape", "uri": spotify_uri}
    return summarize_catalog_json(uri_type, j)


def log_playlist_rows_with_client_credentials(
    access_token: str, playlists: list[MePlaylist]
) -> None:
    """Resolve each row's ``uri`` via the Web API and log summaries (console / logger)."""
    _log.info(
        "Spotify client-credentials (2LO): resolving %d playlist-list URI(s)",
        len(playlists),
    )
    for row in playlists:
        summary = fetch_public_catalog_summary(access_token, row.uri)
        _log.info(
            "Spotify catalog (2LO): list_row name=%r uri=%s -> %s",
            row.name,
            row.uri,
            summary,
        )


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
