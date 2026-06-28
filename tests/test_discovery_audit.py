"""Regression tests for the audit fixes (no network).

Covers: TV episodes are discovered anywhere (not just under Season folders),
plex_movies leaves episode-like files for plex_tv, the two Plex domains don't
both claim the same file, and the 0-result outcome is explained.
"""

import os
import tempfile
import unittest

from quackaloger.config import Config
from quackaloger.models import (
    AudioFileMeta,
    Book,
    MoveAction,
    PlanReport,
)
from quackaloger.constants import TOOL_DIR_NAME
from quackaloger.plex_discovery import (
    _resolve_tv_file,
    scan_movie_books,
    scan_tv_episode_books,
)
from quackaloger.reporting import build_plan, collect_review_leftovers, explain_outcome
from quackaloger.tmdb import movie_candidate, tv_candidate


def _touch(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()


class _MixedLibrary:
    """A disorganized library: real movies + loose / foldered / release episodes."""

    def __init__(self):
        self.root = tempfile.mkdtemp()
        _touch(os.path.join(self.root, "Inception 2010 1080p BluRay x264.mkv"))
        _touch(os.path.join(self.root, "Old Movie (1999)", "Old Movie 1999.mkv"))
        _touch(os.path.join(self.root, "Some.Show.S01E02.1080p.WEB.mkv"))
        _touch(os.path.join(self.root, "My Show", "My Show S03E04 720p.mkv"))
        _touch(os.path.join(self.root, "Release.S02E01", "Release.S02E01.1080p.mkv"))


class DiscoveryPartitionTests(unittest.TestCase):
    def setUp(self):
        self.lib = _MixedLibrary()

    def _names(self, books):
        return sorted(os.path.basename(f.filepath) for b in books for f in b.files)

    def test_tv_found_outside_season_folders(self):
        tv = self._names(scan_tv_episode_books(self.lib.root))
        self.assertEqual(
            tv,
            sorted([
                "My Show S03E04 720p.mkv",
                "Release.S02E01.1080p.mkv",
                "Some.Show.S01E02.1080p.WEB.mkv",
            ]),
        )

    def test_movies_skip_episodes(self):
        movies = self._names(scan_movie_books(self.lib.root))
        self.assertEqual(
            movies,
            sorted(["Inception 2010 1080p BluRay x264.mkv", "Old Movie 1999.mkv"]),
        )

    def test_no_overlap_between_domains(self):
        movies = set(self._names(scan_movie_books(self.lib.root)))
        tv = set(self._names(scan_tv_episode_books(self.lib.root)))
        self.assertEqual(movies & tv, set())


class SeeNumberingTests(unittest.TestCase):
    """Old cartoon/anime "Show - 101 - Title" 3-digit episode codes."""

    def test_three_digit_see_matches(self):
        show, season, episode, title = _resolve_tv_file(
            "Biker Mice from Mars (2006) - 101 - The Adventure begins (1) [jpv711]",
            "Biker Mice From Mars (2006)", "TV Shows",
        )
        self.assertEqual((season, episode), (1, 1))
        self.assertIn("Biker Mice", show)
        self.assertEqual(title, "The Adventure begins (1)")  # release tag stripped

    def test_high_episode_in_season_one(self):
        _, season, episode, _ = _resolve_tv_file(
            "Biker Mice from Mars (2006) - 128 - Turf Wars", "Biker Mice From Mars (2006)", "x",
        )
        self.assertEqual((season, episode), (1, 28))

    def test_resolution_codec_year_do_not_false_match(self):
        for stem in [
            "Some.Movie.2006.1080p.WEB-DL.x264-GRP",
            "Random Doc 720p H 264-RAWR",
            "Plain Title No Episode",
        ]:
            self.assertIsNone(_resolve_tv_file(stem, "Folder", "TV Shows"), stem)

    def test_explicit_markers_still_win(self):
        _, season, episode, _ = _resolve_tv_file(
            "MADtv - S05 E07 - The 100th Episode", "MADtv", "x",
        )
        self.assertEqual((season, episode), (5, 7))

    def test_movies_skip_see_episodes(self):
        d = tempfile.mkdtemp()
        _touch(os.path.join(d, "Biker Mice From Mars (2006)",
                            "Biker Mice from Mars (2006) - 101 - The Adventure begins.avi"))
        _touch(os.path.join(d, "Real Movie (2010) 1080p.avi"))
        movies = sorted(os.path.basename(f.filepath)
                        for b in scan_movie_books(d) for f in b.files)
        self.assertEqual(movies, ["Real Movie (2010) 1080p.avi"])


class CrossDomainDedupTests(unittest.TestCase):
    def test_second_domain_dropped_for_same_source(self):
        from quackaloger.domains.base import OrganizeResult, register_domain
        from quackaloger import runner

        src = os.path.join(tempfile.mkdtemp(), "Show.S01E01.mkv")

        def mk(domain):
            f = AudioFileMeta(filepath=src, filename="Show.S01E01.mkv", extension=".mkv")
            b = Book(book_id=domain, files=[f], source_dir=os.path.dirname(src))
            r = PlanReport()
            r.moves = [MoveAction(source=src, dest=src + ".out", file_type="video")]
            r.audible_stats = {"matched": 1, "unmatched": 0}
            return OrganizeResult(domain_id=domain, report=r, books=[b])

        class Fake:
            def __init__(self, i):
                self.id = i

            def validate_config(self, cfg):
                pass

            def run(self, ctx):
                return mk(self.id)

        register_domain(Fake("audit_a"))
        register_domain(Fake("audit_b"))

        cfg = Config()
        cfg.organize_domains = ["audit_a", "audit_b"]
        merged, books = runner.run_organize_domains(cfg, extract_client=None)
        self.assertEqual(len(books), 1)
        self.assertEqual(len(merged.moves), 1)


def _tv_book(name, dest_dir, dest_file):
    f = AudioFileMeta(filepath="/src/" + name, filename=name, extension=".mkv")
    f.target_filename = dest_file
    b = Book(book_id=name, files=[f], source_dir="/src/" + name, domain_tag="plex_tv")
    b.target_dir = dest_dir
    return b


class EpisodeDuplicateDetectionTests(unittest.TestCase):
    def test_distinct_episodes_in_one_season_are_not_duplicates(self):
        books = [
            _tv_book("e1", "/lib/Show/Season 04", "Show - S04E01 {tmdb-1}.mkv"),
            _tv_book("e2", "/lib/Show/Season 04", "Show - S04E02 {tmdb-1}.mkv"),
            _tv_book("e4", "/lib/Show/Season 04", "Show - S04E04 {tmdb-1}.mkv"),
        ]
        rep = build_plan(books, {}, "/lib")
        self.assertEqual(len(rep.duplicates), 0)

    def test_same_episode_from_two_sources_is_a_duplicate(self):
        books = [
            _tv_book("a", "/lib/Show/Season 01", "Show - S01E01 {tmdb-9}.mkv"),
            _tv_book("b", "/lib/Show/Season 01", "Show - S01E01 {tmdb-9}.mkv"),
        ]
        rep = build_plan(books, {}, "/lib")
        self.assertEqual(len(rep.duplicates), 1)


class CandidateEnrichmentTests(unittest.TestCase):
    def test_tv_candidate_includes_region_signals(self):
        c = tv_candidate({
            "id": 219341, "name": "Taskmaster", "original_name": "Taskmaster",
            "origin_country": ["AU"], "original_language": "en",
            "first_air_date": "2023-02-02", "popularity": 12.34, "vote_average": 7.5,
        })
        self.assertEqual(c["origin_country"], ["AU"])
        self.assertEqual(c["original_language"], "en")
        self.assertEqual(c["popularity"], 12.3)

    def test_movie_candidate_includes_original_title(self):
        c = movie_candidate({
            "id": 5, "title": "Spirited Away", "original_title": "千と千尋の神隠し",
            "original_language": "ja", "release_date": "2001-07-20", "popularity": 99.9,
        })
        self.assertEqual(c["original_title"], "千と千尋の神隠し")
        self.assertEqual(c["original_language"], "ja")


class LeftoverCollectionTests(unittest.TestCase):
    def setUp(self):
        self.lib = tempfile.mkdtemp()
        self.tool = os.path.join(self.lib, TOOL_DIR_NAME)
        _touch(os.path.join(self.lib, "Show.S01E01", "Show.S01E01.mkv"))   # organized
        _touch(os.path.join(self.lib, "Show.S01E01", "info.nfo"))          # leftover
        _touch(os.path.join(self.lib, "Show.S01E01", "Screens", "s1.png"))  # nested leftover
        _touch(os.path.join(self.lib, "RandomJunk", "readme.txt"))         # no-video folder
        _touch(os.path.join(self.lib, "loose-note.txt"))                   # loose root file
        _touch(os.path.join(self.tool, "logs", "old.txt"))                 # tool dir: never swept

    def test_collects_only_unmatched_content(self):
        from quackaloger.models import MoveAction, PlanReport
        rep = PlanReport()
        src = os.path.join(self.lib, "Show.S01E01", "Show.S01E01.mkv")
        rep.moves = [MoveAction(source=src, dest=os.path.join(self.lib, "Show", "x.mkv"),
                                file_type="video")]
        collect_review_leftovers(rep, self.lib, self.tool)
        got = sorted(os.path.relpath(p, self.lib) for p in rep.to_review)
        self.assertEqual(got, sorted([
            os.path.join("RandomJunk", "readme.txt"),
            os.path.join("Show.S01E01", "Screens", "s1.png"),
            os.path.join("Show.S01E01", "info.nfo"),
            "loose-note.txt",
        ]))
        # The organized source and anything under .quackaloger are never swept.
        self.assertNotIn(src, rep.to_review)
        self.assertFalse(any(TOOL_DIR_NAME in p for p in rep.to_review))


class ExplainOutcomeTests(unittest.TestCase):
    def test_nothing_found(self):
        r = PlanReport()
        r.audible_stats = {"matched": 0, "unmatched": 0}
        self.assertIn("No matching media files", explain_outcome(r))

    def test_all_already_correct(self):
        r = PlanReport()
        r.audible_stats = {"matched": 3, "unmatched": 0}
        r.already_correct = [object(), object(), object()]
        msg = explain_outcome(r)
        self.assertIn("already correctly organized", msg)

    def test_has_moves_returns_empty(self):
        r = PlanReport()
        r.moves = [MoveAction(source="/a", dest="/b", file_type="video")]
        self.assertEqual(explain_outcome(r), "")


if __name__ == "__main__":
    unittest.main()
