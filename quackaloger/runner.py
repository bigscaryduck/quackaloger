"""Multi-domain organize orchestration: one run journal, merged plan, shared execute."""

from __future__ import annotations

import os
from typing import Optional

from quackaloger.config import Config
from quackaloger.domains import get_domain
from quackaloger.domains.base import OrganizeContext, OrganizeResult
from quackaloger.fileops import execute_plan
from quackaloger.history import finish_run, start_run
from quackaloger.llm import build_extract_client
from quackaloger.models import PlanReport
from quackaloger.reporting import write_reports
from quackaloger.ui import ui


def merge_plan_reports(a: PlanReport, b: PlanReport) -> PlanReport:
    """Append all plan sections (per-run undo relies on a single merged journal)."""
    out = PlanReport()
    out.moves.extend(a.moves)
    out.moves.extend(b.moves)
    out.already_correct.extend(a.already_correct)
    out.already_correct.extend(b.already_correct)
    out.conflicts.extend(a.conflicts)
    out.conflicts.extend(b.conflicts)
    out.ambiguous.extend(a.ambiguous)
    out.ambiguous.extend(b.ambiguous)
    out.duplicates.extend(a.duplicates)
    out.duplicates.extend(b.duplicates)
    out.skipped_folders.extend(a.skipped_folders)
    out.skipped_folders.extend(b.skipped_folders)
    out.stale_metadata.extend(a.stale_metadata)
    out.stale_metadata.extend(b.stale_metadata)
    out.quarantine.extend(a.quarantine)
    out.quarantine.extend(b.quarantine)
    out.to_review.extend(a.to_review)
    out.to_review.extend(b.to_review)
    out.audible_stats = {
        "matched": int(a.audible_stats.get("matched", 0)) + int(b.audible_stats.get("matched", 0)),
        "unmatched": int(a.audible_stats.get("unmatched", 0)) + int(b.audible_stats.get("unmatched", 0)),
    }
    return out


def run_organize_domains(cfg: Config, *, extract_client) -> tuple[PlanReport, list]:
    """Execute each configured domain in order; return merged plan and all books for logging."""
    import quackaloger.domains.audiobooks  # noqa: F401
    import quackaloger.domains.plex_movies  # noqa: F401
    import quackaloger.domains.plex_tv  # noqa: F401
    import quackaloger.domains.stub_print  # noqa: F401

    ctx = OrganizeContext(cfg=cfg, extract_client=extract_client, verbose=cfg.verbosity in ("verbose", "debug"))
    merged = PlanReport()
    all_books: list = []
    domain_ids = list(cfg.organize_domains) or ["audiobooks"]

    # Source files already claimed by an earlier domain. Guards against two
    # domains matching the same file (e.g. plex_movies vs plex_tv on a video).
    claimed: set[str] = set()

    def _norm(path: str) -> str:
        return os.path.normcase(os.path.normpath(path))

    for did in domain_ids:
        dom = get_domain(did)
        dom.validate_config(cfg)
        res: OrganizeResult = dom.run(ctx)

        kept_books = []
        for b in res.books:
            srcs = [_norm(f.filepath) for f in b.files]
            if srcs and all(s in claimed for s in srcs):
                continue  # every file already owned by an earlier domain
            kept_books.append(b)
        res.report.moves = [m for m in res.report.moves if _norm(m.source) not in claimed]

        for b in kept_books:
            for f in b.files:
                claimed.add(_norm(f.filepath))
        for m in res.report.moves:
            claimed.add(_norm(m.source))

        all_books.extend(kept_books)
        merged = merge_plan_reports(merged, res.report)

    # After every domain has claimed what it can, sweep the remaining non-empty,
    # unidentified content into needs-review (computed on the merged plan).
    from quackaloger.reporting import collect_review_leftovers
    collect_review_leftovers(merged, cfg.library_root, cfg.tool_dir)

    return merged, all_books


def organize_library_flow(
    cfg: Config,
    *,
    extract_client,
    verbose: bool,
) -> None:
    """Phases after config: merged domain runs, reports, optional execute."""
    from quackaloger import __version__

    merged, all_books = run_organize_domains(cfg, extract_client=extract_client)

    config_snapshot = {
        "confidence_threshold": cfg.confidence_threshold,
        "dry_run": cfg.dry_run,
        "no_audible": cfg.no_audible,
        "no_ai": cfg.no_ai,
        "organize_domains": list(cfg.organize_domains),
        "llm_provider": cfg.llm_provider,
    }
    run = start_run(cfg.tool_dir, cfg.library_root, config_snapshot)

    ui.rule("Results")
    if verbose:
        ui.report_verbose(merged, all_books, cfg.library_root)
    else:
        ui.report_summary(merged, cfg.library_root)

    summary_path, verbose_path = write_reports(
        merged, all_books, cfg.library_root, cfg.tool_dir, run.run_id,
    )
    ui.info("Reports saved:")
    ui.muted(f"  Summary: {summary_path}")
    ui.muted(f"  Verbose: {verbose_path}")

    if not cfg.dry_run:
        if not merged.moves and not merged.quarantine:
            ui.info("No moves to execute.")
            finish_run(
                cfg.tool_dir, run, status="completed",
                summary={"books_processed": len(all_books), "files_moved": 0},
            )
            return

        total_ops = len(merged.moves) + len(merged.quarantine)
        ui.info(f"About to process {total_ops} file operations.")
        if not ui.prompt_confirm("Continue?", default=False):
            ui.warn("Aborted.")
            finish_run(cfg.tool_dir, run, status="aborted")
            return

        ui.phase(7, "Executing file operations")
        summary = execute_plan(merged, cfg, run, verbose=verbose)
        summary["books_processed"] = len(all_books)
        finish_run(cfg.tool_dir, run, status="completed", summary=summary)
        ui.success("Done! Rescan your library in your media server to pick up the changes.")
    else:
        finish_run(
            cfg.tool_dir, run, status="dry_run",
            summary={
                "books_processed": len(all_books),
                "files_would_move": len(merged.moves),
                "books_quarantined": len(merged.quarantine),
                "would_review": len(merged.to_review),
            },
        )
        ui.info("This was a dry run. No files were moved.")
        ui.flavor("dry_run_done")


def build_extract_client_for_cfg(cfg: Config):
    """Instantiate extract client from merged config (respects provider + keys)."""
    if not cfg.enable_ai or cfg.no_ai:
        return None
    prov = (cfg.llm_provider or "openai").lower()
    if prov == "anthropic":
        return build_extract_client(
            "anthropic",
            openai_key="",
            openai_model="",
            anthropic_key=cfg.anthropic_api_key,
            anthropic_model=cfg.anthropic_model,
        )
    return build_extract_client(
        "openai",
        openai_key=cfg.openai_api_key,
        openai_model=cfg.openai_model,
        anthropic_key="",
        anthropic_model="",
    )
