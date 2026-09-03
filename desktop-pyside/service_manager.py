from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from callback_endpoints import (
    DEFAULT_ACTIVITY_CALLBACK_ACK_URL,
    DEFAULT_ACTIVITY_CALLBACK_CLAIM_URL,
    LEGACY_ACTIVITY_CALLBACK_ACK_URLS,
    LEGACY_ACTIVITY_CALLBACK_CLAIM_URLS,
    migrate_callback_endpoint,
)
from log_rotation import rotate_log


CREATE_NO_WINDOW = 0x08000000


class ServiceError(RuntimeError):
    pass


class NodeServiceManager:
    PORT = 28758
    HEALTH_URL = "http://127.0.0.1:28758/api/health"
    PRODUCT = "mercado-discount-manager"
    PROTOCOL_VERSION = "3"
    HARD_START_TIMEOUT_SECONDS = 120.0

    def __init__(self, project_root: Path | None = None):
        self.project_root = project_root or Path(__file__).resolve().parents[1]
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        configured_data = os.environ.get("MDM_DATA_DIR")
        self.data_dir = Path(configured_data) if configured_data else local / "MercadoDiscountManagerStandalone" / "data"
        configured_auth = os.environ.get("ML_STANDALONE_AUTH_DIR")
        self.auth_dir = Path(configured_auth) if configured_auth else Path.home() / "Documents" / "美客多授权"
        self.log_dir = self.data_dir / "logs"
        self.process: subprocess.Popen[str] | None = None
        self._log_handles: list[object] = []
        self._start_lock = threading.Lock()
        self._startup_started_at: float | None = None
        # Injectable clock/sleeper keep the bounded wait logic deterministic in
        # offline tests without starting a product process.
        self._clock = time.monotonic
        self._sleep = time.sleep

    @property
    def owns_process(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def is_healthy(self, timeout: float = 1.0) -> bool:
        payload = self._read_health(timeout)
        return payload is not None and self._health_contract_error(payload) is None

    def _read_health(self, timeout: float = 1.0) -> dict[str, object] | None:
        try:
            with urllib.request.urlopen(self.HEALTH_URL, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if response.status != 200 or not isinstance(payload, dict):
                    return {"_invalid_contract": True}
                return payload
        except ValueError:
            return {"_invalid_contract": True}
        except (OSError, urllib.error.URLError):
            return None

    def _health_contract_error(self, payload: dict[str, object]) -> str | None:
        if not payload.get("ok"):
            return "端口上的服务未通过健康检查，无法确认是本软件组件。"
        product = str(payload.get("product") or "")
        protocol = str(payload.get("protocol_version") or "")
        fingerprint = str(payload.get("build_fingerprint") or "")
        if not product or not protocol or not fingerprint:
            return "端口上的服务缺少产品协议标识，无法确认是本软件组件。"
        if product != self.PRODUCT:
            return "端口上的服务不是本软件组件，已停止启动且不会结束该进程。"
        if protocol != self.PROTOCOL_VERSION:
            return "本软件组件协议不兼容，请先关闭占用该端口的旧版本。"
        return None

    def ensure_started(
        self,
        wait_seconds: float = 30.0,
        *,
        hard_timeout_seconds: float | None = None,
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> bool:
        """Start or reuse the local service with a bounded late-health grace.

        ``wait_seconds`` is a soft observation threshold.  If the owned child
        is still alive, the manager keeps the same process and polls until the
        hard deadline instead of killing a service that is still initializing.
        The lock makes concurrent callers share one startup attempt and avoids
        spawning a second Node process.
        """
        soft_timeout = max(0.0, float(wait_seconds))
        hard_timeout = float(
            self.HARD_START_TIMEOUT_SECONDS
            if hard_timeout_seconds is None
            else hard_timeout_seconds
        )
        if hard_timeout < soft_timeout:
            hard_timeout = soft_timeout
        with self._start_lock:
            return self._ensure_started_locked(
                soft_timeout,
                hard_timeout,
                progress_callback,
            )

    def _ensure_started_locked(
        self,
        soft_timeout: float,
        hard_timeout: float,
        progress_callback: Callable[[dict[str, object]], None] | None,
    ) -> bool:
        existing = self._read_health()
        if existing is not None:
            error = self._health_contract_error(existing)
            if error is None:
                self._report_progress(progress_callback, "reused", 0.0)
                return False
            raise ServiceError(error)

        if self.process is not None and self.process.poll() is not None:
            self._close_log_handles()
            self.process = None
            self._startup_started_at = None
            raise ServiceError("程序组件启动后异常退出。")
        owned_process = self.process if self.owns_process else None
        if owned_process is None:
            if self._port_owner_pid() is not None:
                raise ServiceError("程序组件端口已被其它进程占用，无法安全启动软件。")
            node_exe, app_dir = self._runtime_paths()
            server_js = app_dir / "src" / "server.js"
            if not node_exe.exists() or not server_js.exists():
                raise ServiceError("候选包缺少 Node 或业务服务文件。")
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.log_dir.mkdir(parents=True, exist_ok=True)
            out_path = self.log_dir / "pyside-server.out.log"
            err_path = self.log_dir / "pyside-server.err.log"
            rotate_log(out_path)
            rotate_log(err_path)
            out_handle = open(out_path, "a", encoding="utf-8")
            err_handle = open(err_path, "a", encoding="utf-8")
            self._log_handles = [out_handle, err_handle]
            env = os.environ.copy()
            env["MDM_DATA_DIR"] = str(self.data_dir)
            env["ML_STANDALONE_AUTH_DIR"] = str(self.auth_dir)
            self._apply_webhook_env(env)
            self.process = subprocess.Popen(
                [str(node_exe), "src/server.js"],
                cwd=str(app_dir),
                env=env,
                stdout=out_handle,
                stderr=err_handle,
                text=True,
                creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self._startup_started_at = self._clock()
            self._report_progress(progress_callback, "launching", 0.0)
        else:
            if self._startup_started_at is None:
                self._startup_started_at = self._clock()
            self._report_progress(progress_callback, "reusing_owned_process", 0.0)

        started_at = self._startup_started_at if self._startup_started_at is not None else self._clock()
        soft_reported = False
        while True:
            elapsed = max(0.0, self._clock() - started_at)
            if elapsed >= hard_timeout:
                self._report_progress(progress_callback, "failed", elapsed, "hard_timeout")
                self.stop()
                raise ServiceError("程序组件启动超过有界等待时间，请关闭后重试。")

            if not soft_reported and elapsed >= soft_timeout:
                soft_reported = True
                self._report_progress(progress_callback, "soft_timeout", elapsed, "health_pending")

            payload = self._read_health()
            if payload is not None:
                error = self._health_contract_error(payload)
                if error is None:
                    self._report_progress(progress_callback, "ready", elapsed)
                    self._startup_started_at = None
                    return True
                self.stop()
                self._report_progress(progress_callback, "failed", elapsed, "health_contract")
                raise ServiceError(error)

            process = self.process
            if process is None or process.poll() is not None:
                self._close_log_handles()
                self.process = None
                self._startup_started_at = None
                self._report_progress(progress_callback, "failed", elapsed, "process_exit")
                raise ServiceError("程序组件启动后异常退出。")

            self._sleep(0.25)

    def _report_progress(
        self,
        callback: Callable[[dict[str, object]], None] | None,
        state: str,
        elapsed: float,
        cause_code: str = "",
    ) -> None:
        if callback is None:
            return
        payload: dict[str, object] = {
            "phase": "service_connect",
            "state": state,
            "elapsed_ms": int(max(0.0, elapsed) * 1000),
        }
        if cause_code:
            payload["cause_code"] = cause_code
        try:
            callback(payload)
        except Exception:
            # Progress is observability only and must never change the startup
            # safety decision or cause a duplicate process attempt.
            return

    def stop(self) -> None:
        process = self.process
        self.process = None
        self._startup_started_at = None
        if process is not None and process.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=CREATE_NO_WINDOW,
                    check=False,
                )
            else:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        self._close_log_handles()

    def detach(self) -> None:
        """Release ownership while leaving an active compatible service running."""
        self.process = None
        self._close_log_handles()

    def _apply_webhook_env(self, env: dict[str, str]) -> None:
        """Inject activity callback (webhook receiver) settings from settings.json."""
        try:
            with open(self.data_dir / "settings.json", encoding="utf-8") as handle:
                settings = json.load(handle)
        except (OSError, ValueError):
            return
        enabled = settings.get("activityCallbackEnabled") is True
        if not enabled:
            env.pop("MDM_ACTIVITY_CALLBACK_ENABLED", None)
            env.pop("MDM_ACTIVITY_CALLBACK_APPLICATION_ID", None)
            env.pop("MDM_ACTIVITY_CALLBACK_SECRET_FILE", None)
            env.pop("MDM_ACTIVITY_CLAIM_ENABLED", None)
            env.pop("MDM_ACTIVITY_CLAIM_APPLICATION_ID", None)
            env.pop("MDM_ACTIVITY_CLAIM_SECRET_FILE", None)
            env.pop("MDM_ACTIVITY_CLAIM_URL", None)
            env.pop("MDM_ACTIVITY_CLAIM_ACK_URL", None)
            return
        application_id = str(settings.get("activityCallbackApplicationId") or "").strip()
        secret_file = str(settings.get("activityCallbackSecretFile") or "").strip()
        claim_url = migrate_callback_endpoint(
            settings.get("activityCallbackClaimUrl"),
            DEFAULT_ACTIVITY_CALLBACK_CLAIM_URL,
            LEGACY_ACTIVITY_CALLBACK_CLAIM_URLS,
        )
        ack_url = migrate_callback_endpoint(
            settings.get("activityCallbackAckUrl"),
            DEFAULT_ACTIVITY_CALLBACK_ACK_URL,
            LEGACY_ACTIVITY_CALLBACK_ACK_URLS,
        )
        env["MDM_ACTIVITY_CALLBACK_ENABLED"] = "1"
        env["MDM_ACTIVITY_CLAIM_ENABLED"] = "1"
        if application_id:
            env["MDM_ACTIVITY_CALLBACK_APPLICATION_ID"] = application_id
            env["MDM_ACTIVITY_CLAIM_APPLICATION_ID"] = application_id
        if secret_file:
            env["MDM_ACTIVITY_CALLBACK_SECRET_FILE"] = secret_file
            env["MDM_ACTIVITY_CLAIM_SECRET_FILE"] = secret_file
        if claim_url:
            env["MDM_ACTIVITY_CLAIM_URL"] = claim_url
        if ack_url:
            env["MDM_ACTIVITY_CLAIM_ACK_URL"] = ack_url

    def _close_log_handles(self) -> None:
        for handle in self._log_handles:
            try:
                handle.flush()
                handle.close()
            except OSError:
                pass
        self._log_handles.clear()

    def _runtime_paths(self) -> tuple[Path, Path]:
        bundle_root = Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "frozen", False) else None
        if bundle_root:
            return bundle_root / "node" / "node.exe", bundle_root / "app"
        node = shutil.which("node")
        if not node:
            raise ServiceError("未找到项目已使用的 Node 运行组件。")
        return Path(node), self.project_root

    def _port_owner_pid(self) -> int | None:
        if os.name != "nt":
            return None
        result = subprocess.run(
            ["netstat.exe", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
            check=False,
        )
        suffix = f":{self.PORT}"
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[1].endswith(suffix) and parts[3].upper() == "LISTENING":
                try:
                    return int(parts[4])
                except ValueError:
                    return None
        return None

    def __enter__(self) -> "NodeServiceManager":
        self.ensure_started()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()
