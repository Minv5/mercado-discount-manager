# -*- coding: utf-8 -*-
"""Access-control and write-gate tests for the mobile gateway, driven over real HTTP."""

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mobile_gateway

TOKEN = "unit-test-token"


class MobileGatewayAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        patcher = patch.object(mobile_gateway, "get_data_dir", return_value=self.data_dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        os.environ.pop(mobile_gateway.ACCESS_TOKEN_ENV, None)
        self.addCleanup(env.stop)

        (self.data_dir / "sessions_snapshot.json").write_text(
            json.dumps([{"id": "c_1", "session_type": "claim", "account_id": "A1", "stage": "claim", "status": "opened"}]),
            encoding="utf-8",
        )
        (self.data_dir / "orders_snapshot.json").write_text(json.dumps([{"order_id": "o1"}]), encoding="utf-8")

        self.server = mobile_gateway.create_server(port=0)
        self.server.server_address  # bound
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.tmp.cleanup)

    def _set_token(self) -> None:
        (self.data_dir / mobile_gateway.ACCESS_TOKEN_FILENAME).write_text(TOKEN, encoding="utf-8")

    def _call(self, path: str, body: dict | None = None, token: str | None = None) -> tuple[int, dict]:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read().decode("utf-8"))

    def _sessions_on_disk(self) -> list:
        return json.loads((self.data_dir / "sessions_snapshot.json").read_text(encoding="utf-8"))

    def test_status_is_public_but_leaks_nothing(self) -> None:
        code, body = self._call("/api/status")
        self.assertEqual(code, 200)
        self.assertNotIn("tunnel_url", body)

    def test_everything_refused_when_no_token_configured(self) -> None:
        for path in ("/api/orders", "/api/sessions", "/api/stores", "/api/overview", "/api/session_detail?session_id=c_1"):
            code, _ = self._call(path, token="anything")
            self.assertEqual(code, 503, path)
        code, _ = self._call("/api/sync", {"orders": []}, token="anything")
        self.assertEqual(code, 503)
        self.assertEqual(json.loads((self.data_dir / "orders_snapshot.json").read_text(encoding="utf-8")), [{"order_id": "o1"}])

    def test_missing_or_wrong_token_is_rejected(self) -> None:
        self._set_token()
        for token in (None, "wrong"):
            for path in ("/api/orders", "/api/sessions", "/api/auth_check"):
                code, _ = self._call(path, token=token)
                self.assertEqual(code, 401, (path, token))
            for path, body in (
                ("/api/reply", {"session_id": "c_1", "reply_text": "hi"}),
                ("/api/claim_action", {"session_id": "c_1", "action": "full-refund", "confirm": "REAL_SUBMIT"}),
                ("/api/sync", {"sessions": []}),
            ):
                code, _ = self._call(path, body, token=token)
                self.assertEqual(code, 401, (path, token))
        self.assertEqual(len(self._sessions_on_disk()), 1)

    def test_valid_token_reads_data(self) -> None:
        self._set_token()
        code, body = self._call("/api/orders", token=TOKEN)
        self.assertEqual(code, 200)
        self.assertEqual(body["total"], 1)
        self.assertEqual(self._call("/api/auth_check", token=TOKEN)[0], 200)

    def test_env_token_takes_precedence(self) -> None:
        self._set_token()
        os.environ[mobile_gateway.ACCESS_TOKEN_ENV] = "env-token"
        self.assertEqual(self._call("/api/orders", token=TOKEN)[0], 401)
        self.assertEqual(self._call("/api/orders", token="env-token")[0], 200)

    def test_claim_action_needs_explicit_action_and_confirmation(self) -> None:
        self._set_token()
        with patch.object(mobile_gateway, "execute_claim_action") as execute:
            code, _ = self._call("/api/claim_action", {"session_id": "c_1", "confirm": "REAL_SUBMIT"}, token=TOKEN)
            self.assertEqual(code, 400)
            code, _ = self._call("/api/claim_action", {"session_id": "c_1", "action": "full-refund"}, token=TOKEN)
            self.assertEqual(code, 400)
            execute.assert_not_called()
            execute.return_value = (True, "ok")
            code, body = self._call(
                "/api/claim_action", {"session_id": "c_1", "action": "full-refund", "confirm": "REAL_SUBMIT"}, token=TOKEN
            )
            self.assertEqual((code, body["success"]), (200, True))
            execute.assert_called_once()

    def test_failed_refund_reports_failure_and_keeps_local_state(self) -> None:
        client = MagicMock()
        client.submit_claim_full_refund.side_effect = RuntimeError("403")
        with patch("engine.client.MercadoClient", return_value=client), patch("engine.auth.AuthManager"):
            ok, _ = mobile_gateway.execute_claim_action("c_1", "full-refund")
        self.assertFalse(ok)
        self.assertEqual(self._sessions_on_disk()[0]["status"], "opened")

    def test_successful_refund_closes_local_claim(self) -> None:
        client = MagicMock()
        with patch("engine.client.MercadoClient", return_value=client), patch("engine.auth.AuthManager"):
            ok, _ = mobile_gateway.execute_claim_action("c_1", "full-refund")
        self.assertTrue(ok)
        client.submit_claim_full_refund.assert_called_once_with("A1", "1")
        self.assertEqual(self._sessions_on_disk()[0]["status"], "closed")

    def test_failed_reply_reports_failure_and_records_nothing(self) -> None:
        client = MagicMock()
        client.send_claim_message.side_effect = RuntimeError("timeout")
        with patch("engine.client.MercadoClient", return_value=client), patch("engine.auth.AuthManager"):
            ok, _ = mobile_gateway.send_real_reply("c_1", "hola")
        self.assertFalse(ok)
        self.assertNotIn("messages", self._sessions_on_disk()[0])

    def test_ensure_access_token_generates_once(self) -> None:
        first = mobile_gateway.ensure_access_token()
        self.assertGreaterEqual(len(first), 24)
        self.assertEqual(mobile_gateway.ensure_access_token(), first)


class MobileLinkOptInTests(unittest.TestCase):
    """The desktop opens the gateway and pushes snapshots only after the user opts in."""

    def _apply(self, window) -> None:
        from main_window import MainWindow

        MainWindow._apply_mobile_link_settings(window)

    def test_gateway_is_on_by_default_and_follows_the_setting(self) -> None:
        from types import SimpleNamespace
        import engine.mobile_sync as mobile_sync

        self.addCleanup(mobile_sync.set_mobile_sync_enabled, False)
        window = SimpleNamespace(settings={}, _mobile_gateway_server=None)
        fake_server = MagicMock()
        with patch.object(mobile_gateway, "create_server", return_value=fake_server) as create, \
                patch.object(mobile_gateway, "ensure_access_token", return_value="t") as ensure, \
                patch.object(mobile_sync, "full_sync_from_disk_async"):
            self._apply(window)
            self._apply(window)
            create.assert_called_once_with(port=28760)
            ensure.assert_called_once()
            self.assertTrue(mobile_sync._sync_enabled)
            self.assertIs(window._mobile_gateway_server, fake_server)

            window.settings = {"mobileGatewayEnabled": False}
            self._apply(window)
            self.assertIsNone(window._mobile_gateway_server)
            self.assertFalse(mobile_sync._sync_enabled)
            for _ in range(50):
                if fake_server.shutdown.called:
                    break
                threading.Event().wait(0.02)
            fake_server.shutdown.assert_called_once()


if __name__ == "__main__":
    unittest.main()
