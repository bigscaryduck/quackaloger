"""Web/automation-facing service layer.

Wraps the same building blocks the CLI uses -- ``run_organize_domains`` to build
a plan and ``execute_plan`` to commit it -- but without any interactive prompts,
so an HTTP request or a folder-watch trigger can drive the whole flow.

Flow across two requests:
    1. ``scan()`` builds a dry-run plan, pickles the whole bundle to disk keyed
       by a ``plan_id``, and returns a JSON-serializable summary for review.
    2. ``execute()`` reloads that bundle and commits all (or a selected subset of)
       the planned moves, journaling the run for undo.
"""

from __future__ import annotations

import os
import pickle
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from quackaloger.config import Config, load_config
from quackaloger.constants import TOOL_DIR_NAME
from quackaloger.fileops import execute_plan
from quackaloger.history import finish_run, list_runs, load_run, start_run, undo_run
from quackaloger.models import PlanReport
from quackaloger.reporting import explain_outcome, write_reports
from quackaloger.runner import build_extract_client_for_cfg, run_organize_domains
from quackaloger.user_config import load_user_yaml, user_config_dir

PLAN_TTL_SECONDS = 24 * 60 * 60  # prune persisted plans older than a day

# Domains that cannot run without a TMDB API key (see each domain's validate_config).
DOMAINS_NEEDING_TMDB = {"plex_movies", "plex_tv"}


# ---------------------------------------------------------------------------
# Plan bundle (the unit persisted between scan and execute)
# ---------------------------------------------------------------------------

@dataclass
class PlanBundle:
    plan_id: str
    created_at: float
    library_path: str
    domain: str
    cfg: Config
    report: PlanReport
    books: list = field(default_factory=list)
    run_id: str = ""          # set when the scan was journaled as a dry-run
    summary_path: str = ""    # saved summary report, if any
    verbose_path: str = ""    # saved verbose report, if any


def _plans_dir() -> str:
    d = os.path.join(user_config_dir(), "plans")
    os.makedirs(d, exist_ok=True)
    return d


def _plan_path(plan_id: str) -> str:
    return os.path.join(_plans_dir(), f"{plan_id}.pkl")


def _new_plan_id() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


def _save_bundle(bundle: PlanBundle) -> None:
    with open(_plan_path(bundle.plan_id), "wb") as f:
        pickle.dump(bundle, f, protocol=pickle.HIGHEST_PROTOCOL)


