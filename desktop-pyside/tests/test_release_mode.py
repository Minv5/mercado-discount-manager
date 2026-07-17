from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import build_filters, execution_payload  # noqa: E402


class ReleaseModeTests(unittest.TestCase):
    def test_visible_title_and_package_names_have_no_candidate_wording(self) -> None:
        window_source = (ROOT / "main_window.py").read_text(encoding="utf-8")
        spec_source = (ROOT / "mercado_discount_manager_pyside.spec").read_text(encoding="utf-8")
        self.assertIn('setWindowTitle("美客多活动助手")', window_source)
        self.assertNotIn("候选版", window_source)
        self.assertNotIn("候选版", spec_source)
        self.assertNotIn("PySide6候选", spec_source)
        self.assertIn('name="美客多活动助手"', spec_source)

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

    def test_release_uses_verified_independent_node_staging(self) -> None:
        spec_source = (ROOT / "mercado_discount_manager_pyside.spec").read_text(encoding="utf-8")
        build_source = (ROOT / "build-release.ps1").read_text(encoding="utf-8")
        self.assertNotIn("standalone", spec_source.lower())
        self.assertNotIn("PayloadWork", spec_source)
        self.assertIn("runtime-staging", spec_source)
        self.assertIn("node-runtime.lock.json", build_source)
        self.assertIn("SHA256", build_source)

    def test_release_generates_manifest_and_product_installer(self) -> None:
        build_source = (ROOT / "build-release.ps1").read_text(encoding="utf-8")
        installer = (ROOT / "install-release.ps1").read_text(encoding="utf-8")
        self.assertIn("release-manifest.json", build_source)
        for field in ("display_name", "file_count", "total_bytes", "exe_sha256", "protocol_version", "build_fingerprint"):
            self.assertIn(field, build_source)
        self.assertIn("active", installer.lower())
        self.assertIn("backup", installer.lower())
        self.assertIn("rollback", installer.lower())
        self.assertIn("--keyboard-smoke", installer)
        self.assertIn("--smoke-service", installer)

    def test_installer_defaults_to_current_user_start_menu_without_desktop_policy(self) -> None:
        installer = (ROOT / "install-release.ps1").read_text(encoding="utf-8")
        self.assertIn("GetFolderPath('Programs')", installer)
        self.assertNotIn("GetFolderPath('Desktop')", installer)
        self.assertNotIn("DesktopDirectory", installer)
        self.assertIn("0x6D3B,0x52A8,0x52A9,0x624B", installer)
        self.assertIn("Remove-LegacyStartMenuShortcut", installer)

    def test_installer_resolves_default_candidate_after_script_root_is_available(self) -> None:
        installer = (ROOT / "install-release.ps1").read_text(encoding="utf-8")
        self.assertRegex(installer, r"\[string\]\$CandidateRoot\s*=\s*\$null")
        self.assertIn("if ([string]::IsNullOrWhiteSpace($CandidateRoot))", installer)

    def test_installer_blocks_active_submission_prepare_as_well_as_jobs_and_groups(self) -> None:
        installer = (ROOT / "install-release.ps1").read_text(encoding="utf-8")
        self.assertIn("/api/execution/submissions/active", installer)
        self.assertIn("$activeSubmission.active", installer)

    def test_release_json_is_bom_free_and_installer_keeps_current_backup_by_identity(self) -> None:
        build_source = (ROOT / "build-release.ps1").read_text(encoding="utf-8")
        installer = (ROOT / "install-release.ps1").read_text(encoding="utf-8")
        contract = (ROOT.parent / "src" / "productContract.js").read_text(encoding="utf-8")
        self.assertIn("UTF8Encoding]::new($false)", build_source)
        self.assertIn("replace(/^\\uFEFF/", contract)
        self.assertIn("$_.FullName -ne $backup", installer)
        self.assertNotIn("Sort-Object LastWriteTime -Descending", installer)
        self.assertIn("Copy-Item -LiteralPath $backup -Destination $install -Recurse", installer)

    def test_release_protocol_comes_from_product_contract(self) -> None:
        build_source = (ROOT / "build-release.ps1").read_text(encoding="utf-8")
        installer = (ROOT / "install-release.ps1").read_text(encoding="utf-8")
        for source in (build_source, installer):
            self.assertIn("src\\productContract.js", source)
            self.assertIn("PROTOCOL_VERSION", source)
            self.assertNotRegex(source, r"\$ProtocolVersion\s*=\s*['\"]\d+['\"]")

    def test_validation_defaults_to_pyside_and_legacy_is_explicit(self) -> None:
        validate = (ROOT.parent / "scripts" / "validate.ps1").read_text(encoding="utf-8")
        self.assertIn("[string]$PackageTarget = 'PySide'", validate)
        self.assertIn("'Legacy'", validate)
        self.assertNotIn("[string]$PackageTarget = 'Standalone'", validate)
        legacy_gate = validate.index("if ($PackageTarget -in @('Legacy','Both'))")
        dotnet_build = validate.index("standalone\\MercadoDiscountManager.Standalone.csproj")
        self.assertLess(legacy_gate, dotnet_build)


if __name__ == "__main__":
    unittest.main()
