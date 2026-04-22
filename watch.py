#!/usr/bin/env python3
"""
Restart `main.py` when any project `.py` file changes (ignores venv, __pycache__).
Requires: pip install watchdog  (see requirements.txt)

Usage: python3 watch.py
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError:
    print("Install watchdog: pip install watchdog", file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).resolve().parent
MAIN = ROOT / "main.py"


def _python() -> str:
    for rel in ("venv/bin/python3", "venv/bin/python", "venv/Scripts/python.exe"):
        p = ROOT / rel
        if p.is_file():
            return str(p)
    return sys.executable


class _Restart(FileSystemEventHandler):
    def __init__(self, restart: Callable[[], None]) -> None:
        super().__init__()
        self._restart = restart
        self._last = 0.0
        self._debounce = 0.4

    def on_modified(self, event: object) -> None:
        if event.is_directory or not str(event.src_path).endswith(".py"):
            return
        p = Path(event.src_path).resolve()
        if "venv" in p.parts or "__pycache__" in p.parts:
            return
        now = time.time()
        if now - self._last < self._debounce:
            return
        self._last = now
        self._restart()


def main() -> None:
    if not MAIN.is_file():
        print(f"Missing {MAIN}", file=sys.stderr)
        sys.exit(1)

    proc: subprocess.Popen | None = None

    def start() -> None:
        nonlocal proc
        if proc and proc.poll() is None:
            return
        env = os.environ.copy()
        print(f"\n[watch] {_python()} {MAIN}\n", flush=True)
        proc = subprocess.Popen(
            [_python(), str(MAIN)], cwd=str(ROOT), env=env,
            stdout=None, stderr=None,
        )

    def stop() -> None:
        nonlocal proc
        if not proc or proc.poll() is not None:
            proc = None
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        except OSError as e:
            print(f"[watch] stop: {e}", file=sys.stderr)
        proc = None

    def restart() -> None:
        print(f"[watch] code changed, restarting…", flush=True)
        stop()
        time.sleep(0.1)
        start()

    def shutdown(*_a: object) -> None:
        stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    start()
    ob = Observer()
    ob.schedule(_Restart(restart), str(ROOT), recursive=True)
    ob.start()
    print(f"[watch] watching {ROOT} for .py changes (Ctrl+C to exit)\n", flush=True)
    try:
        while True:
            if proc and proc.poll() is not None:
                code = proc.returncode
                print(f"\n[watch] app exited ({code}), restart in 2s…\n", flush=True)
                time.sleep(2)
                start()
            time.sleep(0.3)
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
