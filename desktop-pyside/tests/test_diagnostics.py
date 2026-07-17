from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DiagnosticsTests(unittest.TestCase):
    def test_unhandled_python_exception_is_persisted_without_faulthandler_label(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "runtime.log"
            environment = os.environ.copy()
            environment["MDM_DIAGNOSTIC_LOG"] = str(log)
            script = (
                "import sys;"
                f"sys.path.insert(0, {str(ROOT)!r});"
                "from diagnostics import install_runtime_diagnostics;"
                "install_runtime_diagnostics();"
                "raise RuntimeError('expected-test-error')"
            )
            result = subprocess.run([sys.executable, "-c", script], env=environment, capture_output=True, text=True, timeout=20)
            self.assertNotEqual(result.returncode, 0)
            content = log.read_text(encoding="utf-8")
            self.assertIn('"event": "python_unhandled_exception"', content)
            self.assertIn("expected-test-error", content)
            self.assertNotIn("Windows fatal exception", content)


if __name__ == "__main__":
    unittest.main()
