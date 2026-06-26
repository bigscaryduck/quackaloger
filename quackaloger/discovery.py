"""Recursive file scanning, grouping files into logical books.

Handles incremental runs by checking embedded processing markers.
"""

import os
from collections import defaultdict
from datetime import datetime
from typing import Optional

from quackaloger.constants import (
    AUDIO_EXTENSIONS,
    BOOK_NUM_PATTERNS,
    IMAGE_EXTENSIONS,
    SERIES_BOOK_FILENAME_PATTERN,
    SIDECAR_EXTENSIONS,
    SIDECAR_FILES,
    TOOL_DIR_NAME,
)
from quackaloger.models import AudioFileMeta, Book
from quackaloger import metadata as meta_mod
from quackaloger.ui import ui


# ---------------------------------------------------------------------------
# Filename / folder parsing
# ---------------------------------------------------------------------------

def parse_book_number_from_filename(filename: str) -> tuple:
    """Extract (book_number: int|None, series_hint: str|None) from a filename."""
    stem = os.path.splitext(filename)[0]
    m = SERIES_BOOK_FILENAME_PATTERN.match(stem)
    if m:
        return int(m.group(2)), m.group(1).strip()
    for pat in BOOK_NUM_PATTERNS:
        m = pat.search(stem)
        if m:
            return int(m.group(1)), None
    return None, None


def parse_folder_path(filepath: str, library_root: str) -> dict:
    """Derive author/series/title hints from the folder hierarchy."""
    hints = {"author": None, "series": None, "title": None}
    rel = os.path.relpath(os.path.dirname(filepath), library_root)
    parts = [p for p in rel.replace("\\", "/").split("/") if p and p != "."]

    if len(parts) == 0:
        # Loose file in the library root: fall back to the filename (minus ext)
        # so identification still has a title hint instead of nothing.
        stem = os.path.splitext(os.path.basename(filepath))[0].strip()
        hints["title"] = stem or None
        return hints
    elif len(parts) == 1:
        folder = parts[0]
        if " - " in folder:
            author_part, title_part = folder.split(" - ", 1)
            hints["author"] = author_part.strip()
            hints["title"] = title_part.strip()
        else:
            hints["title"] = folder
    elif len(parts) == 2:
        hints["author"] = parts[0]
        hints["title"] = parts[1]
    elif len(parts) >= 3:
        hints["author"] = parts[0]
        hints["series"] = parts[1]
        hints["title"] = parts[-1]
    return hints


# ---------------------------------------------------------------------------
# Single-file scanner
# ---------------------------------------------------------------------------

def scan_audio_file(
    filepath: str,
    library_root: str,
    verbose: bool = False,
) -> AudioFileMeta:
    """Read tags and derive hints for a single audio file."""
    filename = os.path.basename(filepath)
    ext = os.path.splitext(filename)[1].lower()
    size = 0
    try:
        size = os.path.getsize(filepath)
    except OSError:
        pass

    fm = AudioFileMeta(filepath=filepath, filename=filename, extension=ext, size=size)
    tags = meta_mod.read_tags(filepath, verbose=verbose)
    fm.raw_tags = tags
    fm.tag_title = tags.get("title")
    fm.tag_album = tags.get("album") or tags.get("talb")
    fm.tag_artist = tags.get("artist") or tags.get("albumartist")
    fm.tag_album_artist = tags.get("albumartist") or tags.get("album_artist")
    fm.tag_composer = tags.get("composer")
    fm.tag_series = tags.get("series") or tags.get("mvnm")
    fm.tag_series_part = tags.get("series-part") or tags.get("mvin") or tags.get("series_part")
    fm.tag_asin = tags.get("asin") or tags.get("audible_asin")
    fm.tag_track = tags.get("tracknumber") or tags.get("track")
    fm.tag_disc = tags.get("discnumber") or tags.get("disc")

    book_num, series_hint = parse_book_number_from_filename(filename)
    fm.fn_book_number = book_num
    fm.fn_series_hint = series_hint

    path_hints = parse_folder_path(filepath, library_root)
    fm.path_author_hint = path_hints["author"]
    fm.path_series_hint = path_hints["series"]
    fm.path_title_hint = path_hints["title"]

    if verbose:
        ui.verbose(f"Scanned: {filepath}")
        if tags:
            for k, v in sorted(tags.items()):
                ui.verbose(f"  tag[{k}] = {v}")
        if book_num is not None:
            ui.verbose(f"  filename_book_number = {book_num}")
        if series_hint:
            ui.verbose(f"  filename_series_hint = {series_hint}")
        ui.verbose(f"  path hints: author={path_hints['author']}, series={path_hints['series']}, title={path_hints['title']}")

    return fm


# ---------------------------------------------------------------------------
# Incremental skip check
# ---------------------------------------------------------------------------

def _should_skip_file(filepath: str, force: bool = False) -> bool:
    """Return True if the file has a processing marker and hasn't changed since."""
    if force:
        return False
    marker = meta_mod.read_marker(filepath)
    if not marker:
        return False
    try:
        marker_time = datetime.fromisoformat(marker["processed_at"])
        file_mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
        return file_mtime <= marker_time
    except (KeyError, ValueError, OSError):
        return False


