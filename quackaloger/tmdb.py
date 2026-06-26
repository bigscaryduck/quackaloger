"""TMDB API client with versioned per-library JSON cache and TTL hints."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

try:
    import requests
except ImportError:
    requests = None

from quackaloger.ui import ui

CACHE_FILENAME = "tmdb.json"
CACHE_SCHEMA_VERSION = 1
# Seconds — conservative defaults; tune per Plex/TMDB usage patterns
TTL_SEARCH_MOVIE = 86400
TTL_SEARCH_TV = 86400
TTL_MOVIE_DETAIL = 604800
TTL_TV_DETAIL = 604800


def _round_pop(value: Any) -> float:
    try:
        return round(float(value or 0.0), 1)
    except (TypeError, ValueError):
        return 0.0


def tv_candidate(r: dict[str, Any]) -> dict[str, Any]:
    """Compact, disambiguation-rich view of a TMDB TV search hit for the LLM.

    Beyond name + air date, this surfaces the signals that separate regional
    editions of the same format (e.g. an AU/NZ/UK version): original_name,
    origin_country, original_language, plus popularity to break ties.
    """
    return {
        "tmdb_id": r.get("id"),
        "name": r.get("name"),
        "original_name": r.get("original_name"),
        "origin_country": r.get("origin_country"),
        "original_language": r.get("original_language"),
        "first_air_date": r.get("first_air_date"),
        "popularity": _round_pop(r.get("popularity")),
        "vote_average": r.get("vote_average"),
    }


def movie_candidate(r: dict[str, Any]) -> dict[str, Any]:
    """Compact, disambiguation-rich view of a TMDB movie search hit for the LLM."""
    return {
        "tmdb_id": r.get("id"),
        "title": r.get("title"),
        "original_title": r.get("original_title"),
        "original_language": r.get("original_language"),
        "release_date": r.get("release_date"),
        "popularity": _round_pop(r.get("popularity")),
        "vote_average": r.get("vote_average"),
    }


def _cache_path(tool_dir: str) -> str:
    return os.path.join(tool_dir, "cache", CACHE_FILENAME)


def _load(tool_dir: str) -> dict[str, Any]:
    p = _cache_path(tool_dir)
    if not os.path.exists(p):
        return {"v": CACHE_SCHEMA_VERSION, "entries": {}}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or data.get("v") != CACHE_SCHEMA_VERSION:
            return {"v": CACHE_SCHEMA_VERSION, "entries": {}}
        data.setdefault("entries", {})
        return data
    except Exception:
        return {"v": CACHE_SCHEMA_VERSION, "entries": {}}


def _save(tool_dir: str, data: dict[str, Any]) -> None:
    d = os.path.join(tool_dir, "cache")
    os.makedirs(d, exist_ok=True)
    p = _cache_path(tool_dir)
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        ui.warn(f"Could not save TMDB cache: {e}")


def _get_cached(data: dict[str, Any], key: str, ttl: int) -> Optional[Any]:
    ent = data["entries"].get(key)
    if not ent or "t" not in ent or "payload" not in ent:
        return None
    if time.time() - float(ent["t"]) > ttl:
        return None
    return ent["payload"]


def _set_cached(data: dict[str, Any], key: str, payload: Any) -> None:
    data["entries"][key] = {"t": time.time(), "payload": payload}


class TmdbClient:
    def __init__(self, api_key: str, tool_dir: str, *, language: str = "en-US"):
        self.api_key = api_key
        self.tool_dir = tool_dir
        self.language = language
        self._base = "https://api.themoviedb.org/3"

    def search_movie(self, query: str, *, verbose: bool = False) -> list[dict[str, Any]]:
        if not self.api_key or requests is None:
            return []
        cache = _load(self.tool_dir)
        key = f"movie_search:{query.lower()}:{self.language}"
        hit = _get_cached(cache, key, TTL_SEARCH_MOVIE)
        if hit is not None:
            return hit
        try:
            r = requests.get(
                f"{self._base}/search/movie",
                params={"api_key": self.api_key, "query": query, "language": self.language},
                timeout=15,
            )
            r.raise_for_status()
            results = r.json().get("results", []) or []
            _set_cached(cache, key, results)
            _save(self.tool_dir, cache)
            if verbose:
                ui.verbose(f"[TMDB] movie search '{query}': {len(results)} hits")
            return results
        except Exception as e:
            if verbose:
                ui.verbose(f"[TMDB] movie search failed: {e}")
            return []

    def search_tv(self, query: str, *, verbose: bool = False) -> list[dict[str, Any]]:
        if not self.api_key or requests is None:
            return []
        cache = _load(self.tool_dir)
        key = f"tv_search:{query.lower()}:{self.language}"
        hit = _get_cached(cache, key, TTL_SEARCH_TV)
        if hit is not None:
            return hit
        try:
            r = requests.get(
                f"{self._base}/search/tv",
                params={"api_key": self.api_key, "query": query, "language": self.language},
                timeout=15,
            )
            r.raise_for_status()
            results = r.json().get("results", []) or []
            _set_cached(cache, key, results)
            _save(self.tool_dir, cache)
            if verbose:
                ui.verbose(f"[TMDB] tv search '{query}': {len(results)} hits")
            return results
        except Exception as e:
            if verbose:
                ui.verbose(f"[TMDB] tv search failed: {e}")
            return []

    def movie_detail(self, tmdb_id: int, *, verbose: bool = False) -> dict[str, Any]:
        if not self.api_key or requests is None:
            return {}
        cache = _load(self.tool_dir)
        key = f"movie_detail:{tmdb_id}:{self.language}"
        hit = _get_cached(cache, key, TTL_MOVIE_DETAIL)
        if hit is not None:
            return hit
        try:
            r = requests.get(
                f"{self._base}/movie/{tmdb_id}",
                params={"api_key": self.api_key, "language": self.language},
                timeout=15,
            )
            r.raise_for_status()
            payload = r.json()
            _set_cached(cache, key, payload)
            _save(self.tool_dir, cache)
            return payload
        except Exception as e:
            if verbose:
                ui.verbose(f"[TMDB] movie detail failed: {e}")
            return {}

    def tv_detail(self, tmdb_id: int, *, verbose: bool = False) -> dict[str, Any]:
        if not self.api_key or requests is None:
            return {}
        cache = _load(self.tool_dir)
        key = f"tv_detail:{tmdb_id}:{self.language}"
        hit = _get_cached(cache, key, TTL_TV_DETAIL)
        if hit is not None:
            return hit
        try:
            r = requests.get(
                f"{self._base}/tv/{tmdb_id}",
                params={"api_key": self.api_key, "language": self.language},
                timeout=15,
            )
            r.raise_for_status()
            payload = r.json()
            _set_cached(cache, key, payload)
            _save(self.tool_dir, cache)
            return payload
        except Exception as e:
            if verbose:
                ui.verbose(f"[TMDB] tv detail failed: {e}")
            return {}
