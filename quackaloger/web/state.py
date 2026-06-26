"""Persistent store of the user's configured libraries (web UI).

Each library is one ``{path + domain}`` pairing -- the unit the web UI scans
and organizes. Stored as JSON under the user config dir so it lives next to the
existing ``config.yaml`` (and maps to the Docker ``/config`` volume). Global
settings (API keys, provider, AI on/off) stay in the existing user config.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Optional

from quackaloger.user_config import user_config_dir

VALID_DOMAINS = ["audiobooks", "plex_movies", "plex_tv"]
WATCH_MODES = ["off", "scan-only", "auto-organize"]

DEFAULT_WATCH = {"enabled": False, "mode": "scan-only", "debounce_seconds": 30}


def _libraries_path() -> str:
    return os.path.join(user_config_dir(), "libraries.json")


def _load() -> list:
    path = _libraries_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [_normalize(x) for x in data] if isinstance(data, list) else []
    except Exception:
        return []


def _save(libs: list) -> None:
    os.makedirs(user_config_dir(), exist_ok=True)
    with open(_libraries_path(), "w", encoding="utf-8") as f:
        json.dump(libs, f, indent=2, ensure_ascii=False)


def _normalize(lib: dict) -> dict:
    """Fill in missing keys so callers can rely on a stable shape."""
    watch = {**DEFAULT_WATCH, **(lib.get("watch") or {})}
    if watch.get("mode") not in WATCH_MODES:
        watch["mode"] = "scan-only"
    try:
        watch["debounce_seconds"] = max(1, int(watch.get("debounce_seconds", 30)))
    except (TypeError, ValueError):
        watch["debounce_seconds"] = 30
    watch["enabled"] = bool(watch.get("enabled"))
    return {
        "id": lib.get("id") or uuid.uuid4().hex[:8],
        "name": lib.get("name") or "",
        "path": lib.get("path") or "",
        "domain": lib.get("domain") if lib.get("domain") in VALID_DOMAINS else "audiobooks",
        "overrides": lib.get("overrides") or {},
        "watch": watch,
    }


# ---------------------------------------------------------------------------
# Public CRUD
# ---------------------------------------------------------------------------

def list_libraries() -> list:
    return _load()


def get_library(lib_id: str) -> Optional[dict]:
    for lib in _load():
        if lib["id"] == lib_id:
            return lib
    return None


def add_library(
    *,
    name: str,
    path: str,
    domain: str,
    overrides: Optional[dict] = None,
    watch: Optional[dict] = None,
) -> dict:
    libs = _load()
    lib = _normalize({
        "id": uuid.uuid4().hex[:8],
        "name": name or os.path.basename(path.rstrip("/\\")) or domain,
        "path": path,
        "domain": domain,
        "overrides": overrides or {},
        "watch": watch or {},
    })
    libs.append(lib)
    _save(libs)
    return lib


def update_library(lib_id: str, **fields) -> Optional[dict]:
    libs = _load()
    updated = None
    for i, lib in enumerate(libs):
        if lib["id"] == lib_id:
            merged = {**lib, **{k: v for k, v in fields.items() if v is not None}}
            updated = _normalize(merged)
            libs[i] = updated
            break
    if updated is not None:
        _save(libs)
    return updated


def delete_library(lib_id: str) -> bool:
    libs = _load()
    remaining = [lib for lib in libs if lib["id"] != lib_id]
    if len(remaining) == len(libs):
        return False
    _save(remaining)
    return True
