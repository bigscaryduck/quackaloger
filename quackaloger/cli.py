"""CLI entry point: organize, undo, and status subcommands."""

import argparse
import os
import sys

from quackaloger import __version__
from quackaloger.ui import ui

# Force UTF-8 on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", errors="replace", buffering=1)
    sys.stderr = open(sys.stderr.fileno(), mode="w", encoding="utf-8", errors="replace", buffering=1)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quackaloger",
        description="Organize audiobooks, Plex movies/TV, and more (modular domains)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--library", default=None,
                        help="Library root path (default: current directory)")
    parser.add_argument("--config", default=None,
                        help="Path to config.yaml (default: .quackaloger/config.yaml)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose output")
    parser.add_argument("--debug", action="store_true",
                        help="Debug-level output")

    subs = parser.add_subparsers(dest="command")

    # --- organize ---
    org = subs.add_parser("organize", help="Scan and organize the library (default)")
    org.add_argument("library_path", nargs="?", default=None,
                     help="Library root path (positional alternative to --library)")
    org.add_argument("--execute", action="store_true",
                     help="Commit changes (default is dry-run)")
    org.add_argument("--dry-run", action="store_true", dest="dry_run",
                     help="Preview only (this is the default)")
    org.add_argument("--confidence", type=float, default=None,
                     help="Minimum confidence threshold (0.0-1.0)")
    org.add_argument("--openai-key", default=None,
                     help="OpenAI API key (or set OPENAI_API_KEY env var)")
    org.add_argument("--no-ai", action="store_true",
                     help="Disable AI matching")
    org.add_argument("--no-audible", action="store_true",
                     help="Skip all Audible API calls")
    org.add_argument("--force", action="store_true",
                     help="Re-process files that already have markers")
    org.add_argument(
        "--domain",
        action="append",
        dest="domains",
        default=None,
        help="Organizer domain to run (repeatable). Default: config organize_domains, usually audiobooks.",
    )
    org.add_argument(
        "--llm-provider",
        choices=["openai", "anthropic"],
        default=None,
        help="Override LLM provider for this run",
    )
    org.add_argument(
        "--anthropic-key",
        default=None,
        help="Anthropic API key (or set ANTHROPIC_API_KEY / QUACK_ANTHROPIC_API_KEY)",
    )

    # --- wizard ---
    subs.add_parser("wizard", help="Interactive setup for user config, domains, and API keys")

    # --- undo ---
    undo = subs.add_parser("undo", help="Undo a previous run")
    undo.add_argument("--run", default=None, dest="run_id",
                      help="Run ID to undo (default: interactive picker)")
    undo.add_argument("--force", action="store_true",
                      help="Skip conflict checks")

    # --- status ---
    subs.add_parser("status", help="Show what would change on next run")

    # --- init ---
    init = subs.add_parser("init", help="Generate config.yaml and directory structure without scanning")
    init.add_argument("library_path", nargs="?", default=None,
                      help="Library root path (positional alternative to --library)")

    return parser


# ---------------------------------------------------------------------------
# Organize subcommand
# ---------------------------------------------------------------------------

def _check_first_run(library_root: str, config_path_override: str = None) -> str:
    """If no config.yaml exists, prompt the user for how to proceed.

    Returns: "continue" | "init" | "exit"
    """
    from quackaloger.config import config_exists

    if config_exists(library_root, config_path_override):
        return "continue"

    ui._console.print()
    ui.info("No configuration file found.")
    ui.muted("This appears to be your first time running the organizer for this library.")
    ui._console.print()

    choices = [
        "Generate config file and review it before scanning",
        "Continue with defaults",
        "Exit",
    ]
    answer = ui.prompt_select("How would you like to proceed?", choices)
    if answer == choices[0]:
        return "init"
    elif answer == choices[1]:
        return "continue"
    else:
        return "exit"


