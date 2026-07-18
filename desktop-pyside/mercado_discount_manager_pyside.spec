# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

desktop = Path(SPECPATH)
project = desktop.parent

datas = [
    (str(desktop / "assets"), "assets"),
    (str(desktop / "runtime-staging" / "app"), "app"),
    (str(desktop / "runtime-staging" / "node" / "node.exe"), "node"),
]

a = Analysis(
    [str(desktop / "app.py")],
    pathex=[str(desktop)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="美客多活动管家",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(desktop / "assets" / "app.ico"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="美客多活动管家",
)
