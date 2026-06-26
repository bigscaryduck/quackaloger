"""Directory browser sandbox: paths outside QUACK_BROWSE_ROOTS are rejected."""

import os
import tempfile
import unittest

try:
    from fastapi.testclient import TestClient
    HAVE_WEB = True
except Exception:
    HAVE_WEB = False


@unittest.skipUnless(HAVE_WEB, "web extra (fastapi) not installed")
class BrowseSandboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.allowed = os.path.join(self.tmp.name, "allowed")
        os.makedirs(os.path.join(self.allowed, "sub"))
        self._env = {
            "QUACK_CONFIG_DIR": os.path.join(self.tmp.name, "config"),
            "QUACK_BROWSE_ROOTS": self.allowed,
        }
        self._old = {k: os.environ.get(k) for k in self._env}
        os.environ.update(self._env)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_roots_listing_and_sandbox(self):
        from quackaloger.web.app import app

        with TestClient(app) as client:
            roots = client.get("/api/browse")
            self.assertEqual(roots.status_code, 200)
            self.assertIn("roots", roots.json())

            inside = client.get("/api/browse", params={"path": self.allowed})
            self.assertEqual(inside.status_code, 200)
            names = [d["name"] for d in inside.json()["dirs"]]
            self.assertIn("sub", names)

            outside = client.get("/api/browse", params={"path": self.tmp.name})
            self.assertEqual(outside.status_code, 403)

            traversal = client.get("/api/browse", params={"path": os.path.join(self.allowed, "..", "..")})
            self.assertEqual(traversal.status_code, 403)


if __name__ == "__main__":
    unittest.main()