def _cmd_organize(args, parser):
    from quackaloger.config import load_config, create_default_config
    from quackaloger.runner import build_extract_client_for_cfg, organize_library_flow

    # Determine library root
    library_root = getattr(args, "library_path", None) or getattr(args, "library", None) or os.getcwd()
    library_root = os.path.abspath(library_root)
    if not os.path.isdir(library_root):
        ui.error_panel("Library Not Found", f"Library path does not exist: {library_root}")
        sys.exit(1)

    # Check for first run (no config file)
    config_override = getattr(args, "config", None)
    first_run = _check_first_run(library_root, config_override)
    if first_run == "exit":
        ui.info("Exiting.")
        return
    if first_run == "init":
        config_path = create_default_config(library_root)
        ui.success(f"Config file created: {config_path}")
        ui.info("Edit it to set your preferences, then run again.")
        ui.muted("Tip: run 'python -m quackaloger init' for a full walkthrough.")
        return

    # Build CLI overrides dict
    cli_overrides = {
        "execute": getattr(args, "execute", False),
        "dry_run": getattr(args, "dry_run", False),
        "verbose": getattr(args, "verbose", False),
        "debug": getattr(args, "debug", False),
        "confidence": getattr(args, "confidence", None),
        "openai_key": getattr(args, "openai_key", None),
        "anthropic_key": getattr(args, "anthropic_key", None),
        "llm_provider": getattr(args, "llm_provider", None),
        "domains": getattr(args, "domains", None),
        "no_ai": getattr(args, "no_ai", False),
        "no_audible": getattr(args, "no_audible", False),
        "force": getattr(args, "force", False),
    }

    cfg = load_config(library_root, config_path=getattr(args, "config", None), cli_args=cli_overrides)
    verbose = cfg.verbosity in ("verbose", "debug")

    # Mode confirmation when neither --dry-run nor --execute was explicitly given
    explicit_mode = getattr(args, "execute", False) or getattr(args, "dry_run", False)
    if not explicit_mode:
        if cfg.dry_run:
            ui.info("Mode: DRY RUN (preview only, no files will be moved)")
            ui.muted("This is the default. Use --execute to make real changes.")
            if not ui.prompt_confirm("Continue?", default=True):
                ui.warn("Aborted.")
                return
        else:
            ui.warn("EXECUTE mode is active (files WILL be moved and modified).")
            ui.muted("This is set in your config.yaml (dry_run: false).")
            ui.muted("Use --dry-run to preview changes without modifying anything.")
            if not ui.prompt_confirm("Continue?", default=False):
                ui.warn("Aborted.")
                return

    extract_client = build_extract_client_for_cfg(cfg)

    ui.init(verbose=verbose)
    ui.banner(version=__version__)

    mode = "EXECUTE" if not cfg.dry_run else "DRY RUN"
    ui.info(f"Mode: {mode}")
    ui.info(f"Library: {cfg.library_root}")
    ui.info(f"Domains: {', '.join(cfg.organize_domains)}")
    if cfg.no_audible:
        ui.warn("Audible lookups: DISABLED (audiobooks domain only)")
    if extract_client:
        ui.info(f"AI extraction: ENABLED (provider={cfg.llm_provider})")
    elif not cfg.no_ai and cfg.enable_ai:
        ui.muted(
            "AI extraction: DISABLED (set keys for your llm_provider — "
            "OPENAI_API_KEY and/or ANTHROPIC_API_KEY / QUACK_* env vars)"
        )
    ui._console.print()

    try:
        organize_library_flow(cfg, extract_client=extract_client, verbose=verbose)
    except KeyError as e:
        ui.error_panel("Unknown domain", str(e))
        sys.exit(2)
    except ValueError as e:
        ui.error_panel("Configuration error", str(e))
        sys.exit(2)


# ---------------------------------------------------------------------------
# Undo subcommand
# ---------------------------------------------------------------------------

