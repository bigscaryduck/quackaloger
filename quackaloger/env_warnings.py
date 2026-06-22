"""Detect deprecated env prefixes and emit one-time migration hints."""

import os

_warned = False


def collect_legacy_abo_env_warnings() -> list[str]:
    """Return human-readable warnings for legacy ABO_* vars (prefer QUACK_*)."""
    msgs = []
    pairs = [
        ("ABO_OPENAI_API_KEY", "QUACK_OPENAI_API_KEY"),
        ("ABO_AUDIBLE_CATALOG_URL", "QUACK_AUDIBLE_CATALOG_URL"),
        ("ABO_AUDNEXUS_URL", "QUACK_AUDNEXUS_URL"),
    ]
    for old, new in pairs:
        if os.environ.get(old) and not os.environ.get(new):
            msgs.append(
                f"{old} is deprecated; set {new} (both are read for now; {old} will be removed later)."
            )
    return msgs


def emit_legacy_env_warnings(ui_mod, *, force: bool = False) -> None:
    """Print deprecation warnings once per process unless force=True."""
    global _warned
    if _warned and not force:
        return
    msgs = collect_legacy_abo_env_warnings()
    if msgs:
        _warned = True
        for m in msgs:
            ui_mod.warn(m)
