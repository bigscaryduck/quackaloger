"""Folder-watch handler: debounce, media-only filter, and tool-dir ignore."""

import os
import tempfile
import time
import unittest

try:
    import watchdog  # noqa: F401
    HAVE_WATCHDOG = True
except Exception:
    HAVE_WATCHDOG = False


class _FakeEvent:
    def __init__(self, src_path, is_directory=False, dest_path=None):
        self.src_path = src_path
        self.is_directory = is_directory
        if dest_path is not None:
            self.dest_path = dest_path


@unittest.skipUnless(HAVE_WATCHDOG, "watchdog not installed")
class WatcherHandlerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.libpath = os.path.join(self.tmp.name, "lib")
        os.makedirs(self.libpath)
        self._old = os.environ.get("QUACK_CONFIG_DIR")
        os.environ["QUACK_CONFIG_DIR"] = os.path.join(self.tmp.name, "config")

        from quackaloger.web import actions, state
        self.actions = actions
        self.state = state
        self.lib = state.add_library(
            name="L", path=self.libpath, domain="audiobooks",
            watch={"enabled": True, "mode": "scan-only", "debounce_seconds": 1},
        )
        self.calls = []
        self._orig_submit = actions.submit_auto
        actions.submit_auto = lambda lib: self.calls.append(lib)

    def tearDown(self):
        self.actions.submit_auto = self._orig_submit
        if self._old is None:
            os.environ.pop("QUACK_CONFIG_DIR", None)
        else:
            os.environ["QUACK_CONFIG_DIR"] = self._old
        self.tmp.cleanup()

    def _handler(self):
        from quackaloger.web import watcher
        return watcher._LibraryHandler(self.state.get_library(self.lib["id"]))

    def test_media_event_debounces_to_single_submit(self):
        h = self._handler()
        # irrelevant: non-media + tool-dir media should NOT arm
        h.on_created(_FakeEvent(os.path.join(self.libpath, "notes.txt")))
        h.on_created(_FakeEvent(os.path.join(self.libpath, ".quackaloger", "cache", "x.mp3")))
        # relevant burst -> coalesced into one trigger
        h.on_created(_FakeEvent(os.path.join(self.libpath, "x.mp3")))
        h.on_created(_FakeEvent(os.path.join(self.libpath, "y.mp3")))
        time.sleep(1.5)
        self.assertEqual(len(self.calls), 1)

    def test_irrelevant_only_never_submits(self):
        h = self._handler()
        h.on_created(_FakeEvent(os.path.join(self.libpath, "cover.jpg")))
        h.on_created(_FakeEvent(os.path.join(self.libpath, "desc.txt")))
        time.sleep(1.3)
        self.assertEqual(len(self.calls), 0)


if __name__ == "__main__":
    unittest.main()