# ---------------------------------------------------------------------------
# Phase 1: Full library scan (two-pass: discover then read tags)
# ---------------------------------------------------------------------------

def scan_library(
    library_root: str,
    audio_extensions: set = None,
    image_extensions: set = None,
    ignore_folders: list = None,
    follow_symlinks: bool = False,
    force: bool = False,
    verbose: bool = False,
) -> dict:
    """Walk the library tree and collect all files by folder.

    Returns {dirpath: {"audio": [AudioFileMeta], "images": [path], "sidecars": [path], "metadata_json": path|None}}
    """
    if audio_extensions is None:
        audio_extensions = AUDIO_EXTENSIONS
    if image_extensions is None:
        image_extensions = IMAGE_EXTENSIONS
    if ignore_folders is None:
        ignore_folders = []

    folders = defaultdict(lambda: {"audio": [], "images": [], "sidecars": [], "metadata_json": None})

    ui.phase(1, f"Scanning library at {library_root}")

    # --- Pass 1: Discover all files (fast, no tag reading) ---
    ui.info("Discovering files (scanning directory tree, may take a moment on network shares)...")
    audio_paths = []  # (dirpath, filepath)
    with ui.spinner("Scanning directories..."):
        for dirpath, dirnames, filenames in os.walk(library_root, followlinks=follow_symlinks):
            rel_dir = os.path.relpath(dirpath, library_root)
            rel_parts = rel_dir.replace("\\", "/").split("/")

            if TOOL_DIR_NAME in rel_parts:
                dirnames.clear()
                continue

            skip = False
            for ignore in ignore_folders:
                if ignore in rel_parts:
                    skip = True
                    break
            if skip:
                continue

            for fname in filenames:
                fpath = os.path.join(dirpath, fname)
                ext = os.path.splitext(fname)[1].lower()

                if ext in audio_extensions:
                    audio_paths.append((dirpath, fpath))
                elif ext in image_extensions:
                    folders[dirpath]["images"].append(fpath)
                elif fname.lower() == "metadata.json":
                    folders[dirpath]["metadata_json"] = fpath
                elif ext in SIDECAR_EXTENSIONS or fname.lower() in SIDECAR_FILES:
                    folders[dirpath]["sidecars"].append(fpath)

    ui.info(f"Discovered {len(audio_paths)} audio files")

    # --- Pass 2: Read tags with progress bar ---
    skipped_incremental = 0
    progress = ui.progress(len(audio_paths), desc="Reading tags", unit="files")
    with progress:
        task = progress.add_task("Reading tags", total=len(audio_paths))
        for dirpath, fpath in audio_paths:
            if _should_skip_file(fpath, force=force):
                skipped_incremental += 1
                progress.advance(task)
                continue
            fm = scan_audio_file(fpath, library_root, verbose=verbose)
            folders[dirpath]["audio"].append(fm)
            progress.advance(task)

    audio_folder_count = sum(1 for v in folders.values() if v["audio"])
    total_files = sum(len(v["audio"]) for v in folders.values())
    ui.info(f"Found {total_files} audio files across {audio_folder_count} folders")
    if skipped_incremental:
        ui.muted(f"Skipped {skipped_incremental} previously-processed files (use --force to re-scan)")
    return dict(folders)


# ---------------------------------------------------------------------------
# Phase 2: Group into logical books
# ---------------------------------------------------------------------------

def _grouping_key(meta: AudioFileMeta) -> str:
    """Produce a key that groups files belonging to the same logical book."""
    parts = []
    if meta.fn_book_number is not None:
        parts.append(f"booknum:{meta.fn_book_number}")
    elif meta.tag_series_part is not None:
        sp = meta.tag_series_part.strip()
        if sp:
            parts.append(f"seriespart:{sp}")

    album = (meta.tag_album or "").strip().lower()
    if album:
        parts.append(f"album:{album}")

    if not parts:
        return "__same__"
    return "|".join(sorted(parts))


def group_files_into_books(folders: dict, verbose: bool = False) -> list:
    """Split each folder's audio files into logical book groups.

    Returns a list of Book objects.
    """
    if verbose:
        ui.phase(2, "Grouping files into logical books")

    books = []
    book_counter = 0

    for dirpath, contents in folders.items():
        audio_files = contents["audio"]
        if not audio_files:
            continue

        groups = defaultdict(list)
        for fm in audio_files:
            key = _grouping_key(fm)
            groups[key].append(fm)

        if len(groups) > 1 and verbose:
            ui.verbose(f"SPLIT: {dirpath} -> {len(groups)} books")
            for key, files in groups.items():
                ui.verbose(f"  Group [{key}]: {[f.filename for f in files]}")

        if len(groups) == 1 and verbose and len(audio_files) > 1:
            key = list(groups.keys())[0]
            ui.verbose(f"KEPT TOGETHER: {dirpath} ({len(audio_files)} files, key={key})")

        for key, file_group in groups.items():
            book_counter += 1
            book = Book(
                book_id=f"book_{book_counter:04d}",
                files=file_group,
                cover_files=list(contents["images"]),
                sidecar_files=list(contents["sidecars"]),
                source_dir=dirpath,
            )
            books.append(book)

    ui.info(f"Identified {len(books)} logical books")
    return books
