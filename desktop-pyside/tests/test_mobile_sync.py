# -*- coding: utf-8 -*-
"""Unit tests for automatic silent mobile synchronizer."""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.mobile_sync import (
    MobileGatewaySynchronizer,
    full_sync_from_disk_async,
    trigger_mobile_sync,
)


class TestMobileSync(unittest.TestCase):
    def setUp(self) -> None:
        self.sync = MobileGatewaySynchronizer(
            primary_url="http://127.0.0.1:28760/api/sync",
            fallback_url="https://minv5.trade/api/sync",
            debounce_seconds=0.1,
        )

    def tearDown(self) -> None:
        if self.sync._timer:
            self.sync._timer.cancel()

    def test_post_sync_primary_success(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            ok = self.sync._post_sync({"sessions": [{"id": "s1"}]})
            self.assertTrue(ok)
            mock_urlopen.assert_called_once()
            req = mock_urlopen.call_args[0][0]
            self.assertEqual(req.full_url, "http://127.0.0.1:28760/api/sync")
            self.assertEqual(req.get_method(), "POST")

    def test_post_sync_fallback_when_primary_fails(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp

        def side_effect(req, timeout=3.0):
            if "127.0.0.1" in req.full_url:
                raise ConnectionRefusedError("Connection refused")
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=side_effect) as mock_urlopen:
            ok = self.sync._post_sync({"orders": [{"order_id": "o1"}]})
            self.assertTrue(ok)
            self.assertEqual(mock_urlopen.call_count, 2)
            first_url = mock_urlopen.call_args_list[0][0][0].full_url
            second_url = mock_urlopen.call_args_list[1][0][0].full_url
            self.assertIn("127.0.0.1", first_url)
            self.assertIn("minv5.trade", second_url)

    def test_post_sync_both_fail_no_exception(self) -> None:
        with patch("urllib.request.urlopen", side_effect=RuntimeError("Network down")):
            ok = self.sync._post_sync({"sessions": []})
            self.assertFalse(ok)

    def test_trigger_debounce_coalesces_payloads(self) -> None:
        dispatched_payloads = []

        def mock_post_sync(payload):
            dispatched_payloads.append(payload)
            return True

        self.sync._post_sync = mock_post_sync

        self.sync.trigger(sessions=[{"id": "1"}])
        self.sync.trigger(orders=[{"order_id": "100"}])
        self.sync.trigger(sessions=[{"id": "2"}])

        # Wait for debounce timer (0.1s)
        time.sleep(0.2)

        self.assertEqual(len(dispatched_payloads), 1)
        self.assertEqual(dispatched_payloads[0]["sessions"], [{"id": "2"}])
        self.assertEqual(dispatched_payloads[0]["orders"], [{"order_id": "100"}])

    def test_trigger_immediate_dispatches_rapidly(self) -> None:
        dispatched_payloads = []

        def mock_post_sync(payload):
            dispatched_payloads.append(payload)
            return True

        self.sync._post_sync = mock_post_sync
        self.sync.trigger(sessions=[{"id": "imm"}], immediate=True)

        time.sleep(0.1)
        self.assertEqual(len(dispatched_payloads), 1)
        self.assertEqual(dispatched_payloads[0]["sessions"], [{"id": "imm"}])

    def test_full_sync_from_disk_async(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_p = Path(tmp_dir)
            (tmp_p / "sessions_snapshot.json").write_text('[{"id": "s_disk"}]', encoding="utf-8")
            (tmp_p / "orders_snapshot.json").write_text('[{"order_id": "o_disk"}]', encoding="utf-8")

            with patch("engine.mobile_sync.get_data_dir", return_value=tmp_p):
                with patch("engine.mobile_sync.trigger_mobile_sync") as mock_trigger:
                    full_sync_from_disk_async(immediate=True)
                    time.sleep(0.1)
                    mock_trigger.assert_called_once()
                    kwargs = mock_trigger.call_args[1]
                    self.assertEqual(kwargs["sessions"], [{"id": "s_disk"}])
                    self.assertEqual(kwargs["orders"], [{"order_id": "o_disk"}])


if __name__ == "__main__":
    unittest.main()
