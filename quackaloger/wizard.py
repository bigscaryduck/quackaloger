"""Interactive setup wizard (machine user config + optional library hints)."""

from __future__ import annotations

import os

from quackaloger import llm_models
from quackaloger.env_warnings import collect_legacy_abo_env_warnings
from quackaloger.ui import ui
from quackaloger.user_config import ensure_user_config_template, load_user_yaml, user_config_path

try:
    import yaml
except ImportError:
    yaml = None


def run_wizard() -> None:
    if yaml is None:
        ui.error("PyYAML is required for the wizard. pip install PyYAML")
        return

    ui.rule("Quackaloger setup wizard")
    ui.info("This updates your machine-wide user config (not library .quackaloger/).")
    ui.muted(f"Target file: {user_config_path()}")
    ui._console.print()

    legacy = collect_legacy_abo_env_warnings()
    if legacy:
        ui.warn("Legacy environment variables detected:")
        for m in legacy:
            ui.muted(f"  • {m}")
        ui._console.print()

    ensure_user_config_template()
    data = load_user_yaml()
    data.setdefault("version", 1)

    choices = [
        "audiobooks",
        "plex_movies",
        "plex_tv",
        "comic_archives",
        "ebooks",
    ]
    ui.info("Select one or more organizer domains (space to toggle in TUI clients):")
    picked = ui.prompt_checkbox(
        "Domains to enable",
        choices,
    )
    if not picked:
        ui.warn("No domains selected. Exiting without changes.")
        return
    data["organize_domains"] = picked

    paths = data.setdefault("paths", {})
    if "audiobooks" in picked:
        paths["audiobooks"] = ui.prompt_text(
            "Audiobooks library root (optional, can use --library later)",
            default=paths.get("audiobooks") or "",
        )
    if "plex_movies" in picked:
        paths["plex_movies"] = ui.prompt_text(
            "Plex Movie library root",
            default=paths.get("plex_movies") or "",
        )
    if "plex_tv" in picked:
        paths["plex_tv"] = ui.prompt_text(
            "Plex TV library root",
            default=paths.get("plex_tv") or "",
        )

    prov = ui.prompt_select(
        "LLM provider for disambiguation (optional)",
        ["openai", "anthropic", "skip AI setup"],
    )
    ai = data.setdefault("ai", {})
    if prov == "skip AI setup":
        ai["enable"] = False
    else:
        ai["enable"] = True
        ai["provider"] = prov
        if prov == "openai":
            ui.muted(f"Recommended model id: {llm_models.DEFAULT_OPENAI_SMALL}")
            ai["model"] = ui.prompt_text("OpenAI model id (blank = default)", default="") or None
        else:
            ui.muted(f"Recommended model id: {llm_models.DEFAULT_ANTHROPIC_HAIKU}")
            ai["model"] = ui.prompt_text("Anthropic model id (blank = default)", default="") or None

    keys = data.setdefault("api_keys", {})
    if prov == "openai":
        k = ui.prompt_text("OpenAI API key (blank to keep using env OPENAI_API_KEY only)", default="")
        if k:
            keys["openai"] = k
    elif prov == "anthropic":
        k = ui.prompt_text("Anthropic API key (blank to keep using env ANTHROPIC_API_KEY only)", default="")
        if k:
            keys["anthropic"] = k

    if "plex_movies" in picked or "plex_tv" in picked:
        tk = ui.prompt_text(
            "TMDB API key (https://www.themoviedb.org/settings/api — blank to use env QUACK_TMDB_API_KEY / TMDB_API_KEY only)",
            default="",
        )
        if tk:
            keys["tmdb"] = tk

    path = user_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)

    ui.success(f"Wrote user configuration: {path}")
    ui.info("Run: quackaloger organize --library <path> [--domain ...]")
    ui.muted("Library .quackaloger/config.yaml is still created on first use of that folder.")
