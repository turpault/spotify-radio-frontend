#!/usr/bin/env python3
"""Run main.py and restart when project Python sources change (venv ignored)."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError:
    print("Install watchdog: pip install -r requirements.txt")
    sys.exit(1)


def _venv_python(project_root: Path) -> Optional[Path]:
    candidates = (
        project_root / "venv" / "bin" / "python",
        project_root / "venv" / "bin" / "python3",
        project_root / "venv" / "Scripts" / "python.exe",
    )
    for p in candidates:
        if p.is_file():
            return p
    return None


class CodeChangeHandler(FileSystemEventHandler):
    def __init__(self, project_root: Path, restart_callback: Callable[[], None]) -> None:
        super().__init__()
        self._root = project_root.resolve()
        self._restart = restart_callback
        self._last_restart = 0.0
        self._debounce_s = 1.0

    def _ignore_path(self, src_path: str) -> bool:
        path = Path(src_path).resolve()
        if "__pycache__" in path.parts:
            return True
        try:
            path.relative_to(self._root / "venv")
            return True
        except ValueError:
            pass
        return False

    def on_modified(self, event: Any) -> None:
        if event.is_directory:
            return
        if not str(event.src_path).endswith(".py"):
            return
        if self._ignore_path(event.src_path):
            return
        now = time.time()
        if now - self._last_restart < self._debounce_s:
            return
        self._last_restart = now
        print(f"\n[dev] changed: {event.src_path}\n[dev] restarting…\n")
        self._restart()


class AppRunner:
    def __init__(self, script_path: Path) -> None:
        self.script_path = script_path
        self.process: Optional[subprocess.Popen] = None
        self.observer: Optional[Observer] = None
        self.running = True

    def _python(self) -> str:
        root = self.script_path.parent
        v = _venv_python(root)
        return str(v) if v else sys.executable

    def start_app(self) -> None:
        if self.process and self.process.poll() is None:
            return
        env = os.environ.copy()
        print(f"[dev] {self._python()} {self.script_path}")
        self.process = subprocess.Popen(
            [self._python(), str(self.script_path)],
            env=env,
            cwd=str(self.script_path.parent),
            stdout=sys.stdout,
            stderr=sys.stderr,
        )

    def stop_app(self) -> None:
        if not self.process or self.process.poll() is not None:
            self.process = None
            return
        print("[dev] stopping app…")
        try:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        except OSError as e:
            print(f"[dev] stop error: {e}")
        finally:
            self.process = None

    def restart_app(self) -> None:
        self.stop_app()
        time.sleep(0.3)
        self.start_app()

    def start_watcher(self, project_root: Path) -> None:
        h = CodeChangeHandler(project_root, self.restart_app)
        self.observer = Observer()
        self.observer.schedule(h, str(project_root), recursive=True)
        self.observer.start()
        print(f"[dev] watching {project_root} (venv ignored)")
        print("[dev] Ctrl+C to exit\n")

    def stop_watcher(self) -> None:
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=5)
            self.observer = None

    def run(self) -> None:
        root = self.script_path.parent

        def shutdown(*_args: object) -> None:
            print("\n[dev] shutdown")
            self.running = False
            self.stop_app()
            self.stop_watcher()
            sys.exit(0)

        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)

        self.start_app()
        self.start_watcher(root)

        try:
            while self.running:
                if self.process and self.process.poll() is not None:
                    code = self.process.returncode
                    print(f"\n[dev] app exited ({code}), restarting in 2s…\n")
                    time.sleep(2)
                    self.start_app()
                time.sleep(0.5)
        except KeyboardInterrupt:
            shutdown()


def main() -> None:
    root = Path(__file__).resolve().parent
    script = root / "main.py"
    if not script.is_file():
        print(f"Missing {script}")
        sys.exit(1)
    AppRunner(script).run()


if __name__ == "__main__":
    main()
