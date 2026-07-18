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
from pathlib import Path

from log_rotation import rotate_log


CREATE_NO_WINDOW = 0x08000000


class ServiceError(RuntimeError):
    pass


class NodeServiceManager:
    PORT = 28758
    HEALTH_URL = "http://127.0.0.1:28758/api/health"
    PRODUCT = "mercado-discount-manager"
    PROTOCOL_VERSION = "3"

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

    def ensure_started(self, wait_seconds: float = 30.0) -> bool:
        existing = self._read_health()
        if existing is not None:
            error = self._health_contract_error(existing)
            if error is None:
                return False
            raise ServiceError(error)
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
        self.process = subprocess.Popen(
            [str(node_exe), "src/server.js"],
            cwd=str(app_dir),
            env=env,
            stdout=out_handle,
            stderr=err_handle,
            text=True,
            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            payload = self._read_health()
            if payload is not None:
                error = self._health_contract_error(payload)
                if error is None:
                    return True
                self.stop()
                raise ServiceError(error)
            if self.process.poll() is not None:
                raise ServiceError("程序组件启动后异常退出。")
            time.sleep(0.25)
        self.stop()
        raise ServiceError("程序组件启动时间过长，请关闭后重试。")

    def stop(self) -> None:
        process = self.process
        self.process = None
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
