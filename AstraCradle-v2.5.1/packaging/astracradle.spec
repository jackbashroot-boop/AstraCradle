# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for building standalone AstraCradle binaries (no Python required)."""

from pathlib import Path
from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT

ROOT = Path(SPECPATH).resolve().parent

datas = [
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "data"), "data"),
    (str(ROOT / "locales"), "locales"),
    (str(ROOT / "README.md"), "."),
]

a = Analysis(
    [str(ROOT / "astracradle" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "customtkinter",
        "darkdetect",
        "PIL._tkinter_finder",
        "pystray",
        "pystray._win32",
        "pystray._darwin",
        "pystray._xorg",
        "pystray._appindicator",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

ico_path = ROOT / "assets" / "astracradle.ico"
icns_path = ROOT / "assets" / "astracradle.icns"
import sys as _sys
bundle_icon = icns_path if (_sys.platform == "darwin" and icns_path.exists()) else ico_path

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AstraCradle",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(bundle_icon) if bundle_icon.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="AstraCradle",
)
