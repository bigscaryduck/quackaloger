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
SXXEYY_RE = re.compile(r"S(\d+)E(\d+)", re.IGNORECASE)


def _skip_tool(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    return TOOL_DIR_NAME in parts


def scan_movie_books(library_root: str, *, verbose: bool = False) -> list[Book]:
    """One Book per folder that contains video files (skips TV Season folders)."""
    library_root = os.path.abspath(library_root)
    by_parent: dict[str, list[str]] = defaultdict(list)

    for dirpath, _dirnames, filenames in os.walk(library_root):
        if _skip_tool(dirpath):
            continue
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in VIDEO_EXTENSIONS:
                continue
            full = os.path.join(dirpath, fn)
            parent = os.path.dirname(full)
            base = os.path.basename(parent)
            if SEASON_DIR_RE.match(base):
                continue
            by_parent[parent].append(full)

    books: list[Book] = []
    for parent, paths in sorted(by_parent.items()):
        paths.sort()
        files = [scan_audio_file(p, library_root, verbose=verbose) for p in paths]
        bid = f"movie-{uuid.uuid4().hex[:10]}"
        book = Book(book_id=bid, files=files, source_dir=parent, domain_tag="plex_movies")
        books.append(book)
    return books


def scan_tv_episode_books(library_root: str, *, verbose: bool = False) -> list[Book]:
    """One Book per episode file found under .../Show/Season xx/..."""
    library_root = os.path.abspath(library_root)
    books: list[Book] = []

    for dirpath, _dirnames, filenames in os.walk(library_root):
        if _skip_tool(dirpath):
            continue
        base = os.path.basename(dirpath)
        sm = SEASON_DIR_RE.match(base)
        if not sm:
            continue
        season = int(sm.group(1))
        show_dir = os.path.dirname(dirpath)
        show_title = os.path.basename(show_dir)

        for fn in sorted(filenames):
            ext = os.path.splitext(fn)[1].lower()
            if ext not in VIDEO_EXTENSIONS:
                continue
            full = os.path.join(dirpath, fn)
            files = [scan_audio_file(full, library_root, verbose=verbose)]
            m = SXXEYY_RE.search(fn)
            ep = int(m.group(2)) if m else None
            bid = f"tv-{uuid.uuid4().hex[:10]}"
            book = Book(
                book_id=bid,
                files=files,
                source_dir=os.path.dirname(full),
                domain_tag="plex_tv",
            )
            book.title = fn
            book.series = show_title
            if ep is not None:
                book.sequence = str(ep)
            first = book.files[0]
            first.path_series_hint = show_title
            first.path_title_hint = os.path.splitext(fn)[0]
            first.fn_book_number = season
            books.append(book)
    if verbose and not books:
        ui.verbose("[plex_tv] No files found under Season folders.")
    return books
