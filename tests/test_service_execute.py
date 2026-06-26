"""Service layer: plan persistence, full + selective execute, and undo."""

import os
import tempfile
import unittest

from quackaloger import service
from quackaloger.models import MoveAction, PlanReport


def _touch(path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write("x")


class ServiceExecuteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self._old = os.environ.get("QUACK_CONFIG_DIR")
        os.environ["QUACK_CONFIG_DIR"] = os.path.join(self.root, "config")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("QUACK_CONFIG_DIR", None)
        else:
            os.environ["QUACK_CONFIG_DIR"] = self._old
        self.tmp.cleanup()

    def _persist_bundle(self, lib, moves):
        cfg = service.build_config(
            lib, "audiobooks",
            overrides={"no_ai": True, "no_audible": True}, dry_run=False,
        )
        cfg.embed_markers = False  # deterministic: no metadata writes on dummy files
        report = PlanReport()
        report.moves = moves
        bundle = service.PlanBundle(
            plan_id=service._new_plan_id(),
            created_at=0.0,
            library_path=cfg.library_root,
            domain="audiobooks",
            cfg=cfg,
            report=report,
            books=[],
        )
        service._save_bundle(bundle)
        return bundle

    def test_persist_roundtrip(self):
        lib = os.path.join(self.root, "lib")
        os.makedirs(os.path.join(lib, "src"))
        src = os.path.join(lib, "src", "a.mp3")
        _touch(src)
        bundle = self._persist_bundle(lib, [MoveAction(src, os.path.join(lib, "Out", "a.mp3"), "audio")])
        loaded = service.load_bundle(bundle.plan_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(len(loaded.report.moves), 1)
        self.assertEqual(loaded.report.moves[0].source, src)

    def test_execute_then_undo(self):
        lib = os.path.join(self.root, "lib")
        os.makedirs(os.path.join(lib, "src"))
        src = os.path.join(lib, "src", "a.mp3")
        dst = os.path.join(lib, "Out", "a.mp3")
        _touch(src)
        bundle = self._persist_bundle(lib, [MoveAction(src, dst, "audio")])

        res = service.execute(bundle.plan_id)
        self.assertIn("run_id", res)
        self.assertTrue(os.path.exists(dst))
        self.assertFalse(os.path.exists(src))

        runs = service.runs_for(lib)
        self.assertEqual(len(runs), 1)

        undo = service.undo(lib, res["run_id"])
        self.assertGreaterEqual(undo["reverted"], 1)
        self.assertTrue(os.path.exists(src))
        self.assertFalse(os.path.exists(dst))

    def test_selective_execute_moves_only_chosen(self):
        lib = os.path.join(self.root, "lib")
        os.makedirs(os.path.join(lib, "src"))
        s1 = os.path.join(lib, "src", "a.mp3")
        s2 = os.path.join(lib, "src", "b.mp3")
        d1 = os.path.join(lib, "Out", "a.mp3")
        d2 = os.path.join(lib, "Out", "b.mp3")
        _touch(s1)
        _touch(s2)
        bundle = self._persist_bundle(lib, [MoveAction(s1, d1, "audio"), MoveAction(s2, d2, "audio")])

        service.execute(bundle.plan_id, selected_indexes=[0])
        self.assertTrue(os.path.exists(d1))
        self.assertFalse(os.path.exists(d2))
        self.assertTrue(os.path.exists(s2))

    def test_unknown_plan_raises(self):
        with self.assertRaises(KeyError):
            service.execute("nope-does-not-exist")


if __name__ == "__main__":
    unittest.main()
