# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

desktop = Path(SPECPATH)
project = desktop.parent

node_name = "node.exe" if sys.platform == "win32" else "node"
node_staged = desktop / "runtime-staging" / "node" / node_name

datas = [
    (str(desktop / "assets"), "assets"),
    (str(desktop / "runtime-staging" / "app"), "app"),
    (str(desktop / "reason_text.py"), "app/desktop-pyside"),
    (str(node_staged), "node"),
]

a = Analysis(
    [str(desktop / "app.py")],
    pathex=[str(desktop)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "engine",
        "engine.auth",
        "engine.bridge",
        "engine.client",
        "engine.crypto",
        "engine.executor",
        "engine.pricing",
        "engine.webhook_worker",
        "cryptography",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

app_icon = str(desktop / "assets" / ("app.icns" if sys.platform == "darwin" else "app.ico"))

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="美客多活动管家",
    version=str(desktop / "version_info.txt") if (desktop / "version_info.txt").exists() else None,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=(sys.platform != "darwin"),
    console=False,
    icon=app_icon,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=(sys.platform != "darwin"),
    upx_exclude=[],
    name="美客多活动管家",
)
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="美客多活动管家.app",
        icon=app_icon,
        bundle_identifier="com.mercadodiscountmanager.app",
        info_plist={
            "CFBundleName": "美客多活动管家",
            "CFBundleDisplayName": "美客多活动管家",
            "CFBundleIdentifier": "com.mercadodiscountmanager.app",
            "CFBundleVersion": "2.0.19",
            "CFBundleShortVersionString": "2.0.19",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
            "LSMinimumSystemVersion": "12.0",
        },
    )

