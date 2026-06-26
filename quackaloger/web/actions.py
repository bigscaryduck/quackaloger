"""Convenience job submitters binding the service layer to the job queue.

Shared by the HTTP routes (manual triggers) and the folder watcher (automatic
triggers) so both paths run identically through the single worker.
"""

from __future__ import annotations

from typing import Optional

from quackaloger import service
from quackaloger.web.jobs import Job, manager


def submit_scan(library: dict) -> Job:
    """Dry-run scan of a library; stores plan_id + summary on the job."""
    label = f"Scan: {library.get('name') or library['path']}"

    def _fn(job: Job) -> None:
        bundle = service.scan(
            library["path"], library["domain"], overrides=library.get("overrides")
        )
        job.plan_id = bundle.plan_id
        job.result = service.summarize_bundle(bundle)

    return manager.submit("scan", library["id"], label, _fn)


def submit_execute(
    library_id: str,
    plan_id: str,
    *,
    selected_indexes: Optional[list] = None,
    include_quarantine: Optional[bool] = None,
) -> Job:
    """Commit (a subset of) a previously scanned plan."""
    label = f"Organize plan {plan_id}"

    def _fn(job: Job) -> None:
        job.plan_id = plan_id
        job.result = service.execute(
            plan_id,
            selected_indexes=selected_indexes,
            include_quarantine=include_quarantine,
        )

    return manager.submit("execute", library_id, label, _fn)


def submit_auto(library: dict) -> Job:
    """Watch-triggered: scan, then (in auto-organize mode) commit the full plan."""
    mode = (library.get("watch") or {}).get("mode", "scan-only")
    label = f"Auto ({mode}): {library.get('name') or library['path']}"

    def _fn(job: Job) -> None:
        bundle = service.scan(
            library["path"], library["domain"], overrides=library.get("overrides")
        )
        job.plan_id = bundle.plan_id
        scan_summary = service.summarize_bundle(bundle)
        job.result = {"scan": scan_summary}
        if mode == "auto-organize" and bundle.report.moves:
            job.result["execute"] = service.execute(
                bundle.plan_id, selected_indexes=None, include_quarantine=True
            )

    return manager.submit("auto", library["id"], label, _fn)
