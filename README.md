# spotify-radio-frontend

PyQt6 touchscreen UI for a local **[go-librespot](https://github.com/skokhanenko/go-librespot)** daemon. The app talks to the daemon’s HTTP API and WebSocket (`/events`) and does not require Spotify Web playback for normal transport—it starts contexts (playlists, albums, etc.) through the local player when you use the side “recent context” tiles.

**Default API base:** `http://127.0.0.1:3678` — set `GOLIBRESPOT_BASE` if your daemon listens elsewhere.

## Requirements

- Python 3.10+ (3.11+ recommended)
- A running go-librespot instance with the API enabled (same host/port the UI expects)
- For playlist / “me” features that need Spotify’s Web API: configure credentials as in [Environment](#environment) (optional for basic local playback if you only use the daemon)

## Quick start

```bash
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

**Develop with auto-reload** (restarts `main.py` when `.py` files change):

```bash
python dev.py
```

`watch.py` is a simpler file-watcher alternative; `dev.py` is the full dev runner with the same venv resolution.

## Environment

Load variables from a `.env` in the project root (via `python-dotenv`) or export them in your shell.

| Variable | Purpose |
|----------|---------|
| `GOLIBRESPOT_BASE` | Base URL for the daemon (default `http://127.0.0.1:3678`) |
| `JUKEBOX_GLS_DATA_DIR` | Per-user data (playback history, cached metadata); default under XDG config |
| `JUKEBOX_UI_LAYOUT` | Path to JSON overriding the built-in v2 layout (`ui_layout_v2_document.py`) |
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | Client-credentials tokens for public catalog / some API paths |
| `SPOTIFY_ACCESS_TOKEN` or token file | User OAuth; see `spotify_web_api.py` and `SPOTIFY_TOKEN_PATH` |
| `GLS_LOG_LEVEL` / `GLS_LOG_FILE` | App logging |

Do not commit `.env`, tokens, or `credentials.json` (see `.gitignore`).

## Layout and assets

- Layout document: `ui_layout_v2_document.py` (v2 geometry and overlays)
- Bundled OFL fonts: `fonts/` (e.g. Limelight, Corben, Share Tech Mono)
- SVG icons: `icons/` (context types, transport, etc.)

## Project layout (short)

| Path | Role |
|------|------|
| `main.py` | Application entry, UI, WebSocket, integration |
| `gls_client.py` | HTTP helpers for the daemon API |
| `spotify_web_api.py` | Spotify Web API (playlists, catalog, tokens) |
| `playback_history.py` | Recent context URIs for the side tiles |
| `ui_layout_config.py` | Loads optional `JUKEBOX_UI_LAYOUT` JSON |
| `font_loader.py` / `icon_utils.py` | Font registration and icon helpers |
