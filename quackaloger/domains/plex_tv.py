"""Plex TV Series libraries: TMDB show + Season xx + SxxEyy episode filenames."""

from __future__ import annotations

import json
import os
import re
from difflib import SequenceMatcher

from quackaloger.config import Config, domain_confidence_threshold
from quackaloger.domains.base import OrganizeContext, OrganizeResult, register_domain
from quackaloger.llm import ExtractError
from quackaloger.models import Book, PlanReport, PlexMatch
from quackaloger.plex_discovery import scan_tv_episode_books
from quackaloger.plex_format import sanitize_path_component
from quackaloger.reporting import build_plan
from quackaloger.tmdb import TmdbClient, tv_candidate
from quackaloger.ui import ui

TMDB_TV_PICK_SCHEMA = {
    "type": "object",
    "properties": {
        "tmdb_id": {"type": "integer", "description": "Chosen TMDB TV show id, or -1 if none"},
        "reason": {"type": "string"},
    },
    "required": ["tmdb_id", "reason"],
}


def _fuzzy(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


class PlexTvDomain:
    id = "plex_tv"

    def validate_config(self, cfg: Config) -> None:
        if not (cfg.tmdb_api_key or "").strip():
            raise ValueError(
                "plex_tv requires a TMDB API key (identification.tmdb_api_key, "
                "api_keys.tmdb in user config, or QUACK_TMDB_API_KEY / TMDB_API_KEY)."
            )

    def run(self, ctx: OrganizeContext) -> OrganizeResult:
        cfg = ctx.cfg
        verbose = ctx.verbose
        thresh = domain_confidence_threshold(cfg, self.id)
        tmdb = TmdbClient(cfg.tmdb_api_key.strip(), cfg.tool_dir)

        books = scan_tv_episode_books(cfg.library_root, verbose=verbose)
        if not books:
            ui.info("No TV episode files recognized (looked for SxxEyy markers in file/folder names).")
            rep = PlanReport()
            rep.domain_id = self.id
            return OrganizeResult(domain_id=self.id, report=rep, books=[])

        # Group episodes by show so each distinct show is resolved on TMDB once.
        shows: dict[str, list] = {}
        for book in books:
            shows.setdefault((book.series or "Unknown").strip().lower(), []).append(book)

        resolved: dict[str, tuple[int, float, str]] = {}
        for key, group in shows.items():
            parsed_title = group[0].series or "Unknown"
            tmdb_id, conf, canonical = self._resolve_show(
                parsed_title, cfg, tmdb, ctx.extract_client, verbose
            )
            # Prefer TMDB's canonical name for the folder; fall back to what we parsed.
            resolved[key] = (tmdb_id, conf, canonical or parsed_title)
            if verbose:
                ui.verbose(
                    f"[plex_tv] {parsed_title!r} -> {canonical!r} tmdb_id={tmdb_id} "
                    f"conf={conf:.2f} ({len(group)} episodes)"
                )

        ui.phase(3, f"TMDB identification for {len(books)} TV episodes across {len(shows)} shows")
        with ui.progress(len(books), desc="TMDB (tv)") as progress:
            task = progress.add_task("TMDB (tv)", total=len(books))
            for book in books:
                tmdb_id, conf, show_title = resolved[(book.series or "Unknown").strip().lower()]
                self._episode_path(book, cfg, tmdb_id, show_title, conf, thresh)
                progress.advance(task)

        folders: dict = {}
        ui.phase(6, "Building move plan (plex_tv)")
        with ui.spinner("Building plan..."):
            report = build_plan(
                books, folders, cfg.library_root,
                unidentified_action=cfg.unidentified_action,
                confidence_threshold=thresh,
                verbose=verbose,
            )
        matched = sum(1 for b in books if b.plex_match)
        report.audible_stats = {"matched": matched, "unmatched": len(books) - matched}
        report.domain_id = self.id
        return OrganizeResult(domain_id=self.id, report=report, books=books)

    def _resolve_show(
        self,
        show_title: str,
        cfg: Config,
        tmdb: TmdbClient,
        extract_client,
        verbose: bool,
    ) -> tuple[int, float, str]:
        """Return (tmdb_id, confidence, canonical_name). tmdb_id is -1 if unresolved."""
        results = tmdb.search_tv(show_title, verbose=verbose)[:10]
        if not results:
            return -1, 0.0, ""
        by_id = {r.get("id"): (r.get("name") or "") for r in results}
        picked_id: int | None = None
        conf = 0.0
        if extract_client and cfg.enable_ai and not cfg.no_ai:
            lines = [
                "Pick the best TMDB TV show id for this library folder name.",
                f"Show folder/title hint: {show_title}",
                "",
                "Candidates (JSON):",
                json.dumps([tv_candidate(r) for r in results[:8]], indent=2),
                "",
                "Disambiguation guidance:",
                "- A hint may name a regional edition of a format (e.g. 'Taskmaster "
                "Australia', 'The Office US'). TMDB often lists these under the base "
                "name, so match on origin_country / original_language / original_name "
                "rather than requiring the region word in 'name'.",
                "- If a year appears in the hint, prefer the candidate whose "
                "first_air_date matches; popularity breaks otherwise-equal ties.",
                "Return tmdb_id=-1 only if no candidate plausibly matches.",
            ]
            try:
                data = extract_client.extract(
                    [{"role": "user", "content": "\n".join(lines)}],
                    TMDB_TV_PICK_SCHEMA,
                    temperature=0.0,
                )
                tid = int(data.get("tmdb_id", -1))
                if tid > 0 and tid in by_id:
                    return tid, 0.95, by_id[tid]
            except ExtractError as e:
                if verbose:
                    ui.verbose(f"[plex_tv] LLM show pick failed: {e}")

        best = None
        best_score = 0.0
        for r in results[:5]:
            score = max(
                _fuzzy(r.get("name") or "", show_title),
                _fuzzy(r.get("original_name") or "", show_title),
            )
            if score > best_score:
                best_score = score
                best = r
        if best and best_score >= 0.45:
            return int(best["id"]), best_score, (best.get("name") or "")
        return -1, 0.0, ""

    def _episode_path(
        self,
        book: Book,
        cfg: Config,
        show_tmdb_id: int,
        show_title: str,
        show_conf: float,
        thresh: float,
    ) -> None:
        if show_tmdb_id <= 0:
            book.ambiguous = True
            return

        f0 = book.files[0]
        season = int(f0.fn_book_number) if f0.fn_book_number is not None else 1
        if book.sequence and str(book.sequence).isdigit():
            episode = int(book.sequence)
        else:
            m = re.search(r"S(\d+)E(\d+)", f0.filename, re.I)
            episode = int(m.group(2)) if m else 1
        raw_ep_title = (book.title or "").strip()
        ep_title = sanitize_path_component(raw_ep_title) if raw_ep_title else ""

        pm = PlexMatch(
            tmdb_id=show_tmdb_id,
            title=ep_title,
            media_type="tv",
            show_title=show_title,
            season=season,
            episode=episode,
            episode_title=ep_title,
            confidence=show_conf,
        )
        book.plex_match = pm
        book.title = ep_title
        book.ambiguous = show_conf < thresh

        tmdb_hint = f"{{tmdb-{show_tmdb_id}}}"
        sshow = sanitize_path_component(show_title)
        show_folder = cfg.plex_tv_show_folder_pattern.format(
            show_title=sshow, tmdb_hint=tmdb_hint, tmdb_id=show_tmdb_id,
        )
        ext = f0.extension.lower()
        fname = cfg.plex_tv_episode_pattern.format(
            show_title=sshow,
            season=season,
            episode=episode,
            episode_title=ep_title,
            tmdb_hint=tmdb_hint,
            tmdb_id=show_tmdb_id,
            ext=ext,
        )
        # Tidy the artifacts an empty episode title leaves behind, e.g.
        # "S04E01 -  {tmdb-1}.mkv" -> "S04E01 {tmdb-1}.mkv".
        if not ep_title:
            fname = re.sub(r"\s*-\s*(?=\{tmdb-)", " ", fname)
        fname = re.sub(r"\s{2,}", " ", fname)
        book.target_dir = os.path.join(
            cfg.library_root, show_folder, f"Season {season:02d}",
        )
        f0.target_filename = fname


register_domain(PlexTvDomain())
