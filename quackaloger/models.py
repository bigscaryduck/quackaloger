"""Shared dataclasses used across all modules."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AudioFileMeta:
    """Metadata extracted from a single audio file (tags + filename + path hints)."""
    filepath: str
    filename: str
    extension: str
    size: int = 0
    tag_title: Optional[str] = None
    tag_album: Optional[str] = None
    tag_artist: Optional[str] = None
    tag_album_artist: Optional[str] = None
    tag_composer: Optional[str] = None
    tag_series: Optional[str] = None
    tag_series_part: Optional[str] = None
    tag_asin: Optional[str] = None
    tag_track: Optional[str] = None
    tag_disc: Optional[str] = None
    fn_book_number: Optional[int] = None
    fn_series_hint: Optional[str] = None
    path_author_hint: Optional[str] = None
    path_series_hint: Optional[str] = None
    path_title_hint: Optional[str] = None
    raw_tags: dict = field(default_factory=dict)
    target_filename: Optional[str] = None


@dataclass
class AudibleMatch:
    """Metadata retrieved from Audible/Audnexus for a single book."""
    asin: Optional[str] = None
    title: Optional[str] = None
    subtitle: Optional[str] = None
    author: Optional[str] = None
    narrator: Optional[str] = None
    series: Optional[str] = None
    sequence: Optional[str] = None
    year: Optional[str] = None
    description: Optional[str] = None
    genres: list = field(default_factory=list)
    duration_min: int = 0
    confidence: float = 0.0


@dataclass
class PlexMatch:
    """TMDB-based match for Plex movie or TV episode."""
    tmdb_id: int
    title: str
    media_type: str = "movie"  # movie | tv
    year: Optional[int] = None
    show_title: Optional[str] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    episode_title: Optional[str] = None
    confidence: float = 0.0


@dataclass
class Book:
    """A logical audiobook: one or more files that belong together."""
    book_id: str
    files: list = field(default_factory=list)          # list[AudioFileMeta]
    cover_files: list = field(default_factory=list)     # list[str] (paths)
    sidecar_files: list = field(default_factory=list)   # list[str] (paths)
    source_dir: str = ""
    author: Optional[str] = None
    title: Optional[str] = None
    series: Optional[str] = None
    sequence: Optional[str] = None
    narrator: Optional[str] = None
    year: Optional[str] = None
    asin: Optional[str] = None
    audible_match: Optional[AudibleMatch] = None
    target_dir: Optional[str] = None
    conflicts: list = field(default_factory=list)
    resolution_log: list = field(default_factory=list)
    ambiguous: bool = False
    plex_match: Optional[PlexMatch] = None
    domain_tag: str = ""  # which organizer domain produced this row (verbose reports)


@dataclass
class MoveAction:
    """A single planned file operation."""
    source: str
    dest: str
    file_type: str   # "audio", "cover", "sidecar"


@dataclass
class ActionRecord:
    """A single reversible action recorded during execution."""
    action_type: str   # "move", "trash", "embed_marker"
    source: str = ""
    dest: str = ""
    filepath: str = ""
    marker_data: dict = field(default_factory=dict)
    timestamp: str = ""


@dataclass
class RunRecord:
    """Metadata for a single run of the organizer."""
    run_id: str
    started_at: str = ""
    finished_at: str = ""
    status: str = "in_progress"   # "in_progress", "completed", "failed", "dry_run"
    library_root: str = ""
    config_snapshot: dict = field(default_factory=dict)
    summary: dict = field(default_factory=dict)
    actions: list = field(default_factory=list)  # list[ActionRecord]


@dataclass
class PlanReport:
    """Aggregated plan for a dry-run or execution."""
    moves: list = field(default_factory=list)           # list[MoveAction]
    already_correct: list = field(default_factory=list)  # list[Book]
    conflicts: list = field(default_factory=list)
    ambiguous: list = field(default_factory=list)        # list[Book]
    duplicates: list = field(default_factory=list)
    skipped_folders: list = field(default_factory=list)
    stale_metadata: list = field(default_factory=list)   # list[str] (paths)
    quarantine: list = field(default_factory=list)        # list[Book]
    to_review: list = field(default_factory=list)         # list[str] unmatched leftover file paths
    audible_stats: dict = field(default_factory=dict)
    # Optional domain tag for logs / merged runs (e.g. audiobooks, plex_movies)
    domain_id: str = ""
