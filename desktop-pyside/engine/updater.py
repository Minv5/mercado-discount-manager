from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


def parse_version(ver_str: str) -> tuple[int, ...]:
    """将版本号字符串（如 'v2.0.81'、'2.0.81.1'）转换为整数元组以进行严格比对。"""
    cleaned = str(ver_str or "").strip().lstrip("vV")
    matched = re.findall(r"\d+", cleaned)
    if not matched:
        return (0, 0, 0)
    return tuple(int(x) for x in matched)


def is_newer_version(remote_ver: str, current_ver: str) -> bool:
    """判定远程版本号是否严格大于当前版本号。"""
    r_parts = parse_version(remote_ver)
    c_parts = parse_version(current_ver)
    # 补齐长度以对齐比较
    max_len = max(len(r_parts), len(c_parts), 3)
    r_padded = r_parts + (0,) * (max_len - len(r_parts))
    c_padded = c_parts + (0,) * (max_len - len(c_parts))
    return r_padded > c_padded


@dataclass
class ReleaseInfo:
    tag_name: str
    version: str
    release_notes: str
    is_newer: bool
    asset_name: str
    download_url: str
    asset_size: int
    published_at: str


def check_github_latest_release(
    current_version: str,
    repo: str = "Minv5/mercado-discount-manager",
    timeout: float = 10.0,
) -> ReleaseInfo | None:
    """异步向 GitHub Releases API 查询最新正式版并解析适合当前操作系统的安装包。"""
    api_url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(
        api_url,
        headers={
            "User-Agent": "MercadoDiscountManager-Updater",
            "Accept": "application/vnd.github.v3+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    tag_name = str(data.get("tag_name") or "").strip()
    remote_version = tag_name.lstrip("vV")
    if not remote_version:
        return None

    newer = is_newer_version(remote_version, current_version)
    body = str(data.get("body") or "").strip()
    published_at = str(data.get("published_at") or "")

    # 根据当前操作系统匹配产物名称
    # macOS: *-macOS-arm64-*.zip
    # Windows: *-Windows-x64-*.zip
    is_mac = sys.platform == "darwin"
    target_pattern = r"-macOS-arm64-.*\.zip$" if is_mac else r"-Windows-x64-.*\.zip$"

    assets = data.get("assets") or []
    matched_asset_name = ""
    matched_url = ""
    matched_size = 0

    for a in assets:
        name = str(a.get("name") or "")
        if re.search(target_pattern, name, re.IGNORECASE):
            matched_asset_name = name
            matched_url = str(a.get("browser_download_url") or "")
            matched_size = int(a.get("size") or 0)
            break

    if not matched_url and assets:
        # 兜底匹配任意对应平台的 zip
        keyword = "macos" if is_mac else "windows"
        for a in assets:
            name = str(a.get("name") or "").lower()
            if keyword in name and name.endswith(".zip"):
                matched_asset_name = a.get("name")
                matched_url = str(a.get("browser_download_url") or "")
                matched_size = int(a.get("size") or 0)
                break

    return ReleaseInfo(
        tag_name=tag_name,
        version=remote_version,
        release_notes=body,
        is_newer=newer,
        asset_name=matched_asset_name,
        download_url=matched_url,
        asset_size=matched_size,
        published_at=published_at,
    )


def download_release_archive(
    url: str,
    dest_path: Path,
    on_progress: Callable[[int, int], None] | None = None,
    stop_event: threading.Event | None = None,
    timeout: float = 30.0,
) -> bool:
    """流式下载更新包，支持进度回调、网络断流中断检测与国内加速代理自动兜底。"""
    if not url:
        return False

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_download = dest_path.with_suffix(".downloading")

    # 尝试直连，直连失败则自动使用备用加速通道
    urls_to_try = [url, f"https://ghproxy.net/{url}"]

    for attempt_url in urls_to_try:
        if stop_event and stop_event.is_set():
            return False

        req = urllib.request.Request(
            attempt_url,
            headers={"User-Agent": "MercadoDiscountManager-Updater"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                total_bytes = int(resp.headers.get("Content-Length") or 0)
                downloaded = 0
                chunk_size = 65536  # 64KB

                with open(temp_download, "wb") as f:
                    while True:
                        if stop_event and stop_event.is_set():
                            if temp_download.exists():
                                temp_download.unlink(missing_ok=True)
                            return False

                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if on_progress:
                            on_progress(downloaded, total_bytes)

                if temp_download.exists() and temp_download.stat().st_size > 0:
                    temp_download.replace(dest_path)
                    return True
        except Exception:
            if temp_download.exists():
                temp_download.unlink(missing_ok=True)
            continue

    return False


def verify_and_extract_update(zip_path: Path, extract_dir: Path) -> Path:
    """验证 ZIP 压缩包完整性并解压，返回包含目标可执行文件或 .app 包的根路径。"""
    if not zip_path.exists():
        raise FileNotFoundError(f"下载的更新包不存在: {zip_path}")

    if not zipfile.is_zipfile(zip_path):
        raise ValueError("下载的文件并非合法的 ZIP 压缩包，可能已损坏")

    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        # 安全性检测：防止 Zip Slip 路径穿越
        for member in zf.namelist():
            target = extract_dir / member
            if not target.resolve().is_relative_to(extract_dir.resolve()):
                raise ValueError("压缩包内含有非法相对路径，已阻断解压")
        # 完整性自检
        corrupted = zf.testzip()
        if corrupted:
            raise ValueError(f"压缩包校验损坏，损坏文件: {corrupted}")
        zf.extractall(extract_dir)

    is_mac = sys.platform == "darwin"
    if is_mac:
        # 寻找解压出的 .app 目录
        apps = list(extract_dir.glob("*.app"))
        if not apps:
            apps = list(extract_dir.rglob("*.app"))
        if apps:
            return apps[0]
    else:
        # Windows 寻找包含 exe 的目录
        exes = list(extract_dir.glob("*.exe"))
        if exes:
            return extract_dir
        sub_exes = list(extract_dir.rglob("*.exe"))
        if sub_exes:
            return sub_exes[0].parent

    return extract_dir


def launch_in_place_update(extracted_target: Path) -> None:
    """双端跳板替换与自动重启主程序。"""
    is_mac = sys.platform == "darwin"
    current_pid = os.getpid()

    if is_mac:
        # macOS 目标路径：优先当前应用所属的 .app 路径，其次 /Applications
        current_app = None
        curr_exe = Path(sys.executable).resolve()
        for parent in curr_exe.parents:
            if parent.name.endswith(".app"):
                current_app = parent
                break

        if not current_app:
            desktop_app = Path.home() / "Desktop" / "美客多活动管家.app"
            system_app = Path("/Applications/美客多活动管家.app")
            current_app = desktop_app if desktop_app.exists() else system_app

        target_str = str(current_app)
        extracted_str = str(extracted_target)

        # 启动后台 bash 跳板脚本，等待主进程退出后原子替换并拉起新版
        cmd = (
            f"sleep 1.2 && "
            f"rm -rf '{target_str}' && "
            f"cp -R '{extracted_str}' '{target_str}' && "
            f"xattr -cr '{target_str}' && "
            f"open '{target_str}'"
        )
        subprocess.Popen(["bash", "-c", cmd], close_fds=True)
    else:
        # Windows: 生成临时跳板 .bat 脚本
        current_dir = Path(sys.executable).parent.resolve()
        extracted_dir = extracted_target.resolve()

        bat_content = f"""@echo off
chcp 65001 >nul
timeout /t 1 /nobreak >nul
taskkill /f /pid {current_pid} >nul 2>&1
xcopy /s /e /y "{extracted_dir}\\*" "{current_dir}\\" >nul
start "" "{current_dir}\\美客多活动管家.exe"
del "%~f0"
"""
        bat_file = Path(tempfile.gettempdir()) / f"mdm_update_{current_pid}.bat"
        bat_file.write_text(bat_content, encoding="utf-8")

        CREATE_NO_WINDOW = 0x08000000
        subprocess.Popen(
            ["cmd.exe", "/c", str(bat_file)],
            creationflags=CREATE_NO_WINDOW,
            close_fds=True,
        )
