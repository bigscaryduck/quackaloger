"""File operations: move, soft-delete to trash, mkdir, empty-dir cleanup.

Every operation is journaled via history.record_action().
"""

import os
import shutil
from datetime import datetime, timezone

from quackaloger import metadata as meta_mod
from quackaloger.config import Config
from quackaloger.constants import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS
from quackaloger.history import record_action
from quackaloger.models import ActionRecord, MoveAction, PlanReport, RunRecord
from quackaloger.ui import ui


def _estimate_time(count: int, secs_per_item: float) -> str:
    """Human-readable time estimate for *count* items at *secs_per_item* each."""
    total = count * secs_per_item
    if total < 60:
        return f"~{int(total)}s"
    minutes = total / 60
    if minutes < 2:
        return "~1 min"
    return f"~{int(minutes)} min"


def execute_plan(
    report: PlanReport,
    cfg: Config,
    run: RunRecord,
    verbose: bool = False,
) -> dict:
    """Execute all planned moves, trash sidecars, embed markers, clean up empties.

    Returns a summary dict with counts.
    """
    library_root = cfg.library_root
    tool_dir = cfg.tool_dir
    trash_base = os.path.join(tool_dir, cfg.trash_dir)
    review_base = os.path.join(tool_dir, cfg.review_dir)

    moved = 0
    trashed = 0
    markers_embedded = 0
    errors = []

    # --- Moves ---
    ui.info(f"Moving {len(report.moves)} files (est. {_estimate_time(len(report.moves), 1.5)}, "
            f"varies with file size and network speed)...")

    moved_sources: dict[str, str] = {}

    with ui.progress(len(report.moves), desc="Moving files") as progress:
        task = progress.add_task("Moving files", total=len(report.moves))
        for action in report.moves:
            dest_dir = os.path.dirname(action.dest)
            try:
                os.makedirs(dest_dir, exist_ok=True)
            except OSError as e:
                errors.append(f"Cannot create directory {dest_dir}: {e}")
                progress.advance(task)
                continue

            if os.path.exists(action.dest):
                if os.path.normpath(action.source).lower() == os.path.normpath(action.dest).lower():
                    progress.advance(task)
                    continue
                ui.warn(f"Destination already exists: {action.dest}")
                progress.advance(task)
                continue

            src_norm = os.path.normpath(action.source).lower()

            if os.path.exists(action.source):
                try:
                    shutil.move(action.source, action.dest)
                    moved_sources[src_norm] = action.dest
                    record_action(run, ActionRecord(
                        action_type="move",
                        source=action.source,
                        dest=action.dest,
                    ))
                    moved += 1
                    if verbose:
                        ui.verbose(f"MOVED {action.source} -> {action.dest}")
                except Exception as e:
                    errors.append(f"Failed to move {action.source}: {e}")
            elif src_norm in moved_sources:
                try:
                    shutil.copy2(moved_sources[src_norm], action.dest)
                    record_action(run, ActionRecord(
                        action_type="move",
                        source=action.source,
                        dest=action.dest,
                    ))
                    moved += 1
                    if verbose:
                        ui.verbose(f"COPIED {moved_sources[src_norm]} -> {action.dest}")
                except Exception as e:
                    errors.append(f"Failed to copy {action.source}: {e}")
            else:
                errors.append(f"Source not found: {action.source}")

            progress.advance(task)

    ui.success(f"Moved {moved} files")

    # --- Soft-delete sidecars to trash ---
    if report.stale_metadata:
        ui.info(f"Trashing {len(report.stale_metadata)} sidecar files...")
        with ui.progress(len(report.stale_metadata), desc="Trashing sidecars") as progress:
            task = progress.add_task("Trashing sidecars", total=len(report.stale_metadata))
            for fpath in report.stale_metadata:
                if not os.path.exists(fpath):
                    progress.advance(task)
                    continue
                try:
                    rel = os.path.relpath(fpath, library_root)
                except ValueError:
                    rel = os.path.basename(fpath)
                trash_dest = os.path.join(trash_base, rel)
                try:
                    os.makedirs(os.path.dirname(trash_dest), exist_ok=True)
                    shutil.move(fpath, trash_dest)
                    record_action(run, ActionRecord(
                        action_type="trash",
                        source=fpath,
                        dest=trash_dest,
                    ))
                    trashed += 1
                    if verbose:
                        ui.verbose(f"TRASH {fpath} -> {trash_dest}")
                except Exception as e:
                    errors.append(f"Cannot trash {fpath}: {e}")
                progress.advance(task)

    ui.info(f"Moved to trash: {trashed} sidecar files")

    # --- Trash orphaned files in depleted source directories ---
    orphaned_trashed = 0
    source_dirs = {os.path.normpath(os.path.dirname(a.source)) for a in report.moves}
    orphan_files: list[tuple[str, str]] = []
    for src_dir in sorted(source_dirs):
        if not os.path.isdir(src_dir):
            continue
        norm_src = os.path.normpath(src_dir).lower()
        norm_tool = os.path.normpath(tool_dir).lower()
        if norm_src.startswith(norm_tool):
            continue

        remaining = os.listdir(src_dir)
        if not remaining:
            continue
        media_exts = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS
        has_media = any(os.path.splitext(f)[1].lower() in media_exts for f in remaining)
        if has_media:
            continue

        for fname in remaining:
            fpath = os.path.join(src_dir, fname)
            if not os.path.isfile(fpath):
                continue
            try:
                rel = os.path.relpath(fpath, library_root)
            except ValueError:
                rel = fname
            orphan_files.append((fpath, os.path.join(trash_base, rel)))

    if orphan_files:
        ui.info(f"Trashing {len(orphan_files)} orphaned files from depleted directories...")
        with ui.progress(len(orphan_files), desc="Trashing orphans") as progress:
            task = progress.add_task("Trashing orphans", total=len(orphan_files))
            for fpath, trash_dest in orphan_files:
                try:
                    os.makedirs(os.path.dirname(trash_dest), exist_ok=True)
                    shutil.move(fpath, trash_dest)
                    record_action(run, ActionRecord(
                        action_type="trash",
                        source=fpath,
                        dest=trash_dest,
                    ))
                    orphaned_trashed += 1
                    if verbose:
                        ui.verbose(f"TRASH orphan {fpath} -> {trash_dest}")
                except Exception as e:
                    errors.append(f"Cannot trash orphan {fpath}: {e}")
                progress.advance(task)
        ui.info(f"Trashed {orphaned_trashed} orphaned files")

    # --- Quarantine low-confidence / unidentified books ---
    quarantine_files = [(book, f) for book in report.quarantine for f in book.files]
    quarantined = 0
    if quarantine_files:
        ui.info(f"Quarantining {len(quarantine_files)} files to {cfg.review_dir}/...")
        with ui.progress(len(quarantine_files), desc="Quarantining") as progress:
            task = progress.add_task("Quarantining", total=len(quarantine_files))
            for book, f in quarantine_files:
                try:
                    rel = os.path.relpath(f.filepath, library_root)
                except ValueError:
                    rel = f.filename
                review_dest = os.path.join(review_base, rel)
                try:
                    os.makedirs(os.path.dirname(review_dest), exist_ok=True)
                    shutil.move(f.filepath, review_dest)
                    record_action(run, ActionRecord(
                        action_type="move",
                        source=f.filepath,
                        dest=review_dest,
                    ))
                    quarantined += 1
                except Exception as e:
                    errors.append(f"Cannot quarantine {f.filepath}: {e}")
                progress.advance(task)
        ui.info(f"Quarantined: {quarantined} files to {cfg.review_dir}/")

    # --- Embed processing markers ---
    if cfg.embed_markers:
        processed_files = [
            action.dest for action in report.moves
            if action.file_type in ("audio",) and os.path.exists(action.dest)
        ]

        if processed_files:
            ui.info(f"Embedding markers in {len(processed_files)} audio files "
                    f"(est. {_estimate_time(len(processed_files), 2.0)}, "
                    f"varies with file size and network speed)...")
            with ui.progress(len(processed_files), desc="Embedding markers") as progress:
                task = progress.add_task("Embedding markers", total=len(processed_files))
                for fpath in processed_files:
                    try:
                        rel = os.path.relpath(fpath, library_root)
                    except ValueError:
                        rel = fpath
                    marker_data = {
                        "tool": "quackaloger",
                        "version": "1.0.0",
                        "processed_at": datetime.now(timezone.utc).isoformat(),
                        "run_id": run.run_id,
                        "original_path": rel,
                    }
                    if meta_mod.write_marker(fpath, marker_data):
                        record_action(run, ActionRecord(
                            action_type="embed_marker",
                            filepath=fpath,
                            marker_data=marker_data,
                        ))
                        markers_embedded += 1
                    elif verbose:
                        ui.warn(f"Could not embed marker in {fpath}")
                    progress.advance(task)

            ui.info(f"Embedded markers: {markers_embedded} files")

    # --- Clean empty directories ---
    ui.info("Scanning for empty directories (this may take a minute on large libraries)...")
    removed_dirs = 0
    with ui.spinner("Cleaning empty directories..."):
        for dirpath, dirnames, filenames in os.walk(library_root, topdown=False):
            if dirpath == library_root:
                continue
            if os.path.abspath(dirpath).startswith(os.path.abspath(tool_dir)):
                continue
            try:
                if not os.listdir(dirpath):
                    os.rmdir(dirpath)
                    removed_dirs += 1
                    if verbose:
                        ui.verbose(f"RMDIR {dirpath}")
            except OSError:
                pass
    ui.info(f"Removed {removed_dirs} empty directories")

    if errors:
        for e in errors:
            ui.error(e)
        ui.flavor("error_generic")

    return {
        "files_moved": moved,
        "files_trashed": trashed,
        "orphans_trashed": orphaned_trashed,
        "markers_embedded": markers_embedded,
        "books_quarantined": quarantined,
        "empty_dirs_removed": removed_dirs,
        "errors": len(errors),
    }
