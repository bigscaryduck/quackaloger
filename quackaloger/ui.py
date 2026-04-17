"""Centralized UI layer -- all user-facing output routes through here.

The rest of the codebase should ``from quackaloger.ui import ui``
and never import rich or questionary directly.
"""

import sys
from contextlib import contextmanager
from typing import Any, Optional, Sequence

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
)
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from quackaloger.theme import (
    ASCII_DUCK_LINES,
    CYAN,
    FIGLET_FONT,
    FLAVOR,
    PINK,
    STYLE_ERROR,
    STYLE_INFO,
    STYLE_MUTED,
    STYLE_PHASE,
    STYLE_SUCCESS,
    STYLE_WARN,
    TAGLINE,
)


class UI:
    """Singleton wrapping rich.Console + questionary for branded output."""

    def __init__(self, console: Console = None):
        self._console = console or Console(highlight=False)
        self._verbose = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def init(self, verbose: bool = False) -> None:
        """Set runtime flags once config is loaded."""
        self._verbose = verbose

    @property
    def is_interactive(self) -> bool:
        return sys.stdin.isatty()

    # ------------------------------------------------------------------
    # Banner
    # ------------------------------------------------------------------

    def banner(self, version: str = "") -> None:
        """Print the branded startup banner with figlet wordmark + duck."""
        import pyfiglet

        from quackaloger.theme import APP_NAME, AUTHOR

        wordmark = pyfiglet.figlet_format("Quackaloger", font=FIGLET_FONT)
        wordmark_lines = wordmark.rstrip("\n").split("\n")

        duck_rich = self._render_duck_lines()

        self._console.print()

        # "Audiobook" label above the figlet
        self._console.print("  Audiobook", style=f"dim {PINK}")

        # Figlet wordmark in pink
        for line in wordmark_lines:
            self._console.print(line, style=PINK, highlight=False)

        # Duck to the right -- printed separately below the wordmark
        for segment in duck_rich:
            self._console.print(segment, highlight=False)

        # Tagline
        self._console.print(f"  {TAGLINE}", style=STYLE_MUTED)

        # Version + author
        meta_parts = []
        if version:
            meta_parts.append(f"v{version}")
        meta_parts.append(f"by {AUTHOR}")
        meta_line = "  " + "  ".join(meta_parts)
        self._console.print(meta_line, style="dim")
        self._console.print()

    def _render_duck_lines(self) -> list[Text]:
        """Build Rich Text objects for the ASCII duck with mixed coloring."""
        results = []
        for kind, raw in ASCII_DUCK_LINES:
            if kind == "cyan":
                results.append(Text(raw, style=CYAN))
            elif kind == "mixed":
                txt = Text()
                for ch in raw:
                    if ch in ("O", ">"):
                        txt.append(ch, style=PINK)
                    else:
                        txt.append(ch, style=CYAN)
                results.append(txt)
            else:
                results.append(Text(raw))
        return results

    # ------------------------------------------------------------------
    # Message output (three-tier: warn / error / error_panel)
    # ------------------------------------------------------------------

    def success(self, msg: str) -> None:
        self._console.print(f"  {msg}", style=STYLE_SUCCESS)

    def error(self, msg: str) -> None:
        self._console.print(f"  {msg}", style=STYLE_ERROR)

    def error_panel(self, title: str, msg: str) -> None:
        self._console.print()
        self._console.print(
            Panel(
                Text(msg),
                title=title,
                title_align="left",
                border_style=PINK,
                padding=(1, 2),
            )
        )

    def warn(self, msg: str) -> None:
        self._console.print(f"  {msg}", style=STYLE_WARN)

    def info(self, msg: str) -> None:
        self._console.print(f"  {msg}", style=STYLE_INFO)

    def muted(self, msg: str) -> None:
        self._console.print(f"  {msg}", style=STYLE_MUTED)

    def verbose(self, msg: str) -> None:
        if self._verbose:
            self._console.print(f"  {msg}", style=STYLE_INFO)

    def text(self, msg: str, style: str = "") -> None:
        """Print a line with an optional explicit style."""
        self._console.print(f"  {msg}", style=style or None, highlight=False)

    # ------------------------------------------------------------------
    # Structural elements
    # ------------------------------------------------------------------

    def phase(self, n: int, title: str) -> None:
        self._console.print()
        self._console.print(
            Rule(f" Phase {n}: {title} ", characters="\u2500", style=STYLE_PHASE)
        )
        self._console.print()

    def rule(self, title: str = "") -> None:
        if title:
            self._console.print(Rule(title, characters="\u2500", style=PINK))
        else:
            self._console.print(Rule(characters="\u2500", style="dim"))

    def panel(
        self,
        body: Any,
        title: str = "",
        border_style: str = PINK,
    ) -> None:
        kw: dict[str, Any] = {
            "border_style": border_style,
            "padding": (1, 2),
        }
        if title:
            kw["title"] = title
            kw["title_align"] = "left"
        self._console.print(Panel(body, **kw))

    def table(
        self,
        title: str,
        columns: Sequence[str],
        rows: Sequence[Sequence[str]],
    ) -> None:
        tbl = Table(title=title, title_style=STYLE_PHASE, border_style="dim")
        for col in columns:
            tbl.add_column(col)
        for row in rows:
            tbl.add_row(*row)
        self._console.print(tbl)

    # ------------------------------------------------------------------
    # Progress & spinners
    # ------------------------------------------------------------------

    def progress(self, total: int, desc: str = "", unit: str = "files") -> Progress:
        """Return a rich.Progress context manager styled per brand."""
        return Progress(
            SpinnerColumn(spinner_name="dots", style=CYAN),
            TextColumn("[dim]{task.description}"),
            BarColumn(complete_style=PINK, finished_style=PINK, bar_width=30),
            MofNCompleteColumn(),
            TimeRemainingColumn(),
            console=self._console,
            transient=False,
        )

    @contextmanager
    def spinner(self, msg: str):
        """Context manager showing a dots spinner with a message."""
        with self._console.status(msg, spinner="dots", spinner_style=CYAN):
            yield

    # ------------------------------------------------------------------
    # Interactive prompts (questionary with non-TTY fallback)
    # ------------------------------------------------------------------

    def prompt_select(
        self,
        msg: str,
        choices: Sequence[str],
        default: Optional[str] = None,
    ) -> str:
        if self.is_interactive:
            import questionary

            return questionary.select(msg, choices=list(choices), default=default).ask()
        # Non-TTY fallback
        self._console.print(f"  {msg}")
        for i, c in enumerate(choices, 1):
            self._console.print(f"    {i}. {c}")
        raw = input("  > ").strip()
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(choices):
                return choices[idx]
        except ValueError:
            if raw in choices:
                return raw
        return choices[0]

    def prompt_confirm(
        self,
        msg: str,
        default: bool = False,
    ) -> bool:
        if self.is_interactive:
            import questionary

            result = questionary.confirm(msg, default=default).ask()
            if result is None:
                return False
            return result
        hint = "[Y/n]" if default else "[y/N]"
        raw = input(f"  {msg} {hint}: ").strip().lower()
        if default:
            return raw != "n"
        return raw == "y"

    def prompt_text(
        self,
        msg: str,
        default: str = "",
    ) -> str:
        if self.is_interactive:
            import questionary

            return questionary.text(msg, default=default).ask() or default
        return input(f"  {msg} [{default}]: ").strip() or default

    # ------------------------------------------------------------------
    # Report rendering (terminal only -- disk stays plain text)
    # ------------------------------------------------------------------

    def report_summary(
        self, report: Any, library_root: str
    ) -> None:
        """Render the summary report as a styled Panel in the terminal."""
        import os

        tbl = Table(show_header=False, box=None, padding=(0, 2))
        tbl.add_column("Metric", style="dim")
        tbl.add_column("Value", style=CYAN)

        tbl.add_row("Files to move", str(len(report.moves)))
        tbl.add_row("Already correct", str(len(report.already_correct)))
        tbl.add_row(
            "Audible matched", str(report.audible_stats.get("matched", 0))
        )
        tbl.add_row(
            "Audible unmatched", str(report.audible_stats.get("unmatched", 0))
        )
        tbl.add_row("Conflicts", str(len(report.conflicts)))
        tbl.add_row("Quarantined", str(len(report.quarantine)))
        tbl.add_row("Duplicate targets", str(len(report.duplicates)))

        self.panel(tbl, title="Run Summary", border_style=PINK)

        if report.quarantine:
            self.rule("Quarantined")
            for book in report.quarantine:
                rel = os.path.relpath(book.source_dir, library_root)
                self.warn(
                    f"[{book.book_id}] {rel}  "
                    f"author={book.author}, title={book.title}"
                )

        if report.conflicts:
            self.rule("Conflicts")
            for c in report.conflicts:
                self.error(
                    f"[{c['book_id']}] {c['source_dir']} -- {c['conflict']}"
                )

        if report.duplicates:
            self.rule("Duplicate Targets")
            for dup in report.duplicates:
                rel_target = os.path.relpath(dup["target"], library_root)
                self.warn(f"Target: {rel_target}")
                for src in dup["sources"]:
                    self.info(f"  Source: {os.path.relpath(src, library_root)}")

    def report_verbose(
        self, report: Any, books: list, library_root: str
    ) -> None:
        """Render the verbose report as Rule-separated sections."""
        import os

        # Summary counts
        tbl = Table(show_header=False, box=None, padding=(0, 2))
        tbl.add_column("Metric", style="dim")
        tbl.add_column("Value", style=CYAN)
        tbl.add_row("Total books", str(len(books)))
        tbl.add_row("Files to move", str(len(report.moves)))
        tbl.add_row("Already correct", str(len(report.already_correct)))
        tbl.add_row(
            "Audible matched", str(report.audible_stats.get("matched", 0))
        )
        tbl.add_row(
            "Audible unmatched", str(report.audible_stats.get("unmatched", 0))
        )
        tbl.add_row("Conflicts", str(len(report.conflicts)))
        tbl.add_row("Quarantined", str(len(report.quarantine)))
        tbl.add_row("Duplicate targets", str(len(report.duplicates)))
        tbl.add_row("Stale sidecar files", str(len(report.stale_metadata)))
        self._console.print(tbl)

        # Per-book detail
        self.rule("Book-by-Book Resolution Detail")
        for book in books:
            rel = os.path.relpath(book.source_dir, library_root)
            self._console.print(
                f"\n  [{book.book_id}] {rel}", style=STYLE_PHASE
            )
            self.info(f"Files: {[f.filename for f in book.files]}")
            if book.audible_match:
                am = book.audible_match
                self.info(
                    f"Audible: '{am.title}' by {am.author} "
                    f"ASIN={am.asin} confidence={am.confidence:.2f}"
                )
            else:
                self.muted("Audible: NONE")
            for entry in book.resolution_log:
                self.info(entry)
            if book.conflicts:
                for c in book.conflicts:
                    self.error(f"CONFLICT: {c}")
            self.info(
                f"=> author={book.author}, title={book.title}, "
                f"series={book.series}, seq={book.sequence}, "
                f"narrator={book.narrator}"
            )
            if book.target_dir:
                self.info(
                    f"Target: {os.path.relpath(book.target_dir, library_root)}"
                )

        # Planned moves
        if report.moves:
            self.rule("Planned Moves")
            for action in report.moves:
                rel_src = os.path.relpath(action.source, library_root)
                rel_dst = os.path.relpath(action.dest, library_root)
                self.info(f"[{action.file_type.upper():5s}] {rel_src}")
                self.muted(f"    -> {rel_dst}")

        if report.already_correct:
            self.rule("Already Correct")
            for book in report.already_correct:
                rel = os.path.relpath(book.source_dir, library_root)
                self.info(f"{rel}  ({len(book.files)} files)")

        if report.stale_metadata:
            self.rule("Stale Sidecar Files")
            for f in report.stale_metadata:
                self.info(os.path.relpath(f, library_root))

    # ------------------------------------------------------------------
    # Flavor text
    # ------------------------------------------------------------------

    def flavor(self, key: str) -> None:
        text = FLAVOR.get(key)
        if text:
            self._console.print()
            self.muted(text)


# Module-level singleton
ui = UI()
