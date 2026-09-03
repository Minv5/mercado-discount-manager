from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from service_manager import NodeServiceManager, ServiceError  # noqa: E402
from callback_endpoints import (  # noqa: E402
    DEFAULT_ACTIVITY_CALLBACK_ACK_URL,
    DEFAULT_ACTIVITY_CALLBACK_CLAIM_URL,
)


class ServiceManagerTests(unittest.TestCase):
    def _apply_webhook_settings(self, settings: dict[str, object]) -> dict[str, str]:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"MDM_DATA_DIR": directory}
        ):
            data_dir = Path(directory)
            (data_dir / "settings.json").write_text(
                json.dumps(settings),
                encoding="utf-8",
            )
            manager = NodeServiceManager(ROOT.parent)
            env: dict[str, str] = {}
            manager._apply_webhook_env(env)
            return env

    def test_webhook_env_migrates_legacy_claim_endpoints_before_node_start(self) -> None:
        env = self._apply_webhook_settings(
            {
                "activityCallbackEnabled": True,
                "activityCallbackClaimUrl": "https://xingtupro1020.com/meli-callback/consumer/claim",
                "activityCallbackAckUrl": "https://xingtupro1020.com/meli-callback/consumer/ack",
            }
        )

        self.assertEqual(env["MDM_ACTIVITY_CLAIM_URL"], DEFAULT_ACTIVITY_CALLBACK_CLAIM_URL)
        self.assertEqual(env["MDM_ACTIVITY_CLAIM_ACK_URL"], DEFAULT_ACTIVITY_CALLBACK_ACK_URL)

    def test_webhook_env_uses_current_defaults_when_saved_endpoints_are_empty(self) -> None:
        env = self._apply_webhook_settings({"activityCallbackEnabled": True})

        self.assertEqual(env["MDM_ACTIVITY_CLAIM_URL"], DEFAULT_ACTIVITY_CALLBACK_CLAIM_URL)
        self.assertEqual(env["MDM_ACTIVITY_CLAIM_ACK_URL"], DEFAULT_ACTIVITY_CALLBACK_ACK_URL)

    def test_webhook_env_preserves_explicit_custom_claim_endpoints(self) -> None:
        env = self._apply_webhook_settings(
            {
                "activityCallbackEnabled": True,
                "activityCallbackClaimUrl": "https://callback.example.test/claim",
                "activityCallbackAckUrl": "https://callback.example.test/ack",
            }
        )

        self.assertEqual(env["MDM_ACTIVITY_CLAIM_URL"], "https://callback.example.test/claim")
        self.assertEqual(env["MDM_ACTIVITY_CLAIM_ACK_URL"], "https://callback.example.test/ack")

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

    def test_late_health_keeps_owned_process_and_reports_soft_timeout(self) -> None:
        manager = NodeServiceManager(ROOT.parent)

        class LiveProcess:
            def poll(self):
                return None

        manager.process = LiveProcess()  # type: ignore[assignment]
        manager._startup_started_at = 0.0
        now = [0.0]
        manager._clock = lambda: now[0]
        manager._sleep = lambda _seconds: now.__setitem__(0, 31.0)
        matching = {
            "ok": True,
            "product": manager.PRODUCT,
            "protocol_version": manager.PROTOCOL_VERSION,
            "build_fingerprint": "late-ready",
        }
        responses = iter([None, None, matching])
        manager._read_health = lambda *_args, **_kwargs: next(responses)  # type: ignore[method-assign]
        progress: list[dict[str, object]] = []

        self.assertTrue(
            manager.ensure_started(
                wait_seconds=30,
                hard_timeout_seconds=60,
                progress_callback=progress.append,
            )
        )
        self.assertIsNotNone(manager.process)
        self.assertIn("soft_timeout", [row.get("state") for row in progress])
        self.assertEqual(progress[-1].get("state"), "ready")

    def test_hard_timeout_stops_owned_process_and_is_bounded(self) -> None:
        manager = NodeServiceManager(ROOT.parent)

        class LiveProcess:
            def poll(self):
                return None

        manager.process = LiveProcess()  # type: ignore[assignment]
        manager._startup_started_at = 0.0
        now = [0.0]
        manager._clock = lambda: now[0]
        manager._sleep = lambda _seconds: now.__setitem__(0, 61.0)
        manager._read_health = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        with patch.object(manager, "stop") as stop:
            with self.assertRaisesRegex(ServiceError, "有界等待时间"):
                manager.ensure_started(wait_seconds=30, hard_timeout_seconds=60)
        stop.assert_called_once()

    def test_owned_process_exit_is_reported_without_duplicate_start(self) -> None:
        manager = NodeServiceManager(ROOT.parent)

        class ExitedProcess:
            def poll(self):
                return 1

        manager.process = ExitedProcess()  # type: ignore[assignment]
        manager._startup_started_at = 0.0
        manager._clock = lambda: 0.0
        manager._read_health = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        with patch.object(manager, "_port_owner_pid", side_effect=AssertionError("must not spawn")):
            with self.assertRaisesRegex(ServiceError, "异常退出"):
                manager.ensure_started(wait_seconds=30, hard_timeout_seconds=60)

    def test_explicit_data_directory_keeps_release_smoke_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"MDM_DATA_DIR": directory}
        ):
            manager = NodeServiceManager(ROOT.parent)
            self.assertEqual(manager.data_dir, Path(directory))

    def test_auth_directory_defaults_to_current_user_documents(self) -> None:
        with patch.dict("os.environ", {"ML_STANDALONE_AUTH_DIR": ""}):
            manager = NodeServiceManager(ROOT.parent)
            self.assertEqual(manager.auth_dir, Path.home() / "Documents" / "美客多授权")

    def test_auth_directory_respects_explicit_override(self) -> None:
        configured = Path("D:/Mercado/Auth")
        with patch.dict("os.environ", {"ML_STANDALONE_AUTH_DIR": str(configured)}):
            manager = NodeServiceManager(ROOT.parent)
            self.assertEqual(manager.auth_dir, configured)


if __name__ == "__main__":
    unittest.main()
