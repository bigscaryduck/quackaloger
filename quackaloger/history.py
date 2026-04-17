"""Run journaling, undo logic, and conflict detection."""

import json
import os
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Optional

from quackaloger.models import ActionRecord, RunRecord


def _history_dir(tool_dir: str) -> str:
    d = os.path.join(tool_dir, "history")
    os.makedirs(d, exist_ok=True)
    return d


def _run_path(tool_dir: str, run_id: str) -> str:
    return os.path.join(_history_dir(tool_dir), f"{run_id}.json")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_run(tool_dir: str, library_root: str, config_snapshot: dict = None) -> RunRecord:
    """Create and persist a new RunRecord."""
    now = datetime.now(timezone.utc)
    run_id = now.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    run = RunRecord(
        run_id=run_id,
        started_at=now.isoformat(),
        status="in_progress",
        library_root=library_root,
        config_snapshot=config_snapshot or {},
    )
    _save_run(tool_dir, run)
    return run


def record_action(run: RunRecord, action: ActionRecord):
    """Append an action to the RunRecord (in-memory). Call finish_run to persist."""
    if not action.timestamp:
        action.timestamp = datetime.now(timezone.utc).isoformat()
    run.actions.append(action)


def finish_run(tool_dir: str, run: RunRecord, status: str = "completed", summary: dict = None):
    """Finalize the run and write it to disk."""
    run.finished_at = datetime.now(timezone.utc).isoformat()
    run.status = status
    if summary:
        run.summary = summary
    _save_run(tool_dir, run)


def list_runs(tool_dir: str) -> list:
    """List all historical runs, newest first. Returns list[RunRecord] (actions not loaded)."""
    hdir = _history_dir(tool_dir)
    runs = []
    for fname in os.listdir(hdir):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(hdir, fname), "r", encoding="utf-8") as f:
                data = json.load(f)
            run = RunRecord(
                run_id=data.get("run_id", fname.replace(".json", "")),
                started_at=data.get("started_at", ""),
                finished_at=data.get("finished_at", ""),
                status=data.get("status", "unknown"),
                library_root=data.get("library_root", ""),
                config_snapshot=data.get("config_snapshot", {}),
                summary=data.get("summary", {}),
            )
            runs.append(run)
        except Exception:
            continue
    runs.sort(key=lambda r: r.started_at, reverse=True)
    return runs


def load_run(tool_dir: str, run_id: str) -> Optional[RunRecord]:
    """Load a specific run with its full action journal."""
    path = _run_path(tool_dir, run_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        actions = []
        for a in data.get("actions", []):
            actions.append(ActionRecord(
                action_type=a.get("action_type", a.get("type", "")),
                source=a.get("source", ""),
                dest=a.get("dest", ""),
                filepath=a.get("filepath", ""),
                marker_data=a.get("marker_data", {}),
                timestamp=a.get("timestamp", ""),
            ))
        return RunRecord(
            run_id=data.get("run_id", run_id),
            started_at=data.get("started_at", ""),
            finished_at=data.get("finished_at", ""),
            status=data.get("status", "unknown"),
            library_root=data.get("library_root", ""),
            config_snapshot=data.get("config_snapshot", {}),
            summary=data.get("summary", {}),
            actions=actions,
        )
    except Exception:
        return None


def undo_run(tool_dir: str, run_id: str, force: bool = False) -> dict:
    """Reverse all actions from a run in LIFO order.

    Returns {"reverted": int, "skipped": int, "errors": list[str]}.
    """
    import shutil
    from quackaloger import metadata as meta_mod

    run = load_run(tool_dir, run_id)
    if not run:
        return {"reverted": 0, "skipped": 0, "errors": [f"Run {run_id} not found"]}

    reverted = 0
    skipped = 0
    errors = []

    # Process in reverse order (LIFO)
    for action in reversed(run.actions):
        try:
            if action.action_type == "move":
                if os.path.exists(action.dest):
                    os.makedirs(os.path.dirname(action.source), exist_ok=True)
                    shutil.move(action.dest, action.source)
                    reverted += 1
                elif force:
                    skipped += 1
                    errors.append(f"File not at dest, skipped: {action.dest}")
                else:
                    skipped += 1
                    errors.append(f"File not at expected dest (moved by later run?): {action.dest}")

            elif action.action_type == "trash":
                if os.path.exists(action.dest):
                    os.makedirs(os.path.dirname(action.source), exist_ok=True)
                    shutil.move(action.dest, action.source)
                    reverted += 1
                elif force:
                    skipped += 1
                else:
                    skipped += 1
                    errors.append(f"Trashed file not found: {action.dest}")

            elif action.action_type == "embed_marker":
                fp = action.filepath or action.dest
                if fp and os.path.exists(fp):
                    meta_mod.clear_marker(fp)
                    reverted += 1

        except Exception as e:
            errors.append(f"Error reverting {action.action_type} ({action.source}): {e}")

    # Clean up empty directories created during undo
    if run.library_root:
        for dirpath, dirnames, filenames in os.walk(run.library_root, topdown=False):
            if dirpath == run.library_root:
                continue
            try:
                if not os.listdir(dirpath):
                    os.rmdir(dirpath)
            except OSError:
                pass

    return {"reverted": reverted, "skipped": skipped, "errors": errors}


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _save_run(tool_dir: str, run: RunRecord):
    path = _run_path(tool_dir, run.run_id)
    data = {
        "run_id": run.run_id,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "status": run.status,
        "library_root": run.library_root,
        "config_snapshot": run.config_snapshot,
        "summary": run.summary,
        "actions": [],
    }
    for a in run.actions:
        data["actions"].append({
            "action_type": a.action_type,
            "source": a.source,
            "dest": a.dest,
            "filepath": a.filepath,
            "marker_data": a.marker_data,
            "timestamp": a.timestamp,
        })
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
