from __future__ import annotations

import io
import json
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.updater import (
    ReleaseInfo,
    check_github_latest_release,
    download_release_archive,
    is_newer_version,
    launch_in_place_update,
    parse_version,
    verify_and_extract_update,
)


class UpdaterEngineUnitTests(unittest.TestCase):
    def test_parse_version(self) -> None:
        self.assertEqual(parse_version("v2.0.82"), (2, 0, 82))
        self.assertEqual(parse_version("2.0.82.1"), (2, 0, 82, 1))
        self.assertEqual(parse_version("v3"), (3,))
        self.assertEqual(parse_version(""), (0, 0, 0))
        self.assertEqual(parse_version("invalid_text"), (0, 0, 0))

    def test_is_newer_version(self) -> None:
        self.assertTrue(is_newer_version("2.0.83", "2.0.82"))
        self.assertTrue(is_newer_version("v2.1.0", "2.0.82"))
        self.assertTrue(is_newer_version("3.0.0", "2.0.82"))
        self.assertTrue(is_newer_version("2.0.82.1", "2.0.82"))

        self.assertFalse(is_newer_version("2.0.82", "2.0.82"))
        self.assertFalse(is_newer_version("2.0.81", "2.0.82"))
        self.assertFalse(is_newer_version("2.0.82", "2.0.82.1"))
        self.assertFalse(is_newer_version("1.9.99", "2.0.82"))

    def test_check_github_latest_release_matching_platform(self) -> None:
        is_mac = sys.platform == "darwin"
        mac_asset = {
            "name": "MercadoDiscountManager-2.1.04-macOS-arm64-20261007.zip",
            "browser_download_url": "https://github.com/mock/mac.zip",
            "size": 52428800,
        }
        win_asset = {
            "name": "MercadoDiscountManager-2.1.04-Windows-x64-20261007.zip",
            "browser_download_url": "https://github.com/mock/win.zip",
            "size": 62914560,
        }

        mock_payload = {
            "tag_name": "v2.1.04",
            "body": "### 更新内容\n- 修复商品清理偶发问题\n- 自动更新功能上线",
            "published_at": "2026-10-07T00:00:00Z",
            "assets": [mac_asset, win_asset],
        }

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_payload).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            info = check_github_latest_release("2.1.03")

        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(info.version, "2.1.04")
        self.assertTrue(info.is_newer)
        self.assertIn("自动更新功能上线", info.release_notes)
        if is_mac:
            self.assertEqual(info.download_url, "https://github.com/mock/mac.zip")
            self.assertEqual(info.asset_size, 52428800)
        else:
            self.assertEqual(info.download_url, "https://github.com/mock/win.zip")
            self.assertEqual(info.asset_size, 62914560)

    def test_check_github_latest_release_network_failure(self) -> None:
        with patch("urllib.request.urlopen", side_effect=OSError("Network error")):
            info = check_github_latest_release("2.1.03")
        self.assertIsNone(info)

    def test_download_release_archive_success_and_callbacks(self) -> None:
        chunks = [b"chunk1_", b"chunk2_", b"chunk3"]
        total_len = sum(len(c) for c in chunks)

        mock_resp = MagicMock()
        mock_resp.headers.get.return_value = str(total_len)
        mock_resp.read.side_effect = chunks + [b""]
        mock_resp.__enter__.return_value = mock_resp

        progress_calls: list[tuple[int, int]] = []

        def on_prog(downloaded: int, total: int) -> None:
            progress_calls.append((downloaded, total))

        def fake_urlopen(req, *args, **kwargs):
            req_url = req.full_url if hasattr(req, "full_url") else str(req)
            if "archive.zip" in req_url:
                return mock_resp
            dummy = MagicMock()
            dummy.read.return_value = b"{}"
            dummy.__enter__.return_value = dummy
            return dummy

        with tempfile.TemporaryDirectory() as tmp_dir:
            dest_file = Path(tmp_dir) / "test_download.zip"
            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                ok = download_release_archive(
                    url="https://github.com/mock/archive.zip",
                    dest_path=dest_file,
                    on_progress=on_prog,
                )

            self.assertTrue(ok)
            self.assertTrue(dest_file.exists())
            self.assertEqual(dest_file.read_bytes(), b"chunk1_chunk2_chunk3")
            self.assertTrue(len(progress_calls) >= 3)
            self.assertEqual(progress_calls[-1], (total_len, total_len))

    def test_download_release_archive_stop_event_cancels(self) -> None:
        stop_event = threading.Event()
        stop_event.set()

        with tempfile.TemporaryDirectory() as tmp_dir:
            dest_file = Path(tmp_dir) / "cancelled.zip"
            ok = download_release_archive(
                url="https://github.com/mock/archive.zip",
                dest_path=dest_file,
                stop_event=stop_event,
            )
            self.assertFalse(ok)
            self.assertFalse(dest_file.exists())

    def test_verify_and_extract_valid_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = Path(tmp_dir) / "valid.zip"
            extract_dir = Path(tmp_dir) / "extracted"

            is_mac = sys.platform == "darwin"
            file_in_zip = "美客多活动管家.app/Contents/Info.plist" if is_mac else "美客多活动管家.exe"

            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr(file_in_zip, "test-content")

            result_path = verify_and_extract_update(zip_path, extract_dir)
            self.assertTrue(result_path.exists())
            if is_mac:
                self.assertTrue(str(result_path).endswith(".app"))
            else:
                self.assertTrue(result_path.is_dir())

    def test_verify_and_extract_corrupted_archive_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            corrupt_path = Path(tmp_dir) / "corrupt.zip"
            corrupt_path.write_bytes(b"not a valid zip file content")
            extract_dir = Path(tmp_dir) / "extracted"

            with self.assertRaises(ValueError):
                verify_and_extract_update(corrupt_path, extract_dir)

    def test_verify_and_extract_zip_slip_attack_intercepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            slip_path = Path(tmp_dir) / "slip.zip"
            extract_dir = Path(tmp_dir) / "extracted"

            with zipfile.ZipFile(slip_path, "w") as zf:
                zf.writestr("../evil.sh", "echo evil")

            with self.assertRaises(ValueError) as ctx:
                verify_and_extract_update(slip_path, extract_dir)
            self.assertIn("非法相对路径", str(ctx.exception))

    def test_launch_in_place_update_spawns_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "fake_app"
            target.mkdir()

            with patch("subprocess.Popen") as mock_popen:
                launch_in_place_update(target)
                self.assertTrue(mock_popen.called)


if __name__ == "__main__":
    unittest.main()
