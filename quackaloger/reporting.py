"""Report generation: summary (failures only) and verbose (full detail).

Each run produces two files in the logs/ directory.
"""

import os
from collections import defaultdict
from datetime import datetime

from quackaloger.constants import VIDEO_EXTENSIONS
from quackaloger.models import Book, MoveAction, PlanReport
from quackaloger.ui import ui


# ---------------------------------------------------------------------------
# Plan builder (Phase 6)
# ---------------------------------------------------------------------------

def build_plan(
    books: list,
    folders: dict,
    library_root: str,
    unidentified_action: str = "quarantine",
    confidence_threshold: float = 0.75,
    verbose: bool = False,
) -> PlanReport:
    """Analyze resolved books and build a PlanReport of actions to take."""

    report = PlanReport()
    target_to_books = defaultdict(list)

    audible_matched = sum(1 for b in books if b.audible_match)
    report.audible_stats = {
        "matched": audible_matched,
        "unmatched": len(books) - audible_matched,
    }

    for book in books:
        if book.target_dir:
            target_to_books[book.target_dir.lower()].append(book)

    for target, dupe_books in target_to_books.items():
        if len(dupe_books) > 1:
            report.duplicates.append({
                "target": dupe_books[0].target_dir,
                "sources": [b.source_dir for b in dupe_books],
                "books": dupe_books,
            })

    for book in books:
        # Quarantine unidentified / low-confidence books
        if book.ambiguous and unidentified_action == "quarantine":
            report.quarantine.append(book)
            continue

        if book.conflicts:
            report.conflicts.extend(
                {"book_id": book.book_id, "source_dir": book.source_dir, "conflict": c}
                for c in book.conflicts
            )
        if not book.target_dir:
            report.quarantine.append(book)
            continue

        source_norm = os.path.normpath(book.source_dir).lower()
        target_norm = os.path.normpath(book.target_dir).lower()

        if source_norm == target_norm:
            report.already_correct.append(book)
            continue

        for f in book.files:
            dest_name = getattr(f, "target_filename", None) or f.filename
            dest = os.path.join(book.target_dir, dest_name)
            ftype = "video" if f.extension.lower() in VIDEO_EXTENSIONS else "audio"
            report.moves.append(MoveAction(source=f.filepath, dest=dest, file_type=ftype))

        for img_path in book.cover_files:
            dest = os.path.join(book.target_dir, os.path.basename(img_path))
            report.moves.append(MoveAction(source=img_path, dest=dest, file_type="cover"))

    for dirpath, contents in folders.items():
        if contents.get("metadata_json"):
            report.stale_metadata.append(contents["metadata_json"])
        for sc in contents.get("sidecars", []):
            report.stale_metadata.append(sc)

    for dirpath, contents in folders.items():
        if not contents["audio"] and not contents["images"]:
            report.skipped_folders.append(os.path.relpath(dirpath, library_root))

    return report


# ---------------------------------------------------------------------------
# Summary report (failures / anomalies only)
# ---------------------------------------------------------------------------

