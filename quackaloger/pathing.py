"""Target path generation and folder name sanitization."""

import os
import re

from quackaloger.constants import INVALID_PATH_CHARS
from quackaloger.models import Book
from quackaloger.ui import ui

# Leave headroom for filenames inside the directory (e.g. "chapter_01.m4b").
_MAX_DIR_PATH = 220


def sanitize_folder_name(name: str) -> str:
    """Remove invalid path characters and strip leading/trailing dots and spaces."""
    if not name:
        return name
    name = INVALID_PATH_CHARS.sub("_", name)
    name = name.strip(". ")
    return name if name else "_"


def _truncate_narrator_list(narrator: str, budget: int) -> str:
    """Shorten a comma-separated narrator list to fit within *budget* characters.

    Example: "Alice, Bob, Carol, Dave" with a tight budget becomes "Alice, Bob & 2 more".
    """
    if not narrator or len(narrator) <= budget:
        return narrator
    names = [n.strip() for n in narrator.split(",")]
    if len(names) <= 1:
        return narrator[:budget]
    result = names[0]
    included = 1
    for name in names[1:]:
        remaining = len(names) - included
        suffix = f" & {remaining} more"
        candidate = result + ", " + name
        if len(candidate) + len(suffix) > budget:
            return result + suffix
        result = candidate
        included += 1
    return result


def _build_path_str(series_pattern, standalone_pattern, author, title,
                    series, sequence, narrator):
    """Expand tokens into a relative path string."""
    if series and sequence:
        path_str = series_pattern.format(
            author=author, series=series, sequence=sequence,
            title=title, narrator=narrator,
        )
    elif series:
        path_str = series_pattern.format(
            author=author, series=series, sequence="",
            title=title, narrator=narrator,
        )
        path_str = re.sub(r"Book\s+-\s+", "", path_str)
    else:
        path_str = standalone_pattern.format(
            author=author, title=title, narrator=narrator,
        )
    path_str = re.sub(r"\s*\{\s*\}", "", path_str)
    parts = path_str.replace("\\", "/").split("/")
    return [sanitize_folder_name(p) for p in parts if p]


def generate_target_path(
    book: Book,
    library_root: str,
    series_pattern: str = "{author}/{series}/Book {sequence} - {title} {{{narrator}}}",
    standalone_pattern: str = "{author}/{title} {{{narrator}}}",
    verbose: bool = False,
):
    """Compute book.target_dir from resolved metadata and naming patterns.

    Tokens: {author}, {series}, {sequence}, {title}, {narrator}
    """
    author = sanitize_folder_name(book.author or "Unknown Author")
    title = book.title or "Unknown Title"
    series = sanitize_folder_name(book.series) if book.series else None
    sequence = book.sequence or ""
    narrator = book.narrator or ""

    parts = _build_path_str(
        series_pattern, standalone_pattern,
        author, title, series, sequence, narrator,
    )
    target = os.path.join(library_root, *parts)

    if narrator and len(target) > _MAX_DIR_PATH:
        overshoot = len(target) - _MAX_DIR_PATH
        shorter = _truncate_narrator_list(narrator, max(len(narrator) - overshoot, 20))
        parts = _build_path_str(
            series_pattern, standalone_pattern,
            author, title, series, sequence, shorter,
        )
        target = os.path.join(library_root, *parts)
        if verbose:
            ui.verbose(f"  Narrator list truncated to fit path limit: {shorter}")

    book.target_dir = target

    if verbose:
        ui.verbose(f"  Target: {book.target_dir}")
