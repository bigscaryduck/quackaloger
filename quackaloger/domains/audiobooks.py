"""Audiobook library organization (Audible + Audnexus + optional LLM)."""

from quackaloger.config import Config, domain_confidence_threshold
from quackaloger.discovery import group_files_into_books, scan_library
from quackaloger.domains.base import OrganizeContext, OrganizeResult, register_domain
from quackaloger.identification import run_identification
from quackaloger.models import PlanReport
from quackaloger.pathing import generate_target_path
from quackaloger.reporting import build_plan
from quackaloger.resolver import resolve_book_metadata
from quackaloger.ui import ui


class AudiobooksDomain:
    id = "audiobooks"

    def validate_config(self, cfg: Config) -> None:
        return

    def run(self, ctx: OrganizeContext) -> OrganizeResult:
        cfg = ctx.cfg
        verbose = ctx.verbose
        thresh = domain_confidence_threshold(cfg, self.id)

        folders = scan_library(
            cfg.library_root,
            audio_extensions=cfg.audio_extensions,
            image_extensions=cfg.image_extensions,
            ignore_folders=cfg.ignore_folders,
            follow_symlinks=cfg.follow_symlinks,
            force=cfg.force,
            verbose=verbose,
        )
        books = group_files_into_books(folders, verbose=verbose)
        for b in books:
            b.domain_tag = "audiobooks"
        if not books:
            ui.info("No audiobooks found. Nothing to do.")
            rep = PlanReport()
            rep.domain_id = self.id
            return OrganizeResult(domain_id=self.id, report=rep, books=[])

        if not cfg.no_audible:
            run_identification(books, cfg, extract_client=ctx.extract_client)
        else:
            ui.info("Skipping Audible lookups (--no-audible)")

        ui.phase(4, f"Resolving metadata for {len(books)} books")
        with ui.progress(len(books), desc="Resolving metadata") as progress:
            task = progress.add_task("Resolving metadata", total=len(books))
            for book in books:
                resolve_book_metadata(
                    book, folders,
                    confidence_threshold=thresh,
                    verbose=verbose,
                )
                progress.advance(task)

        ui.phase(5, "Generating target paths")
        with ui.progress(len(books), desc="Generating paths") as progress:
            task = progress.add_task("Generating paths", total=len(books))
            for book in books:
                generate_target_path(
                    book, cfg.library_root,
                    series_pattern=cfg.series_pattern,
                    standalone_pattern=cfg.standalone_pattern,
                    verbose=verbose,
                )
                progress.advance(task)

        ui.phase(6, "Building move plan (audiobooks)")
        with ui.spinner("Building plan..."):
            report = build_plan(
                books, folders, cfg.library_root,
                unidentified_action=cfg.unidentified_action,
                confidence_threshold=thresh,
                verbose=verbose,
            )
        report.domain_id = self.id
        return OrganizeResult(domain_id=self.id, report=report, books=books)


register_domain(AudiobooksDomain())
