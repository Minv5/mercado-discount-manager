#!/usr/bin/env python3
"""Build and package 美客多活动管家 for macOS (Apple Silicon arm64 / universal).

Generates:
  dist-mac/美客多活动管家.app
  dist-mac/美客多活动管家-macOS-arm64-v{version}.dmg
  dist-mac/美客多活动管家-macOS-arm64-v{version}.zip
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


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


def find_node_executable() -> tuple[Path, str, str]:
    candidates = [
        shutil.which("node"),
        "/Users/minv5/.local/bin/node",
        "/opt/homebrew/bin/node",
        "/usr/local/bin/node",
        str(Path.home() / ".local" / "bin" / "node"),
    ]
    node_path = None
    for c in candidates:
        if c and os.path.exists(c) and os.access(c, os.X_OK):
            node_path = Path(c).resolve()
            break

    if not node_path:
        raise RuntimeError("Could not find a valid Node.js binary.")

    ver_res = subprocess.run([str(node_path), "--version"], capture_output=True, text=True, check=True)
    version = ver_res.stdout.strip()
    node_hash = get_sha256(node_path)
    return node_path, version, node_hash


def get_product_protocol_version(project_root: Path) -> str:
    contract = project_root / "src" / "productContract.js"
    if contract.exists():
        m = re.search(r"PROTOCOL_VERSION\s*=\s*['\"]([^'\"]+)['\"]", contract.read_text(encoding="utf-8"))
        if m:
            return m.group(1)
    return "3"


def terminate_running_app() -> None:
    print("[*] Closing any running instances of 美客多活动管家...")
    subprocess.run(["pkill", "-f", "美客多活动管家"], check=False)
    subprocess.run(["pkill", "-f", "mercado_discount_manager"], check=False)
    subprocess.run(["pkill", "-f", "desktop-pyside/app.py"], check=False)


def build() -> None:
    terminate_running_app()
    build_started = datetime.now(timezone.utc)
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent
    desktop_dir = project_root / "desktop-pyside"
    spec_path = desktop_dir / "mercado_discount_manager_pyside.spec"
    dist_dir = project_root / "dist-mac"
    work_dir = desktop_dir / "build-release-mac"
    staging_dir = desktop_dir / "runtime-staging"
    app_staging = staging_dir / "app"
    node_staging_dir = staging_dir / "node"
    node_staging_file = node_staging_dir / "node"

    product = "mercado-discount-manager"
    display_name = "美客多活动管家"

    # 1. Version info
    pkg_json = json.loads((project_root / "package.json").read_text(encoding="utf-8"))
    product_version = pkg_json["version"]
    protocol_version = get_product_protocol_version(project_root)
    print(f"[*] Product: {display_name} v{product_version} (protocol v{protocol_version})")

    # 2. Node.js binary
    node_path, node_version, node_hash = find_node_executable()
    print(f"[*] Staging Node.js: {node_path} ({node_version}) SHA256={node_hash}")

    # 3. Ensure app.icns exists
    icns_path = desktop_dir / "assets" / "app.icns"
    if not icns_path.exists():
        ico_path = desktop_dir / "assets" / "app.ico"
        if ico_path.exists():
            print("[*] Generating assets/app.icns from app.ico...")
            tmp_png = Path("/tmp/mdm_icon_256.png")
            tmp_iconset = Path("/tmp/mdm_AppIcon.iconset")
            tmp_iconset.mkdir(parents=True, exist_ok=True)
            subprocess.run(["sips", "-s", "format", "png", str(ico_path), "--out", str(tmp_png)], check=True)
            for size in [16, 32, 64, 128, 256]:
                subprocess.run(["sips", "-z", str(size), str(size), str(tmp_png), "--out", str(tmp_iconset / f"icon_{size}x{size}.png")], check=True)
                subprocess.run(["sips", "-z", str(size*2), str(size*2), str(tmp_png), "--out", str(tmp_iconset / f"icon_{size}x{size}@2x.png")], check=True)
            subprocess.run(["iconutil", "-c", "icns", str(tmp_iconset), "-o", str(icns_path)], check=True)
            shutil.rmtree(tmp_iconset, ignore_errors=True)
            tmp_png.unlink(missing_ok=True)

    # 4. Prepare runtime staging
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    app_staging.mkdir(parents=True, exist_ok=True)
    node_staging_dir.mkdir(parents=True, exist_ok=True)

    print("[*] Copying application sources into staging...")
    if (project_root / "src").exists():
        shutil.copytree(project_root / "src", app_staging / "src")
    public_staging = app_staging / "public"
    public_staging.mkdir(parents=True, exist_ok=True)
    for pub_file in ["index.html", "styles.css"]:
        src_pub = project_root / "public" / pub_file
        if src_pub.exists():
            shutil.copy2(src_pub, public_staging / pub_file)
    shutil.copy2(project_root / "package.json", app_staging / "package.json")

    # Copy node
    shutil.copy2(node_path, node_staging_file)
    node_staging_file.chmod(node_staging_file.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # Build info
    build_fingerprint = get_tree_fingerprint(app_staging)
    build_info = {
        "product": product,
        "display_name": display_name,
        "version": product_version,
        "protocol_version": protocol_version,
        "build_fingerprint": build_fingerprint,
        "built_at": build_started.isoformat(),
        "node_version": node_version,
        "node_sha256": node_hash,
    }
    (app_staging / "build-info.json").write_text(json.dumps(build_info, indent=2, ensure_ascii=False), encoding="utf-8")

    # 5. Run PyInstaller
    print("[*] Running PyInstaller to build macOS .app bundle...")
    subprocess.run(["xattr", "-cr", str(staging_dir)], check=False)
    subprocess.run(["xattr", "-cr", str(desktop_dir / "assets")], check=False)
    build_root = Path("/tmp/mdm-build")
    tmp_dist = build_root / "dist"
    work_dir = build_root / "work"
    if build_root.exists():
        shutil.rmtree(build_root, ignore_errors=True)
    tmp_dist.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    pyinstaller_cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(tmp_dist),
        "--workpath",
        str(work_dir),
        str(spec_path),
    ]
    subprocess.run(pyinstaller_cmd, check=True)

    # 6. Verify .app bundle
    app_bundle = tmp_dist / f"{display_name}.app"
    if not app_bundle.exists():
        raise RuntimeError(f"Expected application bundle not found: {app_bundle}")

    macos_dir = app_bundle / "Contents" / "MacOS"
    main_exe = macos_dir / display_name
    if not main_exe.exists():
        raise RuntimeError(f"Main executable not found: {main_exe}")

    # Ensure any bundled node binary is executable if present
    for p in app_bundle.rglob("node"):
        if p.is_file() and p.name == "node":
            p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # 7. Ad-hoc codesign to avoid Gatekeeper crashes
    print("[*] Performing ad-hoc code signature for macOS bundle in /tmp...")
    subprocess.run(["xattr", "-cr", str(app_bundle)], check=False)
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app_bundle)], check=True)

    # 8. Test bundled application smoke service before packaging
    print("[*] Testing bundled application smoke service...")
    smoke_cmd = [str(main_exe), "--smoke-service"]
    res = subprocess.run(smoke_cmd, capture_output=True, text=True, check=True)
    print(f"[*] Smoke output: {res.stdout.strip()}")
    smoke_data = json.loads(res.stdout.strip())
    if not smoke_data.get("ok"):
        raise RuntimeError(f"Smoke test failed: {res.stdout}")

    # 9. Copy to project dist_dir
    dist_dir.mkdir(parents=True, exist_ok=True)
    dest_bundle = dist_dir / app_bundle.name
    if dest_bundle.exists():
        shutil.rmtree(dest_bundle)
    print(f"[*] Copying finalized bundle to {dest_bundle}...")
    shutil.copytree(app_bundle, dest_bundle)

    # Write release-manifest.json
    all_files = [p for p in dest_bundle.rglob("*") if p.is_file()]
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
        "executable": (dest_bundle / "Contents" / "MacOS" / display_name).name,
        "exe_length": (dest_bundle / "Contents" / "MacOS" / display_name).stat().st_size,
        "exe_sha256": get_sha256(dest_bundle / "Contents" / "MacOS" / display_name),
        "node_version": node_version,
        "node_sha256": node_hash,
    }
    manifest_path = dist_dir / "release-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    # 10. Refresh /Applications and ~/Desktop directly (no DMG / ZIP backups)
    apps_dir = Path("/Applications")
    desktop_dir_path = Path.home() / "Desktop"
    if apps_dir.exists():
        dest_apps = apps_dir / app_bundle.name
        print(f"[*] Updating {dest_apps}...")
        subprocess.run(["rm", "-rf", str(dest_apps)], check=False)
        subprocess.run(["cp", "-R", str(app_bundle), str(dest_apps)], check=False)
        subprocess.run(["xattr", "-cr", str(dest_apps)], check=False)
    if desktop_dir_path.exists():
        dest_desktop = desktop_dir_path / app_bundle.name
        print(f"[*] Updating {dest_desktop}...")
        subprocess.run(["rm", "-rf", str(dest_desktop)], check=False)
        subprocess.run(["cp", "-R", str(app_bundle), str(dest_desktop)], check=False)
        subprocess.run(["xattr", "-cr", str(dest_desktop)], check=False)

    # Ensure running instances are terminated so user can launch the fresh build
    terminate_running_app()

    # Clean tmp build
    shutil.rmtree(staging_dir, ignore_errors=True)
    shutil.rmtree(build_root, ignore_errors=True)

    print("\n" + "=" * 60)
    print(f"✅ macOS 软件包构建成功！")
    print(f"  应用程序: {dest_bundle}")
    print(f"  桌面图标: {desktop_dir_path / app_bundle.name}")
    print(f"  系统程序: {apps_dir / app_bundle.name}")
    print("=" * 60)


if __name__ == "__main__":
    build()
