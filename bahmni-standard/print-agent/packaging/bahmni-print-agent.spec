# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

spec_dir = Path(SPECPATH)
project_dir = spec_dir.parent

hiddenimports = []
hiddenimports += collect_submodules("pystray")
hiddenimports += collect_submodules("webview")

a = Analysis(
    [str(spec_dir / "run_agent.py")],
    pathex=[str(project_dir)],
    binaries=[],
    datas=[
        (str(project_dir / "config.remote.example.ini"), "."),
        (str(project_dir / "scripts" / "print_pdf_windows.ps1"), "scripts"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="BahmniPrintAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
