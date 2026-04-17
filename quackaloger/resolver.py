"""Multi-source metadata resolution: Audible > ID3 > filename > folder > metadata.json."""

import json
import re
from typing import Optional

from quackaloger.constants import (
    NARRATOR_BRACE_PATTERN,
    TITLE_FOLDER_SEQUENCE_PATTERNS,
)
from quackaloger.models import Book
from quackaloger.ui import ui


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_metadata_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _clean_author_name(name: str) -> str:
    if not name:
        return name
    return re.sub(r"\s+", " ", name.strip())


def _clean_title(title: str) -> str:
    if not title:
        return title
    title = NARRATOR_BRACE_PATTERN.sub("", title).strip()
    title = re.sub(r"^\d+\s*[-\u2013.]\s*", "", title)
    title = re.sub(r"^(?:Vol\.?|Volume|Book)\s*\d+\s*[-\u2013.]\s*", "", title, flags=re.IGNORECASE)
    return title.strip(" -\u2013.")


def _extract_narrator_from_folder(folder_name: str) -> Optional[str]:
    m = NARRATOR_BRACE_PATTERN.search(folder_name)
    return m.group(1).strip() if m else None


def _extract_sequence_from_folder(folder_name: str) -> Optional[str]:
    for pat in TITLE_FOLDER_SEQUENCE_PATTERNS:
        m = pat.match(folder_name)
        if m:
            for g in m.groups():
                if g and re.match(r"^\d+$", g.strip()):
                    return g.strip()
    return None


# ---------------------------------------------------------------------------
# Main resolver
# ---------------------------------------------------------------------------

