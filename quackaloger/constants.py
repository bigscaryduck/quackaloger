"""Compile-time constants: regex patterns, extension sets, API URLs, defaults."""

import re

# ---------------------------------------------------------------------------
# File extension sets
# ---------------------------------------------------------------------------

AUDIO_EXTENSIONS = {".mp3", ".m4b", ".m4a", ".flac", ".ogg", ".wma", ".aac", ".opus"}
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".webm", ".mpeg", ".mpg"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
SIDECAR_FILES = {"metadata.json", "metadata.abs", "desc.txt", "reader.txt"}
SIDECAR_EXTENSIONS = {".nfo", ".cue", ".opf"}

# ---------------------------------------------------------------------------
# Regex patterns for parsing filenames / folder names
# ---------------------------------------------------------------------------

BOOK_NUM_PATTERNS = [
    re.compile(r"[Bb]ook\s*[#]?\s*(\d+)", re.IGNORECASE),
    re.compile(r"[Vv]ol(?:ume)?\.?\s*(\d+)", re.IGNORECASE),
]

SERIES_BOOK_FILENAME_PATTERN = re.compile(
    r"^(.+?)\s*-\s*[Bb]ook\s*[#]?\s*(\d+)", re.IGNORECASE
)

TITLE_FOLDER_SEQUENCE_PATTERNS = [
    re.compile(r"^(?:Vol\.?|Volume|Book)\s*(\d+)\s*[-\u2013.]\s*(.+)", re.IGNORECASE),
    re.compile(r"^(\d+)\s*[-\u2013.]\s*(.+)"),
    re.compile(r"^(.+?)\s*[-\u2013]\s*(?:Vol\.?|Volume|Book)\s*(\d+)\s*$", re.IGNORECASE),
]

NARRATOR_BRACE_PATTERN = re.compile(r"\{(.+?)\}")
INVALID_PATH_CHARS = re.compile(r'[<>:"/|?*]')

# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

AUDIBLE_CATALOG_URL = "https://api.audible.com/1.0/catalog/products"
AUDNEXUS_BOOK_URL = "https://api.audnex.us/books"

# ---------------------------------------------------------------------------
# Defaults (overridable via config)
# ---------------------------------------------------------------------------

DEFAULT_CONFIDENCE_THRESHOLD = 0.75
DEFAULT_AUDIBLE_REQUEST_DELAY = 1.0
DEFAULT_MAX_AUDIBLE_CANDIDATES = 5
DEFAULT_GPT_MODEL = "gpt-4o-mini"  # prefer quackaloger.llm_models.DEFAULT_OPENAI_SMALL in new code

TOOL_DIR_NAME = ".quackaloger"
MARKER_TAG_NAME = "AUDIOBOOK_ORGANIZER"
