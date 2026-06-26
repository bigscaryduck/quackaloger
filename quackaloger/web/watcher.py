"""Folder-watch manager: turn "new media landed" into queued scan/organize jobs.

Runs inside the web process. One observer watches every enabled library; relevant
media events are debounced and then submitted to the same single worker as manual
runs. On Unraid, user shares are FUSE/shfs and frequently drop inotify events, so
set ``QUACK_WATCH_POLLING=1`` to use watchdog's polling observer instead.
"""

from __future__ import annotations

import os
import threading
from typing import Optional

from quackaloger.constants import AUDIO_EXTENSIONS, TOOL_DIR_NAME, VIDEO_EXTENSIONS
from quackaloger.ui import ui
from quackaloger.web import actions, state
from quackaloger.web.jobs import manager

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
    from watchdog.observers.polling import PollingObserver
    WATCHDOG_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    WATCHDOG_AVAILABLE = False
    FileSystemEventHandler = object  # type: ignore

MEDIA_EXTS = {e.lower() for e in (set(AUDIO_EXTENSIONS) | set(VIDEO_EXTENSIONS))}


def use_polling() -> bool:
    return os.environ.get("QUACK_WATCH_POLLING", "").strip().lower() in ("1", "true", "yes")


class _LibraryHandler(FileSystemEventHandler):
    """Debounced handler for one library directory."""

    def __init__(self, library: dict):
        self.library_id = library["id"]
        watch = library.get("watch") or {}
        self.debounce = max(1, int(watch.get("debounce_seconds", 30)))
        self.tool_dir = os.path.join(os.path.abspath(library["path"]), TOOL_DIR_NAME).lower()
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    def _is_relevant(self, path: str, is_dir: bool) -> bool:
        if is_dir or not path:
            return False
        ap = os.path.abspath(path)
        # Ignore the tool dir (cache/history/trash/needs-review) to avoid loops.
        if ap.lower().startswith(self.tool_dir):
            return False
        return os.path.splitext(ap)[1].lower() in MEDIA_EXTS

    def on_created(self, event):
        self._consider(event.src_path, event.is_directory)

    def on_moved(self, event):
        self._consider(getattr(event, "dest_path", "") or event.src_path, event.is_directory)

    def _consider(self, path: str, is_dir: bool) -> None:
        if self._is_relevant(path, is_dir):
            self._arm()

    def _arm(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        # Reload the latest library config; watch settings may have changed.
        lib = state.get_library(self.library_id)
        if not lib or not (lib.get("watch") or {}).get("enabled"):
            return
        if manager.library_busy(lib["id"]):
            # A job for this library is in flight; retry after another quiet window
            # so the organizer's own writes never re-trigger us mid-run.
            self._arm()
            return
        ui.info(
            f"Watch: changes in {lib.get('name') or lib['path']} -> "
            f"queueing {lib['watch']['mode']}"
        )
        actions.submit_auto(lib)

    def cancel(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


class WatcherManager:
    def __init__(self):
        self._observer = None
        self._handlers: list = []
        self._lock = threading.Lock()
        self._running = False

    def start(self) -> None:
        with self._lock:
            self._running = True
            self._build()

    def stop(self) -> None:
        with self._lock:
            self._running = False
            self._teardown()

    def reload(self) -> None:
        """Rebuild observers after libraries.json changes."""
        with self._lock:
            if not self._running:
                return
            self._teardown()
            self._build()

    def status(self) -> dict:
        return {
            "available": WATCHDOG_AVAILABLE,
            "running": self._running,
            "polling": use_polling(),
            "watched": len(self._handlers),
        }

    # -- internals (call with _lock held) -------------------------------

    def _build(self) -> None:
        if not WATCHDOG_AVAILABLE:
            ui.warn("watchdog not installed; folder-watch disabled. pip install quackaloger[web]")
            return
        observer = (PollingObserver if use_polling() else Observer)()
        scheduled = 0
        for lib in state.list_libraries():
            watch = lib.get("watch") or {}
            if not watch.get("enabled") or watch.get("mode") == "off":
                continue
            path = lib.get("path") or ""
            if not os.path.isdir(path):
                ui.warn(f"Watch: path not found, skipping: {path}")
                continue
            handler = _LibraryHandler(lib)
            try:
                observer.schedule(handler, path, recursive=True)
                self._handlers.append(handler)
                scheduled += 1
            except Exception as e:  # noqa: BLE001
                ui.warn(f"Watch: could not watch {path}: {e}")
        if scheduled:
            observer.start()
            self._observer = observer
            ui.info(
                f"Folder-watch active on {scheduled} librar"
                f"{'y' if scheduled == 1 else 'ies'} "
                f"({'polling' if use_polling() else 'native'})."
            )

    def _teardown(self) -> None:
        for handler in self._handlers:
            handler.cancel()
        self._handlers = []
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=5)
            except Exception:  # noqa: BLE001
                pass
            self._observer = None


# Module-level singleton
watcher = WatcherManager()
