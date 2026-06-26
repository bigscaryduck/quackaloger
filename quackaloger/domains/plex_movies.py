"""Plex Movie libraries: TMDB match + folder naming with {tmdb-…} hints."""

from __future__ import annotations

import json
import os
import re
from difflib import SequenceMatcher

from quackaloger.config import Config, domain_confidence_threshold
from quackaloger.domains.base import OrganizeContext, OrganizeResult, register_domain
from quackaloger.llm import ExtractError
from quackaloger.models import Book, PlanReport, PlexMatch
from quackaloger.plex_discovery import scan_movie_books
from quackaloger.plex_format import sanitize_path_component
from quackaloger.reporting import build_plan
from quackaloger.tmdb import TmdbClient, movie_candidate
from quackaloger.ui import ui

TMDB_MOVIE_PICK_SCHEMA = {
    "type": "object",
    "properties": {
        "tmdb_id": {"type": "integer", "description": "Chosen TMDB movie id, or -1 if none fit"},
        "reason": {"type": "string"},
    },
    "required": ["tmdb_id", "reason"],
}


def _fuzzy(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


class PlexMoviesDomain:
    id = "plex_movies"

    def validate_config(self, cfg: Config) -> None:
        if not (cfg.tmdb_api_key or "").strip():
            raise ValueError(
                "plex_movies requires a TMDB API key (identification.tmdb_api_key, "
                "api_keys.tmdb in user config, or QUACK_TMDB_API_KEY / TMDB_API_KEY)."
            )

    def run(self, ctx: OrganizeContext) -> OrganizeResult:
        cfg = ctx.cfg
        verbose = ctx.verbose
        thresh = domain_confidence_threshold(cfg, self.id)
        tmdb = TmdbClient(cfg.tmdb_api_key.strip(), cfg.tool_dir)

        books = scan_movie_books(cfg.library_root, verbose=verbose)
        if not books:
            ui.info("No movie video files found (skipped TV Season folders).")
            rep = PlanReport()
            rep.domain_id = self.id
            return OrganizeResult(domain_id=self.id, report=rep, books=[])

        ui.phase(3, f"TMDB identification for {len(books)} movie folders")
        with ui.progress(len(books), desc="TMDB (movies)") as progress:
            task = progress.add_task("TMDB (movies)", total=len(books))
            for book in books:
                self._identify_and_path(book, cfg, tmdb, ctx.extract_client, thresh, verbose)
                progress.advance(task)

        folders: dict = {}
        ui.phase(6, "Building move plan (plex_movies)")
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

    def _identify_and_path(
        self,
        book: Book,
        cfg: Config,
        tmdb: TmdbClient,
        extract_client,
        thresh: float,
        verbose: bool,
    ) -> None:
        q = os.path.basename(book.source_dir.rstrip(os.sep))
        q = re.sub(r"\.(\d{4})\.", r" (\1) ", q)
        results = tmdb.search_movie(q, verbose=verbose)[:10]
        if not results:
            book.ambiguous = True
            return

        picked_id: int | None = None
        conf = 0.0

        if extract_client and cfg.enable_ai and not cfg.no_ai:
            lines = [
                "Pick the single best TMDB movie id for this local folder.",
                f"Local folder name: {q}",
                "",
                "Candidates (JSON):",
                json.dumps([movie_candidate(r) for r in results[:8]], indent=2),
                "",
                "Disambiguation guidance:",
                "- Match foreign / anime titles via original_title and "
                "original_language as well as title (a romanized or English name "
                "in the folder may match the original_title).",
                "- If a year appears in the folder, prefer the candidate whose "
                "release_date matches; popularity breaks otherwise-equal ties.",
                "Return tmdb_id=-1 only if no candidate plausibly matches.",
            ]
            try:
                data = extract_client.extract(
                    [{"role": "user", "content": "\n".join(lines)}],
                    TMDB_MOVIE_PICK_SCHEMA,
                    temperature=0.0,
                )
                tid = int(data.get("tmdb_id", -1))
                if tid > 0 and any(r.get("id") == tid for r in results):
                    picked_id = tid
                    conf = 0.95
            except ExtractError as e:
                if verbose:
                    ui.verbose(f"[plex_movies] LLM pick failed: {e}")

        if picked_id is None:
            best = None
            best_score = 0.0
            for r in results[:5]:
                score = max(
                    _fuzzy(r.get("title") or "", q),
                    _fuzzy(r.get("original_title") or "", q),
                )
                rd = r.get("release_date") or ""
                year = rd.split("-")[0] if rd else ""
                if year and year in q:
                    score = min(1.0, score + 0.15)
                if score > best_score:
                    best_score = score
                    best = r
            if best and best_score >= 0.45:
                picked_id = int(best["id"])
                conf = best_score

        if picked_id is None:
            book.ambiguous = True
            return

        detail = tmdb.movie_detail(picked_id, verbose=verbose)
        title = detail.get("title") or results[0].get("title") or q
        rd = detail.get("release_date") or ""
        year = int(rd.split("-")[0]) if rd and rd.split("-")[0].isdigit() else None

        pm = PlexMatch(
            tmdb_id=picked_id,
            title=title,
            media_type="movie",
            year=year,
            confidence=conf,
        )
        book.plex_match = pm
        book.title = title
        book.year = str(year) if year else None
        book.ambiguous = conf < thresh

        tmdb_hint = f"{{tmdb-{picked_id}}}"
        stitle = sanitize_path_component(title)
        year_s = str(year) if year else ""
        folder = cfg.plex_movie_folder_pattern.format(
            title=stitle, year=year_s, tmdb_hint=tmdb_hint, tmdb_id=picked_id,
        )
        ext = book.files[0].extension.lower()
        fname = cfg.plex_movie_file_pattern.format(
            title=stitle, year=year_s, tmdb_hint=tmdb_hint, tmdb_id=picked_id, ext=ext,
        )
        book.target_dir = os.path.join(cfg.library_root, folder)
        book.files[0].target_filename = fname


register_domain(PlexMoviesDomain())