def load_bundle(plan_id: str) -> Optional[PlanBundle]:
    path = _plan_path(plan_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


def prune_old_plans(ttl_seconds: int = PLAN_TTL_SECONDS) -> int:
    """Delete persisted plans older than *ttl_seconds*. Returns count removed."""
    removed = 0
    now = time.time()
    try:
        names = os.listdir(_plans_dir())
    except OSError:
        return 0
    for name in names:
        if not name.endswith(".pkl"):
            continue
        path = os.path.join(_plans_dir(), name)
        try:
            if now - os.path.getmtime(path) > ttl_seconds:
                os.remove(path)
                removed += 1
        except OSError:
            continue
    return removed


# ---------------------------------------------------------------------------
# Config building
# ---------------------------------------------------------------------------

def tool_dir_for(library_path: str) -> str:
    return os.path.join(os.path.abspath(library_path), TOOL_DIR_NAME)


def build_config(
    library_path: str,
    domain: str,
    *,
    overrides: Optional[dict] = None,
    dry_run: bool = True,
) -> Config:
    """Build a Config scoped to a single library + single domain.

    Per-library overrides map onto the same ``cli_args`` dict that the CLI's
    ``load_config`` understands, so precedence (CLI > env > library > user >
    defaults) is preserved.
    """
    overrides = overrides or {}
    cli_args: dict = {}
    if overrides.get("confidence") is not None:
        cli_args["confidence"] = overrides["confidence"]
    if overrides.get("no_ai"):
        cli_args["no_ai"] = True
    if overrides.get("no_audible"):
        cli_args["no_audible"] = True
    if overrides.get("llm_provider"):
        cli_args["llm_provider"] = overrides["llm_provider"]

    cfg = load_config(library_path, cli_args=cli_args)
    cfg.organize_domains = [domain]
    cfg.dry_run = dry_run

    # Pattern overrides (optional, per library)
    if overrides.get("series_pattern"):
        cfg.series_pattern = str(overrides["series_pattern"])
    if overrides.get("standalone_pattern"):
        cfg.standalone_pattern = str(overrides["standalone_pattern"])
    if overrides.get("unidentified_action"):
        cfg.unidentified_action = str(overrides["unidentified_action"])
    return cfg


# ---------------------------------------------------------------------------
# Scan (build + persist a dry-run plan)
# ---------------------------------------------------------------------------

def scan(
    library_path: str,
    domain: str,
    *,
    overrides: Optional[dict] = None,
    persist_report: bool = False,
) -> PlanBundle:
    """Run the configured domain in dry-run mode, persist and return the plan.

    When *persist_report* is True, the scan also leaves the same audit trail a
    CLI dry-run does: a dry-run entry in the library's ``history/`` and a saved
    summary + verbose report in ``logs/``. Watch-triggered scans leave it False
    so a busy folder watcher does not flood history.
    """
    prune_old_plans()
    cfg = build_config(library_path, domain, overrides=overrides, dry_run=True)
    extract_client = build_extract_client_for_cfg(cfg)
    report, books = run_organize_domains(cfg, extract_client=extract_client)

    bundle = PlanBundle(
        plan_id=_new_plan_id(),
        created_at=time.time(),
        library_path=cfg.library_root,
        domain=domain,
        cfg=cfg,
        report=report,
        books=books,
    )

    if persist_report:
        run = start_run(cfg.tool_dir, cfg.library_root, {
            "dry_run": True,
            "organize_domains": list(cfg.organize_domains),
            "llm_provider": cfg.llm_provider,
            "source": "web-scan",
        })
        bundle.run_id = run.run_id
        try:
            summary_path, verbose_path = write_reports(
                report, books, cfg.library_root, cfg.tool_dir, run.run_id,
            )
            bundle.summary_path = summary_path
            bundle.verbose_path = verbose_path
        finally:
            finish_run(cfg.tool_dir, run, status="dry_run", summary={
                "books_processed": len(books),
                "files_would_move": len(report.moves),
                "books_quarantined": len(report.quarantine),
                "would_review": len(report.to_review),
            })

    _save_bundle(bundle)
    return bundle


def tmdb_key_available(library_path: str) -> bool:
    """True if a TMDB key is resolvable for *library_path* (env, user, or library YAML).

    Read-only and side-effect free (does not create the library tool dir), so it
    is safe to call while rendering the dashboard.
    """
    if (os.environ.get("QUACK_TMDB_API_KEY") or os.environ.get("TMDB_API_KEY") or "").strip():
        return True
    keys = load_user_yaml().get("api_keys") or {}
    if str(keys.get("tmdb") or "").strip():
        return True
    cfg_path = os.path.join(os.path.abspath(library_path), TOOL_DIR_NAME, "config.yaml")
    if os.path.exists(cfg_path):
        try:
            import yaml
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            ident = data.get("identification") or {}
            if str(ident.get("tmdb_api_key") or "").strip():
                return True
        except Exception:
            pass
    return False


# ---------------------------------------------------------------------------
# Execute (commit all or a selected subset of a persisted plan)
# ---------------------------------------------------------------------------

def _filtered_report(
    report: PlanReport,
    selected_indexes: Optional[list],
    include_quarantine: bool,
) -> PlanReport:
    """Build the report execute_plan should act on.

    Full run (selected_indexes is None): every move, trash stale sidecars.
    Partial run: only the chosen move indexes; never trash sidecars belonging to
    books the user left unselected. Quarantine is opt-in via include_quarantine.
    """
    out = PlanReport()
    if selected_indexes is None:
        out.moves = list(report.moves)
        out.stale_metadata = list(report.stale_metadata)
        out.to_review = list(report.to_review)
    else:
        sel = {int(i) for i in selected_indexes}
        out.moves = [m for i, m in enumerate(report.moves) if i in sel]
        out.stale_metadata = []
        out.to_review = []  # only sweep leftovers on a full run
    out.quarantine = list(report.quarantine) if include_quarantine else []
    out.audible_stats = dict(report.audible_stats)
    out.domain_id = report.domain_id
    return out


def execute(
    plan_id: str,
    *,
    selected_indexes: Optional[list] = None,
    include_quarantine: Optional[bool] = None,
) -> dict:
    """Commit a persisted plan. Returns {"run_id", "summary"}.

    Raises KeyError if the plan_id is unknown/expired.
    """
    bundle = load_bundle(plan_id)
    if bundle is None:
        raise KeyError(f"Plan {plan_id} not found or expired")

    full = selected_indexes is None
    if include_quarantine is None:
        include_quarantine = full

    cfg = bundle.cfg
    cfg.dry_run = False
    filtered = _filtered_report(bundle.report, selected_indexes, include_quarantine)

    config_snapshot = {
        "confidence_threshold": cfg.confidence_threshold,
        "dry_run": False,
        "no_audible": cfg.no_audible,
        "no_ai": cfg.no_ai,
        "organize_domains": list(cfg.organize_domains),
        "llm_provider": cfg.llm_provider,
        "source": "web",
        "plan_id": plan_id,
    }
    run = start_run(cfg.tool_dir, cfg.library_root, config_snapshot)
    summary = execute_plan(filtered, cfg, run, verbose=False)
    summary["books_processed"] = len(bundle.books)
    finish_run(cfg.tool_dir, run, status="completed", summary=summary)
    return {"run_id": run.run_id, "summary": summary}


# ---------------------------------------------------------------------------
# JSON-serializable summaries for the API / UI
# ---------------------------------------------------------------------------

def _rel(path: str, root: str) -> str:
    try:
        return os.path.relpath(path, root)
    except ValueError:
        return path


def _confidence(book) -> Optional[float]:
    if getattr(book, "audible_match", None):
        return getattr(book.audible_match, "confidence", None)
    if getattr(book, "plex_match", None):
        return getattr(book.plex_match, "confidence", None)
    return None


def _book_summary(book, root: str) -> dict:
    return {
        "book_id": book.book_id,
        "source_rel": _rel(book.source_dir, root),
        "author": book.author,
        "title": book.title,
        "series": book.series,
        "sequence": book.sequence,
        "files": [f.filename for f in book.files],
        "confidence": _confidence(book),
    }


def summarize_bundle(bundle: PlanBundle) -> dict:
    """Flatten a PlanBundle into JSON the templates/API can render directly."""
    report = bundle.report
    root = bundle.library_path
    moves = [
        {
            "index": i,
            "file_type": m.file_type,
            "source": m.source,
            "dest": m.dest,
            "source_rel": _rel(m.source, root),
            "dest_rel": _rel(m.dest, root),
        }
        for i, m in enumerate(report.moves)
    ]
    return {
        "plan_id": bundle.plan_id,
        "created_at": bundle.created_at,
        "library_path": bundle.library_path,
        "domain": bundle.domain,
        "reason": explain_outcome(report),
        "run_id": bundle.run_id,
        "counts": {
            "moves": len(report.moves),
            "already_correct": len(report.already_correct),
            "conflicts": len(report.conflicts),
            "ambiguous": len(report.ambiguous),
            "quarantine": len(report.quarantine),
            "duplicates": len(report.duplicates),
            "stale_metadata": len(report.stale_metadata),
            "to_review": len(report.to_review),
            "matched": int(report.audible_stats.get("matched", 0)),
            "unmatched": int(report.audible_stats.get("unmatched", 0)),
        },
        "moves": moves,
        "to_review": [_rel(p, root) for p in report.to_review],
        "conflicts": report.conflicts,
        "ambiguous": [_book_summary(b, root) for b in report.ambiguous],
        "quarantine": [_book_summary(b, root) for b in report.quarantine],
        "duplicates": [
            {"target_rel": _rel(d.get("target", ""), root),
             "sources_rel": [_rel(s, root) for s in d.get("sources", [])]}
            for d in report.duplicates
        ],
    }


# ---------------------------------------------------------------------------
# History passthroughs (per library)
# ---------------------------------------------------------------------------

def runs_for(library_path: str) -> list:
    return list_runs(tool_dir_for(library_path))


def run_detail(library_path: str, run_id: str):
    return load_run(tool_dir_for(library_path), run_id)


def undo(library_path: str, run_id: str, *, force: bool = False) -> dict:
    return undo_run(tool_dir_for(library_path), run_id, force=force)
