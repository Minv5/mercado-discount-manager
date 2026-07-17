from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from log_rotation import rotate_log


class LogRotationTests(unittest.TestCase):
    def test_rotation_is_bounded_and_preserves_newest_backups(self):
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "runtime.log"
            log.write_text("first", encoding="utf-8")
            self.assertTrue(rotate_log(log, max_bytes=5, backups=2))
            self.assertFalse(log.exists())
            self.assertEqual(log.with_name("runtime.log.1").read_text(encoding="utf-8"), "first")
            log.write_text("second", encoding="utf-8")
            self.assertTrue(rotate_log(log, max_bytes=5, backups=2))
            log.write_text("third", encoding="utf-8")
            self.assertTrue(rotate_log(log, max_bytes=5, backups=2))
            self.assertEqual(log.with_name("runtime.log.1").read_text(encoding="utf-8"), "third")
            self.assertEqual(log.with_name("runtime.log.2").read_text(encoding="utf-8"), "second")
            self.assertFalse(log.with_name("runtime.log.3").exists())


if __name__ == "__main__":
    unittest.main()
