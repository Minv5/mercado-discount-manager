from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import build_filters, execution_payload  # noqa: E402


class ReleaseModeTests(unittest.TestCase):
    def test_product_version_source_and_ui_reads_artifact_metadata(self) -> None:
        package = json.loads((ROOT.parent / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["version"], "2.0.92")
        window_source = (ROOT / "main_window.py").read_text(encoding="utf-8")
        self.assertIn("def product_version() -> str:", window_source)
        self.assertIn('return "2.0.92"', window_source)

    def test_visible_title_and_package_names_have_no_candidate_wording(self) -> None:
        window_source = (ROOT / "main_window.py").read_text(encoding="utf-8")
        spec_source = (ROOT / "mercado_discount_manager_pyside.spec").read_text(encoding="utf-8")
        self.assertIn('setWindowTitle("美客多活动管家")', window_source)
        self.assertNotIn("候选版", window_source)
        self.assertNotIn("候选版", spec_source)
        self.assertNotIn("PySide6候选", spec_source)
        self.assertIn('name="美客多活动管家"', spec_source)

    def test_diagnostic_switches_are_not_visible_in_normal_main_window(self) -> None:
        window_source = (ROOT / "main_window.py").read_text(encoding="utf-8")
        self.assertNotIn("--keyboard-smoke", window_source)
        self.assertNotIn("--smoke-service", window_source)

    def test_real_write_confirmation_tokens_are_unchanged(self) -> None:
        payload = execution_payload(
            account_id="A1",
            action="update",
            filters=build_filters("MLM", "", ""),
            store_name="测试店",
            site_name_text="墨西哥站",
            seller_discount=6,
            official_discount=7,
            read_concurrency=2,
            activity_concurrency=2,
            write_concurrency=5,
        )
        self.assertEqual(payload["confirmText"], "REAL_SUBMIT")
        window_source = (ROOT / "main_window.py").read_text(encoding="utf-8")
        self.assertIn('commit_body["createConfirmText"] = "CREATE_SELLER_CAMPAIGN"', window_source)

    def test_release_spec_uses_pure_python_engine_without_node_binary(self) -> None:
        spec_source = (ROOT / "mercado_discount_manager_pyside.spec").read_text(encoding="utf-8")
        self.assertNotIn("standalone", spec_source.lower())
        self.assertNotIn("node.exe", spec_source)
        self.assertIn("runtime-staging", spec_source)
        self.assertIn('reason_text.py', spec_source)
        self.assertIn('"app/desktop-pyside"', spec_source)

    def test_cross_platform_build_scripts_and_github_workflow_exist(self) -> None:
        build_mac = (ROOT.parent / "scripts" / "build-macos.py").read_text(encoding="utf-8")
        build_win = (ROOT.parent / "scripts" / "build-windows.py").read_text(encoding="utf-8")
        workflow = (ROOT.parent / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        for script_src in (build_mac, build_win):
            self.assertIn("release-manifest.json", script_src)
            self.assertIn("--smoke-service", script_src)
            for field in ("display_name", "version", "file_count", "total_bytes", "exe_sha256", "protocol_version", "build_fingerprint"):
                self.assertIn(field, script_src)
        self.assertIn("windows-latest", workflow)
        self.assertIn("macos-latest", workflow)
        self.assertIn("scripts/build-windows.py", workflow)
        self.assertIn("scripts/build-macos.py", workflow)


if __name__ == "__main__":
    unittest.main()
