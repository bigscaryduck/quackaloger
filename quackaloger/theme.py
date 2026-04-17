"""Brand constants: colors, typography, ASCII art, and flavor text."""

# ---------------------------------------------------------------------------
# Colors (Rich style strings)
# ---------------------------------------------------------------------------

PINK = "#FF10F0"
CYAN = "#00FFFF"

STYLE_SUCCESS = f"bold {CYAN}"
STYLE_ERROR = f"bold {PINK}"
STYLE_WARN = "bold yellow"
STYLE_INFO = "dim"
STYLE_MUTED = "dim italic"
STYLE_PHASE = f"bold {PINK}"

# ---------------------------------------------------------------------------
# Brand identity
# ---------------------------------------------------------------------------

APP_NAME = "Audiobook Quackaloger"
AUTHOR = "BigScaryDuck"
TAGLINE = "Putting your ducks (and your audiobooks) in a row."
FIGLET_FONT = "slant"

# Duck C -- Menacing/Scary Duck
# Body renders in cyan, O (eye) and > (beak tip) in pink.
ASCII_DUCK_LINES = [
    ("cyan", "     _~_"),
    ("mixed", "   >(O  )___"),   # > and O in pink, rest cyan
    ("mixed", "    ( ._>  /"),   # > in pink, rest cyan
    ("cyan", "     `---'"),
]

# ---------------------------------------------------------------------------
# Flavor text (edge cases only -- never on main success path)
# ---------------------------------------------------------------------------

FLAVOR = {
    "empty_library": (
        "The pond is empty. Zero audiobooks detected. "
        "This is, technically, a form of organization."
    ),
    "dry_run_done": "No files were harmed in the making of this report.",
    "all_correct": (
        "All books accounted for. The universe is, briefly, in order."
    ),
    "undo_complete": (
        "Undo complete. Entropy has been, locally and temporarily, reversed."
    ),
    "error_generic": (
        "An error occurred. This is, statistically speaking, inevitable."
    ),
    "api_waiting": (
        "Audible has been contacted. A response is, probably, forthcoming."
    ),
}
