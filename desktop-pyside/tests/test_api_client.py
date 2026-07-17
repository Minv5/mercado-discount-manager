from __future__ import annotations

import json
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api_client import ApiClient, ApiError  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    requests: list[tuple[str, str, dict]] = []

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/delay-25-seconds":
            time.sleep(0.15)
        self._send(200, {"ok": True, "path": self.path})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        self.requests.append(("POST", self.path, body))
        if self.path == "/fail":
            self._send(409, {"ok": False, "error": "业务阻断"})
        else:
            self._send(202, {"ok": True, "body": body})

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _send(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionAbortedError):
            pass


class ApiClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.client = ApiClient(f"http://127.0.0.1:{cls.server.server_port}")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def test_get_and_post_json(self) -> None:
        self.assertTrue(self.client.get("/health")["ok"])
        result = self.client.post("/echo", {"中文": "正常"})
        self.assertEqual(result["body"]["中文"], "正常")

    def test_business_error_keeps_status_and_chinese_message(self) -> None:
        with self.assertRaises(ApiError) as context:
            self.client.post("/fail", {})
        self.assertEqual(context.exception.status, 409)
        self.assertEqual(str(context.exception), "业务阻断")

    def test_25_second_poll_delay_is_classified_as_timeout(self) -> None:
        with self.assertRaises(ApiError) as context:
            self.client.get("/delay-25-seconds", timeout=0.02)
        self.assertEqual(context.exception.kind, "timeout")
        self.assertTrue(context.exception.retryable)
        self.assertIn("等待", str(context.exception))
        self.assertNotIn("组件暂时不可用", str(context.exception))

    def test_prepare_poll_timeout_uses_operation_specific_message(self) -> None:
        with self.assertRaises(ApiError) as context:
            self.client.get(
                "/delay-25-seconds", timeout=0.02,
                timeout_message="准备进度查询延迟，后台仍在核对范围。",
            )
        self.assertEqual(context.exception.kind, "timeout")
        self.assertEqual(str(context.exception), "准备进度查询延迟，后台仍在核对范围。")

    def test_connection_refused_is_not_reported_as_timeout(self) -> None:
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError(ConnectionRefusedError(10061, "refused"))):
            with self.assertRaises(ApiError) as context:
                self.client.get("/health", timeout=0.1)
        self.assertEqual(context.exception.kind, "connection")
        self.assertTrue(context.exception.retryable)
        self.assertIn("连接", str(context.exception))


if __name__ == "__main__":
    unittest.main()
