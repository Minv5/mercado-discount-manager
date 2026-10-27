# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

desktop = Path(SPECPATH)
project = desktop.parent

datas = [
    (str(desktop / "assets"), "assets"),
    (str(desktop / "runtime-staging" / "app"), "app"),
    (str(desktop / "reason_text.py"), "app/desktop-pyside"),
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
        "engine.controller",
        "engine.crypto",
        "engine.execution_adapter",
        "engine.executor",
        "engine.pricing",
        "engine.webhook_worker",
        "engine.item_cleaner",
        "service_center",
        "service_center.models",
        "service_center.image_cache",
        "service_center.translator",
        "service_center.template_manager",
        "service_center.toast_notification",
        "service_center.service_center_page",
        "orders",
        "orders.models",
        "orders.orders_page",
        "orders.repository",
        "items",
        "items.models",
        "items.items_page",
        "license",
        "license.machine",
        "license.crypto",
        "license.storage",
        "license.manager",
        "license.activation_dialog",
        "ui_common",
        "ui_common.clipboard",
        "ui_common.combos",
        "ui_common.image_cache",
        "ui_common.lightbox",
        "ui_common.widgets",
        "cryptography",
        "pypdf",
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
            "CFBundleVersion": "2.2.51",
            "CFBundleShortVersionString": "2.2.51",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
            "LSMinimumSystemVersion": "12.0",
        },
    )