def _cmd_undo(args, parser):
    from quackaloger.config import load_config
    from quackaloger.history import list_runs, undo_run

    library_root = getattr(args, "library", None) or os.getcwd()
    library_root = os.path.abspath(library_root)
    cfg = load_config(library_root, config_path=getattr(args, "config", None))

    run_id = getattr(args, "run_id", None)
    force = getattr(args, "force", False)

    if not run_id:
        runs = list_runs(cfg.tool_dir)
        if not runs:
            ui.info("No runs found in history.")
            return

        run_choices = []
        for r in runs:
            started = r.started_at[:19].replace("T", " ") if r.started_at else "?"
            run_choices.append(f"{r.run_id}  {r.status:<12}  {started}")
        run_choices.append("Cancel")

        answer = ui.prompt_select("Select a run to undo:", run_choices)
        if answer == "Cancel" or answer is None:
            ui.info("Cancelled.")
            return

        idx = run_choices.index(answer)
        run_id = runs[idx].run_id

    ui.info(f"Undoing run: {run_id}")
    result = undo_run(cfg.tool_dir, run_id, force=force)

    ui.success(f"Reverted: {result['reverted']}")
    ui.info(f"Skipped:  {result['skipped']}")
    if result["errors"]:
        ui.error("Errors:")
        for e in result["errors"]:
            ui.error(f"  {e}")
    else:
        ui.flavor("undo_complete")


# ---------------------------------------------------------------------------
# Status subcommand
# ---------------------------------------------------------------------------

def _cmd_status(args, parser):
    from quackaloger.config import load_config
    from quackaloger.discovery import scan_library, group_files_into_books
    from quackaloger.history import list_runs

    library_root = getattr(args, "library", None) or os.getcwd()
    library_root = os.path.abspath(library_root)
    cfg = load_config(library_root, config_path=getattr(args, "config", None))

    ui.banner(version=__version__)
    ui.info(f"Library: {cfg.library_root}")
    ui.info(f"Tool dir: {cfg.tool_dir}")
    ui.info(f"Organize domains: {', '.join(cfg.organize_domains)}")
    ui._console.print()

    # Quick scan
    folders = scan_library(
        cfg.library_root,
        audio_extensions=cfg.audio_extensions,
        image_extensions=cfg.image_extensions,
        ignore_folders=cfg.ignore_folders,
        force=False,
        verbose=False,
    )
    books = group_files_into_books(folders, verbose=False)

    ui.info(f"{len(books)} books would be processed on next run.")

    # History
    runs = list_runs(cfg.tool_dir)
    if runs:
        rows = []
        for r in runs[:5]:
            started = r.started_at[:19].replace("T", " ") if r.started_at else "?"
            rows.append((r.run_id, r.status, started))
        ui.table("Recent Runs", ["Run ID", "Status", "Started"], rows)
    else:
        ui.muted("No previous runs found.")


# ---------------------------------------------------------------------------
# Init subcommand
# ---------------------------------------------------------------------------

