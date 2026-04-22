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

# Cap error body dump in logs (bytes); still log total size if larger.
_MAX_ERROR_BODY_LOG = 256 * 1024


def _headers_as_lines(msg: object) -> str:
    try:
        if hasattr(msg, "as_string"):
            return str(msg.as_string())  # type: ignore[misc]
        it = getattr(msg, "items", None)
        if callable(it):
            return "\n".join(f"{k}: {v}" for k, v in it())  # type: ignore[misc]
    except Exception:
        pass
    return repr(msg)


def _log_http_error_response(
    method: str, url: str, err: HTTPError, body: bytes
) -> None:
    """
    Log failed HTTP responses: one summary line, then headers, then body only if non-empty
    (avoids noisy "empty body" warnings for 429, etc.).
    """
    h = err.headers
    n = len(body)
    ra = ""
    if h is not None:
        ra = (h.get("Retry-After") or "").strip()
    ra_s = f" retry-after={ra!r}" if ra else ""
    _log.warning(
        "HTTP error: %s %s -> %s %s; response body %d bytes%s",
        method,
        url,
        err.code,
        getattr(err, "reason", "") or "",
        n,
        ra_s,
    )
    if h is not None:
        _log.warning("Response headers:\n%s", _headers_as_lines(h).rstrip())
    else:
        _log.debug("Response headers: (none)")
    if n == 0:
        return
    if n > _MAX_ERROR_BODY_LOG:
        chunk = body[:_MAX_ERROR_BODY_LOG].decode("utf-8", errors="replace")
        _log.warning(
            "Response body: %d bytes (first %d logged)\n%s",
            n,
            _MAX_ERROR_BODY_LOG,
            chunk,
        )
    else:
        _log.warning(
            "Response body: %d bytes\n%s",
            n,
            body.decode("utf-8", errors="replace"),
        )


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
        raw = e.read() or b""
        _log_http_error_response(method, url, e, raw)
        return e.code, raw
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
    _log.debug("get_json: %s <- HTTP 200, %d bytes", path, len(text))
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