def build_summary_report(report: PlanReport, library_root: str) -> str:
    """Build a concise report highlighting only failures and items needing attention."""
    lines = []

    def out(line=""):
        lines.append(line)

    out("=" * 80)
    out("  AUDIOBOOK ORGANIZER - SUMMARY REPORT")
    out(f"  Library: {library_root}")
    out(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    out("=" * 80)

    out(f"\n  Quick stats:")
    out(f"    Files to move:        {len(report.moves)}")
    out(f"    Already correct:      {len(report.already_correct)}")
    out(f"    Catalog matched:      {report.audible_stats.get('matched', 0)}")
    out(f"    Catalog unmatched:    {report.audible_stats.get('unmatched', 0)}")
    out(f"    Conflicts found:      {len(report.conflicts)}")
    out(f"    Quarantined:          {len(report.quarantine)}")
    out(f"    Duplicate targets:    {len(report.duplicates)}")

    if report.quarantine:
        out(f"\n{'─' * 80}")
        out("  QUARANTINED (unidentified / low-confidence)")
        out(f"{'─' * 80}")
        for book in report.quarantine:
            rel = os.path.relpath(book.source_dir, library_root)
            out(f"  [{book.book_id}] {rel}")
            out(f"    author={book.author}, title={book.title}, series={book.series}")

    if report.conflicts:
        out(f"\n{'─' * 80}")
        out("  CONFLICTS (metadata sources disagree)")
        out(f"{'─' * 80}")
        for c in report.conflicts:
            out(f"  [{c['book_id']}] {c['source_dir']}")
            out(f"    {c['conflict']}")

    if report.duplicates:
        out(f"\n{'─' * 80}")
        out("  DUPLICATE TARGETS (multiple sources -> same destination)")
        out(f"{'─' * 80}")
        for dup in report.duplicates:
            out(f"  Target: {os.path.relpath(dup['target'], library_root)}")
            for src in dup["sources"]:
                out(f"    Source: {os.path.relpath(src, library_root)}")

    out(f"\n{'=' * 80}")
    if report.moves:
        out("  To execute these changes, re-run with --execute")
    else:
        out("  No changes needed!")
    out(f"{'=' * 80}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Verbose report (full detail)
# ---------------------------------------------------------------------------

def build_verbose_report(report: PlanReport, books: list, library_root: str) -> str:
    """Build a comprehensive report of every decision and planned action."""
    lines = []

    def out(line=""):
        lines.append(line)

    out("=" * 80)
    out("  AUDIOBOOK ORGANIZER - VERBOSE REPORT")
    out(f"  Library: {library_root}")
    out(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    out("=" * 80)

    out(f"\n  Summary:")
    out(f"    Total books:          {len(books)}")
    out(f"    Files to move:        {len(report.moves)}")
    out(f"    Already correct:      {len(report.already_correct)}")
    out(f"    Catalog matched:      {report.audible_stats.get('matched', 0)}")
    out(f"    Catalog unmatched:    {report.audible_stats.get('unmatched', 0)}")
    out(f"    Conflicts found:      {len(report.conflicts)}")
    out(f"    Quarantined:          {len(report.quarantine)}")
    out(f"    Duplicate targets:    {len(report.duplicates)}")
    out(f"    Stale sidecar files:  {len(report.stale_metadata)}")
    out(f"    Skipped (empty) dirs: {len(report.skipped_folders)}")

    # Per-book resolution detail
    out(f"\n{'─' * 80}")
    out("  BOOK-BY-BOOK RESOLUTION DETAIL")
    out(f"{'─' * 80}")
    for book in books:
        rel = os.path.relpath(book.source_dir, library_root)
        out(f"\n  [{book.book_id}] {rel}")
        dt = getattr(book, "domain_tag", "") or ""
        if dt:
            out(f"    Domain: {dt}")
        out(f"    Files: {[f.filename for f in book.files]}")
        if book.audible_match:
            am = book.audible_match
            out(f"    Audible: '{am.title}' by {am.author} ASIN={am.asin} confidence={am.confidence:.2f}")
        else:
            out(f"    Audible: NONE")
        for entry in book.resolution_log:
            out(f"    {entry}")
        if book.conflicts:
            for c in book.conflicts:
                out(f"    CONFLICT: {c}")
        out(f"    => author={book.author}, title={book.title}, series={book.series}, "
            f"seq={book.sequence}, narrator={book.narrator}")
        if book.target_dir:
            out(f"    Target: {os.path.relpath(book.target_dir, library_root)}")

    # Planned moves
    if report.moves:
        out(f"\n{'─' * 80}")
        out("  PLANNED MOVES")
        out(f"{'─' * 80}")
        for action in report.moves:
            rel_src = os.path.relpath(action.source, library_root)
            rel_dst = os.path.relpath(action.dest, library_root)
            out(f"  [{action.file_type.upper():5s}] {rel_src}")
            out(f"      -> {rel_dst}")

    if report.already_correct:
        out(f"\n{'─' * 80}")
        out("  ALREADY CORRECT (no changes needed)")
        out(f"{'─' * 80}")
        for book in report.already_correct:
            rel = os.path.relpath(book.source_dir, library_root)
            out(f"  {rel}  ({len(book.files)} files)")

    if report.stale_metadata:
        out(f"\n{'─' * 80}")
        out("  STALE SIDECAR FILES (will be trashed on --execute)")
        out(f"{'─' * 80}")
        for f in report.stale_metadata:
            out(f"  {os.path.relpath(f, library_root)}")

    out(f"\n{'=' * 80}")
    if report.moves:
        out("  To execute these changes, re-run with --execute")
    else:
        out("  No changes needed!")
    out(f"{'=' * 80}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# File writers
# ---------------------------------------------------------------------------

def write_reports(
    report: PlanReport,
    books: list,
    library_root: str,
    tool_dir: str,
    run_id: str,
) -> tuple:
    """Write summary and verbose reports to the logs/ directory.

    Returns (summary_path, verbose_path).
    """
    logs_dir = os.path.join(tool_dir, "logs")
    os.makedirs(logs_dir, exist_ok=True)

    summary_text = build_summary_report(report, library_root)
    verbose_text = build_verbose_report(report, books, library_root)

    summary_path = os.path.join(logs_dir, f"{run_id}_summary.txt")
    verbose_path = os.path.join(logs_dir, f"{run_id}_verbose.txt")

    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary_text)
    with open(verbose_path, "w", encoding="utf-8") as f:
        f.write(verbose_text)

    return summary_path, verbose_path
