"""Path-safe strings for Plex folder/file naming."""

import re

from quackaloger.constants import INVALID_PATH_CHARS


def sanitize_path_component(name: str) -> str:
    s = (name or "").strip()
    s = INVALID_PATH_CHARS.sub("_", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or "Unknown"