def _cmd_init(args, parser):
    from quackaloger.config import load_config
    from quackaloger.constants import TOOL_DIR_NAME

    library_root = getattr(args, "library_path", None) or getattr(args, "library", None) or os.getcwd()
    library_root = os.path.abspath(library_root)
    if not os.path.isdir(library_root):
        ui.error_panel("Library Not Found", f"Library path does not exist: {library_root}")
        sys.exit(1)

    cfg = load_config(library_root, config_path=getattr(args, "config", None))
    config_path = os.path.join(cfg.tool_dir, "config.yaml")

    # Ensure all subdirectories exist
    subdirs = {
        "cache":        "Cached API results (Audible, Audnexus, TMDB, etc.)",
        "history":      "Run journals -- every file move is recorded here for undo support",
        "logs":         "Summary and verbose reports from each run",
        "trash":        "Soft-deleted files (sidecars, metadata.json) -- never permanently deleted",
        "needs-review": "Quarantined books that couldn't be confidently identified",
    }
    for subdir in subdirs:
        os.makedirs(os.path.join(cfg.tool_dir, subdir), exist_ok=True)

    ui.banner(version=__version__)
    ui.success("Setup Complete")
    ui.info(f"Library: {cfg.library_root}")

    # --- Directory structure (Panel -- scannable reference) ---
    dir_lines = [f"All tool data lives in: {TOOL_DIR_NAME}/\n"]
    for subdir, desc in subdirs.items():
        dir_lines.append(f"  {TOOL_DIR_NAME}/{subdir + '/':<16} {desc}")
    ui.panel("\n".join(dir_lines), title="\u203a Directory Structure")

    # --- Configuration (flowing prose with rule) ---
    ui.rule(f"\u203a Configuration")
    ui.info(f"Config file: {config_path}")
    ui._console.print()
    ui.info("Key settings and their defaults:")
    ui._console.print()
    ui.muted("confidence_threshold: 0.75")
    ui.muted("  How confident the tool must be in an Audible match.")
    ui.muted("  Scale: 0.0 (accept anything) to 1.0 (exact match only).")
    ui.muted("  Books below this threshold go to needs-review/.")
    ui._console.print()
    ui.muted('series_pattern: "{author}/{series}/Book {sequence} - {title} {{narrator}}"')
    ui.muted('standalone_pattern: "{author}/{title} {{narrator}}"')
    ui.muted("  Controls the output folder structure.")
    ui.muted("  Tokens: {author}, {series}, {sequence}, {title}, {narrator}")
    ui._console.print()
    ui.muted('unidentified_action: "quarantine"')
    ui.muted('  "quarantine" = move to needs-review/ for manual sorting.')
    ui.muted('  "skip" = leave in place, do nothing.')
    ui._console.print()
    ui.muted("embed_markers: true")
    ui.muted("  Writes a hidden tag so subsequent runs skip processed files.")
    ui.muted("  Invisible to media players. Set false to avoid modifying files.")
    ui._console.print()
    ui.muted("dry_run: true")
    ui.muted("  Previews changes by default. Use --execute to commit.")

    # --- AI matching (flowing prose) ---
    ui.rule(f"\u203a AI-Assisted Matching (Recommended)")
    ui.info("The tool can use OpenAI's GPT-4o-mini to accurately identify audiobooks")
    ui.info("by comparing local metadata against Audible search results. Far more")
    ui.info("accurate than fuzzy matching for unusual naming or missing metadata.")
    ui._console.print()
    ui.muted("Cost: ~$0.01-0.05 for an entire library (thousands of books).")
    ui._console.print()
    ui.info("To enable AI matching, do ONE of the following:")
    ui._console.print()
    ui.muted(f"Option A: Set the key in config.yaml")
    ui.muted(f"  Open {config_path}")
    ui.muted(f'  Set:  openai_api_key: "sk-your-key-here"')
    ui._console.print()
    ui.muted("Option B: Set an environment variable")
    ui.muted('  PowerShell:  $env:OPENAI_API_KEY = "sk-your-key-here"')
    ui.muted("  CMD:         set OPENAI_API_KEY=sk-your-key-here")
    ui._console.print()
    ui.muted("Option C: Pass as a flag")
    ui.muted("  quackaloger organize --openai-key sk-your-key-here")
    ui._console.print()
    ui.muted("Without an API key, the tool falls back to fuzzy string matching.")

    # --- Next steps (Panel -- scannable commands) ---
    from quackaloger.theme import CYAN
    next_steps = (
        f"1. Edit your config:    Open {config_path}\n"
        f"2. Preview changes:     quackaloger organize\n"
        f"3. Execute for real:    quackaloger organize --execute\n"
        f"4. Undo if needed:      quackaloger undo"
    )
    ui.panel(next_steps, title="\u203a Next Steps", border_style=CYAN)
    ui._console.print()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    from quackaloger.env_warnings import emit_legacy_env_warnings

    emit_legacy_env_warnings(ui)

    parser = _build_parser()
    args = parser.parse_args()

    # Default to 'organize' if no subcommand given
    command = args.command or "organize"

    if command == "organize":
        _cmd_organize(args, parser)
    elif command == "undo":
        _cmd_undo(args, parser)
    elif command == "status":
        _cmd_status(args, parser)
    elif command == "init":
        _cmd_init(args, parser)
    elif command == "wizard":
        from quackaloger.wizard import run_wizard

        run_wizard()
    else:
        parser.print_help()
