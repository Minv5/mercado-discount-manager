#!/usr/bin/env python3
"""Build and package 美客多活动管家 for macOS (Native PySide6 Engine).

Generates:
  dist-mac/美客多活动管家.app
  dist-mac/release-manifest.json
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
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


def terminate_running_app() -> None:
    print("[*] Closing any running instances of 美客多活动管家...")
    subprocess.run(["pkill", "-f", "美客多活动管家"], check=False)
    subprocess.run(["pkill", "-f", "mercado_discount_manager"], check=False)
    subprocess.run(["pkill", "-f", "desktop-pyside/app.py"], check=False)


def build() -> None:
    is_ci = os.environ.get("CI", "").lower() == "true"
    if not is_ci:
        terminate_running_app()

    build_started = datetime.now(timezone.utc)
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent
    desktop_dir = project_root / "desktop-pyside"
    spec_path = desktop_dir / "mercado_discount_manager_pyside.spec"
    dist_dir = project_root / "dist-mac"
    staging_dir = desktop_dir / "runtime-staging"
    app_staging = staging_dir / "app"

    product = "mercado-discount-manager"
    display_name = "美客多活动管家"
    protocol_version = "3"

    # 1. Version info
    pkg_json = json.loads((project_root / "package.json").read_text(encoding="utf-8"))
    product_version = pkg_json["version"]
    print(f"[*] Product: {display_name} v{product_version} (protocol v{protocol_version})")

    # 2. Ensure app.icns exists
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
                subprocess.run(["sips", "-z", str(size * 2), str(size * 2), str(tmp_png), "--out", str(tmp_iconset / f"icon_{size}x{size}@2x.png")], check=True)
            subprocess.run(["iconutil", "-c", "icns", str(tmp_iconset), "-o", str(icns_path)], check=True)
            shutil.rmtree(tmp_iconset, ignore_errors=True)
            tmp_png.unlink(missing_ok=True)

    # 3. Prepare runtime staging
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
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
    (app_staging / "build-info.json").write_text(json.dumps(build_info, indent=2, ensure_ascii=False), encoding="utf-8")

    # 4. Run PyInstaller
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

    # 5. Verify .app bundle
    app_bundle = tmp_dist / f"{display_name}.app"
    if not app_bundle.exists():
        raise RuntimeError(f"Expected application bundle not found: {app_bundle}")

    macos_dir = app_bundle / "Contents" / "MacOS"
    main_exe = macos_dir / display_name
    if not main_exe.exists():
        raise RuntimeError(f"Main executable not found: {main_exe}")

    # 6. Ad-hoc codesign to avoid Gatekeeper crashes
    print("[*] Performing ad-hoc code signature for macOS bundle...")
    subprocess.run(["xattr", "-cr", str(app_bundle)], check=False)
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app_bundle)], check=True)

    # 7. Test bundled application smoke service before packaging
    print("[*] Testing bundled application smoke service...")
    smoke_cmd = [str(main_exe), "--smoke-service"]
    res = subprocess.run(smoke_cmd, capture_output=True, text=True, check=True)
    lines = [line.strip() for line in res.stdout.splitlines() if line.strip().startswith("{")]
    smoke_line = lines[-1] if lines else res.stdout.strip()
    print(f"[*] Smoke output: {smoke_line}")
    smoke_data = json.loads(smoke_line)
    if not smoke_data.get("ok"):
        raise RuntimeError(f"Smoke test failed: {res.stdout}")

    # 8. Copy to project dist_dir
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
    }
    manifest_path = dist_dir / "release-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    if is_ci or "--zip" in sys.argv:
        zip_path = dist_dir / f"{display_name}-macOS-arm64-v{product_version}.zip"
        if zip_path.exists():
            zip_path.unlink()
        print(f"[*] Creating macOS release archive: {zip_path}...")
        subprocess.run(
            ["ditto", "-c", "-k", "--keepParent", str(dest_bundle), str(zip_path)],
            check=True,
        )
    else:
        # Refresh /Applications and ~/Desktop directly on local dev machine
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
        terminate_running_app()
        if desktop_dir_path.exists() and (desktop_dir_path / app_bundle.name).exists():
            print(f"[*] Launching updated application from Desktop: {desktop_dir_path / app_bundle.name}...")
            subprocess.run(["open", str(desktop_dir_path / app_bundle.name)], check=False)
        elif apps_dir.exists() and (apps_dir / app_bundle.name).exists():
            print(f"[*] Launching updated application from Applications: {apps_dir / app_bundle.name}...")
            subprocess.run(["open", str(apps_dir / app_bundle.name)], check=False)

    # Clean tmp build
    shutil.rmtree(staging_dir, ignore_errors=True)
    shutil.rmtree(build_root, ignore_errors=True)

    print("\n" + "=" * 60)
    print(f"✅ macOS 软件包构建成功！ (v{product_version})")
    print(f"  应用程序: {dest_bundle}")
    print("=" * 60)


if __name__ == "__main__":
    build()
