from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


class ApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        status: int = 0,
        payload: dict[str, Any] | None = None,
        *,
        kind: str = "http",
        retryable: bool = False,
    ):
        super().__init__(message)
        self.status = status
        self.payload = payload or {}
        self.kind = kind
        self.retryable = retryable


@dataclass
class ApiClient:
    base_url: str = "http://127.0.0.1:28758"
    timeout: float = 20.0

    def get(self, path: str, *, timeout: float | None = None, timeout_message: str | None = None) -> dict[str, Any]:
        return self.request("GET", path, timeout=timeout, timeout_message=timeout_message)

    def post(self, path: str, body: dict[str, Any] | None = None, *, timeout: float | None = None, timeout_message: str | None = None) -> dict[str, Any]:
        return self.request("POST", path, body or {}, timeout=timeout, timeout_message=timeout_message)

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        timeout_message: str | None = None,
    ) -> dict[str, Any]:
        url = self.base_url.rstrip("/") + "/" + path.lstrip("/")
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                return self._decode(raw, response.status)
        except urllib.error.HTTPError as error:
            raw = error.read().decode("utf-8", errors="replace")
            payload = self._safe_decode(raw)
            message = str(payload.get("error") or payload.get("message") or f"请求未完成（{error.code}）")
            raise ApiError(message, error.code, payload, kind="http", retryable=error.code >= 500) from error
        except (TimeoutError, socket.timeout) as error:
            if getattr(error, "winerror", None) in {10061, 10065} or getattr(error, "errno", None) in {61, 111}:
                raise ApiError("程序组件连接中断，正在重新连接。", kind="connection", retryable=True) from error
            raise ApiError(timeout_message or "进度查询等待时间较长，任务仍在执行。", kind="timeout", retryable=True) from error
        except urllib.error.URLError as error:
            reason = error.reason
            if isinstance(reason, ConnectionRefusedError) or getattr(reason, "winerror", None) in {10061, 10065} or getattr(reason, "errno", None) in {61, 111}:
                raise ApiError("程序组件连接中断，正在重新连接。", kind="connection", retryable=True) from error
            if isinstance(reason, (TimeoutError, socket.timeout)):
                raise ApiError(timeout_message or "进度查询等待时间较长，任务仍在执行。", kind="timeout", retryable=True) from error
            raise ApiError("程序组件连接中断，正在重新连接。", kind="connection", retryable=True) from error
        except OSError as error:
            raise ApiError("程序组件连接中断，正在重新连接。", kind="connection", retryable=True) from error

    @staticmethod
    def _safe_decode(raw: str) -> dict[str, Any]:
        try:
            value = json.loads(raw or "{}")
            return value if isinstance(value, dict) else {"data": value}
        except json.JSONDecodeError:
            return {"message": raw.strip()}

    @classmethod
    def _decode(cls, raw: str, status: int) -> dict[str, Any]:
        payload = cls._safe_decode(raw)
        if status >= 400:
            raise ApiError(str(payload.get("error") or payload.get("message") or "请求未完成。"), status, payload)
        return payload

    @staticmethod
    def query(path: str, **values: Any) -> str:
        clean = {key: value for key, value in values.items() if value is not None and value != ""}
        return path + ("?" + urllib.parse.urlencode(clean) if clean else "")
