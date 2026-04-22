"""
HTTP client helpers for go-librespot's local API (api-spec.yml, cmd/daemon/api_server.go).

Base URL: http://127.0.0.1:3678 by default; override with GOLIBRESPOT_BASE.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

_log = logging.getLogger("gls-client")


class GlsApiError(Exception):
    """Error from the go-librespot HTTP API."""


@dataclass
class GlsConfig:
    base: str  # e.g. http://127.0.0.1:3678

    @classmethod
    def from_env(cls) -> "GlsConfig":
        base = os.environ.get("GOLIBRESPOT_BASE", "http://127.0.0.1:3678").strip()
        if not base:
            base = "http://127.0.0.1:3678"
        if not base.startswith(("http://", "https://")):
            base = "http://" + base
        return cls(base=base.rstrip("/"))

    def rest_url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return urljoin(self.base + "/", path.lstrip("/"))

    def events_ws_url(self) -> str:
        parsed = urlparse(self.base)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        host = parsed.hostname or "127.0.0.1"
        if parsed.port:
            netloc = f"{host}:{parsed.port}"
        else:
            netloc = host
        return f"{scheme}://{netloc}/events"


def _request(
    method: str,
    url: str,
    *,
    body: Optional[dict[str, Any]] = None,
    timeout: float = 10.0,
) -> tuple[int, bytes]:
    data: Optional[bytes] = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, method=method, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310 — local trusted API
            return resp.getcode() or 0, resp.read()
    except HTTPError as e:
        return e.code, (e.read() or b"")
    except URLError as e:
        raise GlsApiError(str(e)) from e


def get_json(path: str, cfg: Optional[GlsConfig] = None) -> Any:
    c = cfg or GlsConfig.from_env()
    url = c.rest_url(path)
    code, raw = _request("GET", url, body=None)
    if code != 200:
        text = raw.decode("utf-8", errors="replace")
        raise GlsApiError(f"GET {path}: HTTP {code} {text[:500]}")
    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise GlsApiError(
            f"GET {path}: invalid JSON ({e!s}): {text[:300]!r}"
        ) from e


def post_json(
    path: str,
    body: Optional[dict[str, Any]] = None,
    cfg: Optional[GlsConfig] = None,
) -> None:
    c = cfg or GlsConfig.from_env()
    url = c.rest_url(path)
    code, raw = _request("POST", url, body=body if body is not None else {})
    if code not in (200, 201, 204):
        text = raw.decode("utf-8", errors="replace")
        raise GlsApiError(f"POST {path}: HTTP {code} {text[:500]}")


@dataclass
class MePlaylist:
    """One row from GET /v1/me/playlists (via daemon /web-api proxy)."""

    name: str
    uri: str  # e.g. spotify:playlist:… — use with POST /player/play


def get_me_playlists(
    cfg: Optional[GlsConfig] = None, *, limit: int = 6, offset: int = 0
) -> list[MePlaylist]:
    """
    Current user's playlists (name + uri) via go-librespot's /web-api/ proxy (session auth).

    https://github.com/devgianlu/go-librespot — GET /web-api/* forwards to Spotify Web API.
    """
    c = cfg or GlsConfig.from_env()
    path = f"/web-api/v1/me/playlists?limit={int(limit)}&offset={int(offset)}"
    data = get_json(path, cfg=c)
    if data is None:
        _log.warning("me/playlists: empty body")
        return []
    if isinstance(data, list):
        # Unusual but tolerate a bare list from a proxy
        items = data
    elif isinstance(data, dict):
        items = data.get("items")
    else:
        _log.warning("me/playlists: unexpected top-level type %s", type(data))
        return []
    if not isinstance(items, list):
        _log.warning("me/playlists: items not a list, keys=%s", list(data.keys()) if isinstance(data, dict) else None)
        return []
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
        _log.warning("me/playlists: %d item(s) but 0 parsable; sample keys: %s", len(items), list(items[0].keys()) if items and isinstance(items[0], dict) else None)
    return out
