"""Scan video files into Book groups for Plex-oriented domains."""

from __future__ import annotations

import os
import re
import uuid
from collections import defaultdict

from quackaloger.constants import TOOL_DIR_NAME, VIDEO_EXTENSIONS
from quackaloger.discovery import scan_audio_file
from quackaloger.models import Book
from quackaloger.ui import ui

SEASON_DIR_RE = re.compile(r"^Season\s+(\d+)$", re.IGNORECASE)
# SxxEyy (optional space) and the 1x02 alternate form.
SXXEYY_RE = re.compile(r"S(\d{1,2})\s*E(\d{1,3})", re.IGNORECASE)
NXNN_RE = re.compile(r"(?<![A-Za-z0-9])(\d{1,2})x(\d{2,3})(?![A-Za-z0-9])", re.IGNORECASE)
# Leading indexer/site noise like "www.UIndex.org    -    "
SITE_PREFIX_RE = re.compile(r"^\s*www\.\S+\s*-\s*", re.IGNORECASE)
# First release/quality token marks the end of any human-meaningful text.
JUNK_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])("
    r"\d{3,4}p|x26[45]|h\.?26[45]|hevc|web[-. ]?dl|webrip|web|hdtv|"
    r"bluray|bdrip|brrip|dvdrip|hdrip|amzn|nf|dsnp|atvp|hmax|"
    r"ddp?\d?|dd[+p]?\d?|aac\d?|ac3|eac3|flac|disc|repack|proper|internal|"
    r"\d{1,2}bit|hdr|dolby|atmos|remux|uindex|eztv"
    r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def _skip_tool(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    return TOOL_DIR_NAME in parts


def _normalize_name(raw: str) -> str:
    """Turn a dotted/underscored release string into readable spaced text."""
    s = (raw or "").replace(".", " ").replace("_", " ")
    s = re.sub(r"\s+", " ", s).strip(" -_.")
    return s


def _match_episode(name: str):
    """Find a season/episode marker in *name*. Returns (season, episode, start, end) or None."""
    m = SXXEYY_RE.search(name)
    if m:
        return int(m.group(1)), int(m.group(2)), m.start(), m.end()
    m = NXNN_RE.search(name)
    if m:
        return int(m.group(1)), int(m.group(2)), m.start(), m.end()
    return None


def _show_title_from(before: str) -> str:
    """Show name = the text preceding the SxxEyy marker, minus site noise."""
    return _normalize_name(SITE_PREFIX_RE.sub("", before))


def _episode_title_from(after: str) -> str:
    """Episode title = text after the marker, truncated at the first release token."""
    s = after.replace(".", " ").replace("_", " ")
    jm = JUNK_TOKEN_RE.search(s)
    if jm:
        s = s[: jm.start()]
    return _normalize_name(s)


def _resolve_tv_file(stem: str, parent: str, grandparent: str):
    """Derive (show_title, season, episode, episode_title) for one video file.

    Tries the filename first, then the immediate folder name (release dirs), then
    the classic ``Show/Season NN/`` layout. Returns None if nothing looks like a
    TV episode.
    """
    # 1) SxxEyy in the filename itself (loose files and release-named files).
    hit = _match_episode(stem)
    if hit:
        season, episode, s, e = hit
        show = _show_title_from(stem[:s])
        if not show:
            show = _normalize_name(SITE_PREFIX_RE.sub("", parent)) or _normalize_name(grandparent)
        return show, season, episode, _episode_title_from(stem[e:])

    # 2) SxxEyy in the containing folder name (e.g. "Show S01E02 1080p .../file.mkv").
    hit = _match_episode(parent)
    if hit:
        season, episode, s, e = hit
        show = _show_title_from(parent[:s]) or _normalize_name(grandparent)
        return show, season, episode, ""

    # 3) Classic Plex layout: .../Show/Season NN/episode.ext
    sm = SEASON_DIR_RE.match(parent)
    if sm:
        season = int(sm.group(1))
        em = SXXEYY_RE.search(stem) or NXNN_RE.search(stem)
        episode = int(em.group(2)) if em else None
        return _normalize_name(grandparent), season, episode, ""

    return None


def scan_movie_books(library_root: str, *, verbose: bool = False) -> list[Book]:
    """One Book per folder that contains movie video files.

    Anything that looks like a TV episode is left for the plex_tv domain: files
    inside a ``Season NN`` folder, and files whose name or containing folder
    carries an SxxEyy/1x02 marker. This keeps episodes out of the movie plan and
    prevents the two Plex domains from both claiming the same file.
    """
    library_root = os.path.abspath(library_root)
    by_parent: dict[str, list[str]] = defaultdict(list)
    skipped_episodes = 0

    for dirpath, _dirnames, filenames in os.walk(library_root):
        if _skip_tool(dirpath):
            continue
        base = os.path.basename(dirpath)
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in VIDEO_EXTENSIONS:
                continue
            if SEASON_DIR_RE.match(base):
                continue
            stem = os.path.splitext(fn)[0]
            if _match_episode(stem) or _match_episode(base):
                skipped_episodes += 1
                if verbose:
                    ui.verbose(f"[plex_movies] Skipped TV episode (left for plex_tv): {fn}")
                continue
            by_parent[dirpath].append(os.path.join(dirpath, fn))

    if verbose and skipped_episodes:
        ui.verbose(f"[plex_movies] Skipped {skipped_episodes} episode-like file(s).")

    books: list[Book] = []
    for parent, paths in sorted(by_parent.items()):
        paths.sort()
        files = [scan_audio_file(p, library_root, verbose=verbose) for p in paths]
        bid = f"movie-{uuid.uuid4().hex[:10]}"
        book = Book(book_id=bid, files=files, source_dir=parent, domain_tag="plex_movies")
        books.append(book)
    return books


def scan_tv_episode_books(library_root: str, *, verbose: bool = False) -> list[Book]:
    """One Book per episode video file anywhere in the tree.

    Episodes are recognized by an SxxEyy (or 1x02) marker in the file name, its
    containing folder name, or a classic ``Show/Season NN/`` layout -- so loose
    downloads and release-named folders are picked up, not just pre-foldered
    libraries. The show title is parsed from the name preceding the marker.
    """
    library_root = os.path.abspath(library_root)
    books: list[Book] = []

    for dirpath, _dirnames, filenames in os.walk(library_root):
        if _skip_tool(dirpath):
            continue
        parent = os.path.basename(dirpath)
        grandparent = os.path.basename(os.path.dirname(dirpath))

        for fn in sorted(filenames):
            ext = os.path.splitext(fn)[1].lower()
            if ext not in VIDEO_EXTENSIONS:
                continue
            stem = os.path.splitext(fn)[0]
            resolved = _resolve_tv_file(stem, parent, grandparent)
            if resolved is None:
                if verbose:
                    ui.verbose(f"[plex_tv] Skipped (no SxxEyy marker): {fn}")
                continue
            show_title, season, episode, ep_title = resolved

            full = os.path.join(dirpath, fn)
            files = [scan_audio_file(full, library_root, verbose=verbose)]
            book = Book(
                book_id=f"tv-{uuid.uuid4().hex[:10]}",
                files=files,
                source_dir=dirpath,
                domain_tag="plex_tv",
            )
            book.title = ep_title  # may be empty; better than a raw release string
            book.series = show_title or "Unknown"
            if episode is not None:
                book.sequence = str(episode)
            first = book.files[0]
            first.path_series_hint = book.series
            first.path_title_hint = book.title
            first.fn_book_number = season
            books.append(book)

    if verbose and not books:
        ui.verbose("[plex_tv] No TV episode files recognized (looked for SxxEyy markers).")
    return books
