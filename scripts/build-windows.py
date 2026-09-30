#!/usr/bin/env python3
"""Build and package 美客多活动管家 for Windows (x64 Native PySide6 Engine).

Generates:
  dist-win/美客多活动管家/美客多活动管家.exe
  dist-win/release-manifest.json
  dist-win/美客多活动管家-Windows-x64-v{version}.zip
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def get_sha256(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            sha.update(chunk)
    return sha.hexdigest().upper()


def get_tree_fingerprint(root: Path) -> str:
    rows: list[str] = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.name != "build-info.json":
            rel = p.relative_to(root).as_posix()
            rows.append(f"{rel}|{get_sha256(p)}")
    combined = "\n".join(rows).encode("utf-8")
    return hashlib.sha256(combined).hexdigest().upper()


def build() -> None:
    build_started = datetime.now(timezone.utc)
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent
    desktop_dir = project_root / "desktop-pyside"
    spec_path = desktop_dir / "mercado_discount_manager_pyside.spec"
    dist_dir = project_root / "dist-win"
    work_dir = desktop_dir / "build-release-win"
    staging_dir = desktop_dir / "runtime-staging"
    app_staging = staging_dir / "app"

    product = "mercado-discount-manager"
    display_name = "美客多活动管家"
    protocol_version = "3"

    pkg_json = json.loads((project_root / "package.json").read_text(encoding="utf-8"))
    product_version = pkg_json["version"]
    print(f"[*] Product: {display_name} v{product_version} (Windows x64)")

    if staging_dir.exists():
        shutil.rmtree(staging_dir, ignore_errors=True)
    app_staging.mkdir(parents=True, exist_ok=True)
    shutil.copy2(project_root / "package.json", app_staging / "package.json")

    build_fingerprint = get_tree_fingerprint(desktop_dir / "engine")
    build_info = {
        "product": product,
        "display_name": display_name,
        "version": product_version,
        "protocol_version": protocol_version,
        "build_fingerprint": build_fingerprint,
        "built_at": build_started.isoformat(),
        "engine": "native-python-pyside6",
    }
    (app_staging / "build-info.json").write_text(
        json.dumps(build_info, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    if dist_dir.exists():
        shutil.rmtree(dist_dir, ignore_errors=True)
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    dist_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    print("[*] Running PyInstaller to build Windows executable...")
    pyinstaller_cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(work_dir),
        str(spec_path),
    ]
    subprocess.run(pyinstaller_cmd, check=True)

    app_folder = dist_dir / display_name
    main_exe = app_folder / f"{display_name}.exe"
    if not main_exe.exists():
        raise RuntimeError(f"Expected Windows executable not found: {main_exe}")

    print("[*] Testing bundled application smoke service...")
    smoke_env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    res = subprocess.run(
        [str(main_exe), "--smoke-service"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=smoke_env,
        check=True,
    )
    lines = [line.strip() for line in res.stdout.splitlines() if line.strip().startswith("{")]
    smoke_line = lines[-1] if lines else res.stdout.strip()
    print(f"[*] Smoke output: {smoke_line}")
    smoke_data = json.loads(smoke_line)
    if not smoke_data.get("ok"):
        raise RuntimeError(f"Smoke test failed: {res.stdout}")

    all_files = [p for p in app_folder.rglob("*") if p.is_file()]
    manifest = {
        "schema_version": 1,
        "product": product,
        "display_name": display_name,
        "version": product_version,
        "protocol_version": protocol_version,
        "build_fingerprint": build_fingerprint,
        "built_at": build_info["built_at"],
        "file_count": len(all_files),
        "total_bytes": sum(p.stat().st_size for p in all_files),
        "executable": main_exe.name,
        "exe_length": main_exe.stat().st_size,
        "exe_sha256": get_sha256(main_exe),
    }
    manifest_path = dist_dir / "release-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    zip_path = dist_dir / f"{display_name}-Windows-x64-v{product_version}.zip"
    print(f"[*] Creating Windows release archive: {zip_path}...")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in all_files:
            arcname = Path(display_name) / file_path.relative_to(app_folder)
            zf.write(file_path, arcname.as_posix())

    shutil.rmtree(staging_dir, ignore_errors=True)
    shutil.rmtree(work_dir, ignore_errors=True)

    print("\n" + "=" * 60)
    print(f"✅ Windows 软件包构建成功！ (v{product_version})")
    print(f"  可执行程序: {main_exe}")
    print(f"  发布压缩包: {zip_path}")
    print("=" * 60)


if __name__ == "__main__":
    build()
