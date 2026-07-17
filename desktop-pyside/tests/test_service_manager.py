from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from service_manager import NodeServiceManager, ServiceError  # noqa: E402


class ServiceManagerTests(unittest.TestCase):
    def test_stop_terminates_only_owned_process(self) -> None:
        manager = NodeServiceManager(ROOT.parent)
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
        manager.process = process
        manager.stop()
        self.assertIsNotNone(process.poll())
        self.assertIsNone(manager.process)

    def test_stop_does_not_touch_reused_service(self) -> None:
        manager = NodeServiceManager(ROOT.parent)
        manager.stop()
        self.assertFalse(manager.owns_process)

    def test_detach_releases_ownership_without_terminating_owned_node(self) -> None:
        manager = NodeServiceManager(ROOT.parent)
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
        manager.process = process
        try:
            manager.detach()
            self.assertIsNone(manager.process)
            self.assertIsNone(process.poll())
        finally:
            process.terminate()
            process.wait(timeout=5)

    def test_reuses_only_matching_product_and_protocol(self) -> None:
        manager = NodeServiceManager(ROOT.parent)
        matching = {
            "ok": True,
            "product": manager.PRODUCT,
            "protocol_version": manager.PROTOCOL_VERSION,
            "build_fingerprint": "abc123",
        }
        with patch.object(manager, "_read_health", return_value=matching):
            self.assertFalse(manager.ensure_started())
            self.assertFalse(manager.owns_process)

    def test_incompatible_service_is_not_killed_or_reused(self) -> None:
        manager = NodeServiceManager(ROOT.parent)
        incompatible = {
            "ok": True,
            "product": "other-product",
            "protocol_version": manager.PROTOCOL_VERSION,
            "build_fingerprint": "foreign",
        }
        with patch.object(manager, "_read_health", return_value=incompatible), patch.object(
            manager, "stop"
        ) as stop:
            with self.assertRaisesRegex(ServiceError, "不是本软件|协议不兼容"):
                manager.ensure_started()
        stop.assert_not_called()

    def test_unknown_health_contract_is_rejected_in_chinese(self) -> None:
        manager = NodeServiceManager(ROOT.parent)
        with patch.object(manager, "_read_health", return_value={"ok": True}):
            with self.assertRaisesRegex(ServiceError, "无法确认|协议"):
                manager.ensure_started()

    def test_explicit_data_directory_keeps_release_smoke_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"MDM_DATA_DIR": directory}
        ):
            manager = NodeServiceManager(ROOT.parent)
            self.assertEqual(manager.data_dir, Path(directory))


if __name__ == "__main__":
    unittest.main()