def resolve_book_metadata(
    book: Book,
    folders: dict,
    confidence_threshold: float = 0.65,
    verbose: bool = False,
):
    """Resolve the best author/title/series/sequence/narrator for a Book.

    Mutates the Book in place, filling author, title, series, sequence,
    narrator, year, resolution_log, conflicts, and ambiguous.
    """
    log = []
    am = book.audible_match

    if am and am.confidence < confidence_threshold:
        log.append(
            f"Audible match below confidence threshold "
            f"({am.confidence:.2f} < {confidence_threshold}), using local metadata"
        )
        am = None

    # --- Collect local sources ---
    id3_authors, id3_albums, id3_composers = set(), set(), set()
    id3_series, id3_series_parts = set(), set()

    for f in book.files:
        if f.tag_artist:
            id3_authors.add(f.tag_artist.strip())
        if f.tag_album_artist:
            id3_authors.add(f.tag_album_artist.strip())
        if f.tag_album:
            id3_albums.add(f.tag_album.strip())
        if f.tag_composer:
            id3_composers.add(f.tag_composer.strip())
        if f.tag_series:
            id3_series.add(f.tag_series.strip())
        if f.tag_series_part:
            id3_series_parts.add(f.tag_series_part.strip())

    first_file = book.files[0]
    fn_series = first_file.fn_series_hint
    fn_seq = str(first_file.fn_book_number) if first_file.fn_book_number is not None else None

    path_author = first_file.path_author_hint
    path_series = first_file.path_series_hint
    path_title = first_file.path_title_hint
    path_narrator = _extract_narrator_from_folder(path_title) if path_title else None
    path_seq = _extract_sequence_from_folder(path_title) if path_title else None

    # Metadata JSON from the folder
    meta_json = {}
    meta_json_path = folders.get(book.source_dir, {}).get("metadata_json")
    if meta_json_path:
        meta_json = _read_metadata_json(meta_json_path)

    mj_title = meta_json.get("title")
    mj_authors = meta_json.get("authors", [])
    mj_author = mj_authors[0] if mj_authors else None
    mj_narrators = meta_json.get("narrators", [])
    mj_narrator = mj_narrators[0] if mj_narrators else None
    mj_series_list = meta_json.get("series", [])
    mj_series, mj_seq = None, None
    if mj_series_list and isinstance(mj_series_list, list) and mj_series_list:
        s = mj_series_list[0]
        if isinstance(s, dict):
            mj_series = s.get("series") or s.get("name")
            mj_seq = s.get("sequence")
        elif isinstance(s, str):
            mj_series = s
    mj_year = meta_json.get("publishedYear")

    # === Resolution: Audible > ID3 > filename > folder > metadata.json ===

    # Author
    author = None
    if am and am.author:
        author = am.author
        log.append(f"author: Audible -> '{author}'")
    if not author and id3_authors:
        author = sorted(id3_authors)[0]
        log.append(f"author: ID3 tag -> '{author}'")
        if len(id3_authors) > 1:
            book.conflicts.append(f"Multiple ID3 authors: {id3_authors}")
    if not author and path_author:
        author = _clean_author_name(path_author)
        log.append(f"author: folder path -> '{author}'")
    if not author and mj_author:
        author = _clean_author_name(mj_author)
        log.append(f"author: metadata.json -> '{author}'")
    if not author:
        author = "Unknown Author"
        book.ambiguous = True
        log.append("author: UNKNOWN")

    # Series
    series = None
    if am and am.series:
        series = am.series
        log.append(f"series: Audible -> '{series}'")
    if not series and id3_series:
        series = sorted(id3_series)[0]
        log.append(f"series: ID3 tag -> '{series}'")
    if not series and fn_series:
        series = fn_series
        log.append(f"series: filename -> '{series}'")
    if not series and path_series:
        series = path_series
        log.append(f"series: folder path -> '{series}'")
    if not series and mj_series:
        series = mj_series
        log.append(f"series: metadata.json -> '{series}'")

    # Sequence (filename-derived takes priority -- most specific to this file)
    sequence = None
    if fn_seq:
        sequence = fn_seq
        log.append(f"sequence: filename -> '{sequence}'")
        if am and am.sequence and am.sequence != fn_seq:
            log.append(f"sequence: Audible had '{am.sequence}' but filename '{fn_seq}' is more specific")
    elif am and am.sequence:
        sequence = am.sequence
        log.append(f"sequence: Audible -> '{sequence}'")
    if not sequence and id3_series_parts:
        sequence = sorted(id3_series_parts)[0]
        log.append(f"sequence: ID3 tag -> '{sequence}'")
    if not sequence and path_seq:
        sequence = path_seq
        log.append(f"sequence: folder name -> '{sequence}'")
    if not sequence and mj_seq:
        sequence = str(mj_seq)
        log.append(f"sequence: metadata.json -> '{sequence}'")

    # Title (trust Audible only if its sequence matches local book number)
    title = None
    audible_title_trustworthy = False
    if am and am.title:
        if fn_seq and am.sequence:
            try:
                if int(float(am.sequence)) == int(fn_seq):
                    audible_title_trustworthy = True
            except (ValueError, TypeError):
                pass
        elif not fn_seq:
            audible_title_trustworthy = True

        if audible_title_trustworthy:
            title = am.title
            log.append(f"title: Audible -> '{title}'")
        else:
            log.append(f"title: Audible '{am.title}' skipped (sequence mismatch with local book #{fn_seq})")
    if not title and id3_albums:
        raw_album = sorted(id3_albums)[0]
        title = _clean_title(raw_album) or raw_album
        log.append(f"title: ID3 album -> '{title}'")
        if len(id3_albums) > 1:
            book.conflicts.append(f"Multiple ID3 albums: {id3_albums}")
    if not title and path_title:
        cleaned = _clean_title(path_title)
        if cleaned:
            title = cleaned
            log.append(f"title: folder name -> '{title}'")
    if not title and fn_series:
        title = fn_series
        log.append(f"title: filename hint -> '{title}'")
    if not title and mj_title:
        title = mj_title
        log.append(f"title: metadata.json -> '{title}'")
    if not title:
        title = "Unknown Title"
        book.ambiguous = True
        log.append("title: UNKNOWN")

    # Narrator
    narrator = None
    if am and am.narrator:
        narrator = am.narrator
        log.append(f"narrator: Audible -> '{narrator}'")
    if not narrator and id3_composers:
        narrator = sorted(id3_composers)[0]
        log.append(f"narrator: ID3 composer -> '{narrator}'")
    if not narrator and path_narrator:
        narrator = path_narrator
        log.append(f"narrator: folder brace -> '{narrator}'")
    if not narrator and mj_narrator:
        narrator = mj_narrator
        log.append(f"narrator: metadata.json -> '{narrator}'")

    # Year
    year = None
    if am and am.year:
        year = am.year
    elif mj_year:
        year = mj_year

    book.author = author
    book.title = title
    book.series = series
    book.sequence = sequence
    book.narrator = narrator
    book.year = year
    book.resolution_log = log

    if verbose:
        ui.verbose(f"Book [{book.book_id}] in {book.source_dir}:")
        ui.verbose(f"  Files: {[f.filename for f in book.files]}")
        if am:
            ui.verbose(f"  Audible match: '{am.title}' ASIN={am.asin} confidence={am.confidence:.2f}")
        else:
            ui.verbose("  Audible match: NONE")
        for entry in log:
            ui.verbose(f"  {entry}")
        if book.conflicts:
            for c in book.conflicts:
                ui.verbose(f"  CONFLICT: {c}")
        ui.verbose(f"  => author={author}, title={title}, series={series}, seq={sequence}, narrator={narrator}")
