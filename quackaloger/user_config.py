"""Machine-level user configuration (paths, AI defaults, API keys, profiles)."""

from __future__ import annotations

import os
from typing import Any, Optional

try:
    import yaml
except ImportError:
    yaml = None

from quackaloger import llm_models


def user_config_dir() -> str:
    # Explicit override (containers/tests): pin the state dir without touching
    # APPDATA, which on Windows also controls Python's user site-packages.
    override = os.environ.get("QUACK_CONFIG_DIR")
    if override:
        return override
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "quackaloger")
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = xdg if xdg else os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "quackaloger")


def user_config_path() -> str:
    return os.path.join(user_config_dir(), "config.yaml")


def user_config_exists() -> bool:
    return os.path.exists(user_config_path())


def _default_user_yaml() -> str:
    return f"""\
# Quackaloger user configuration (machine-wide)
# Library-specific settings still live in <library>/.quackaloger/config.yaml
# Merge precedence: CLI > environment > library config > this file > built-in defaults

version: 1

organize_domains:
  - audiobooks

ai:
  provider: openai   # openai | anthropic
  enable: true
  # Leave null to use release defaults from llm_models (OpenAI: {llm_models.DEFAULT_OPENAI_SMALL}, Anthropic Haiku: {llm_models.DEFAULT_ANTHROPIC_HAIKU})
  model: null

# Optional explicit keys (prefer QUACK_* or provider env vars for secrets)
api_keys:
  openai: ""
  anthropic: ""
  tmdb: ""

# Quick paths the wizard can populate (optional; --library still works)
paths:
  audiobooks: ""
  plex_movies: ""
  plex_tv: ""

profiles:
  default:
    domains:
      - audiobooks
"""


def ensure_user_config_template() -> str:
    d = user_config_dir()
    os.makedirs(d, exist_ok=True)
    path = user_config_path()
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(_default_user_yaml())
    return path


def load_user_yaml() -> dict[str, Any]:
    path = user_config_path()
    if yaml is None or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_user_yaml(data: dict[str, Any]) -> str:
    """Write the machine-wide user config back to disk. Returns the path."""
    if yaml is None:
        raise RuntimeError("PyYAML is required to write user config")
    d = user_config_dir()
    os.makedirs(d, exist_ok=True)
    path = user_config_path()
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
    return path


def apply_user_yaml_to_config(cfg: Any, data: dict[str, Any]) -> None:
    """Overlay user file onto a Config instance (before library YAML)."""
    if not data:
        return
    od = data.get("organize_domains")
    if isinstance(od, list) and od:
        cfg.organize_domains = [str(x) for x in od]
    ai = data.get("ai", {})
    if isinstance(ai, dict):
        if "provider" in ai and ai["provider"]:
            cfg.llm_provider = str(ai["provider"]).lower()
        if "enable" in ai:
            cfg.enable_ai = bool(ai["enable"])
        if ai.get("model"):
            m = str(ai["model"])
            if cfg.llm_provider == "anthropic":
                cfg.anthropic_model = m
            else:
                cfg.openai_model = m
    keys = data.get("api_keys", {})
    if isinstance(keys, dict):
        if keys.get("openai"):
            cfg.openai_api_key = str(keys["openai"])
        if keys.get("anthropic"):
            cfg.anthropic_api_key = str(keys["anthropic"])
        if keys.get("tmdb"):
            cfg.tmdb_api_key = str(keys["tmdb"])
    paths = data.get("paths", {})
    if isinstance(paths, dict):
        if paths.get("audiobooks"):
            cfg.user_path_audiobooks = str(paths["audiobooks"])
        if paths.get("plex_movies"):
            cfg.user_path_plex_movies = str(paths["plex_movies"])
        if paths.get("plex_tv"):
            cfg.user_path_plex_tv = str(paths["plex_tv"])
