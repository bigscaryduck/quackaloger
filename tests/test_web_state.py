"""Web state store: libraries.json CRUD + normalization."""

import os
import tempfile
import unittest


class WebStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("QUACK_CONFIG_DIR")
        os.environ["QUACK_CONFIG_DIR"] = os.path.join(self.tmp.name, "config")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("QUACK_CONFIG_DIR", None)
        else:
            os.environ["QUACK_CONFIG_DIR"] = self._old
        self.tmp.cleanup()

    def test_crud_and_normalization(self):
        from quackaloger.web import state

        self.assertEqual(state.list_libraries(), [])

        lib = state.add_library(name="Audiobooks", path="/data/ab", domain="audiobooks")
        self.assertEqual(len(state.list_libraries()), 1)
        self.assertEqual(state.get_library(lib["id"])["name"], "Audiobooks")
        # watch defaults filled in
        self.assertIn("watch", lib)
        self.assertFalse(lib["watch"]["enabled"])
        self.assertEqual(lib["watch"]["mode"], "scan-only")

        # invalid domain normalizes to audiobooks; invalid watch mode normalizes
        bad = state.add_library(name="X", path="/data/x", domain="bogus",
                                watch={"enabled": True, "mode": "explode", "debounce_seconds": -5})
        self.assertEqual(bad["domain"], "audiobooks")
        self.assertEqual(bad["watch"]["mode"], "scan-only")
        self.assertGreaterEqual(bad["watch"]["debounce_seconds"], 1)

        state.update_library(lib["id"], name="Renamed")
        self.assertEqual(state.get_library(lib["id"])["name"], "Renamed")

        self.assertTrue(state.delete_library(lib["id"]))
        self.assertEqual(len(state.list_libraries()), 1)
        self.assertFalse(state.delete_library("missing"))


if __name__ == "__main__":
    unittest.main()
