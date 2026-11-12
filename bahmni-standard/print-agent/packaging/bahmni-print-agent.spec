# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

spec_dir = Path(SPECPATH)
project_dir = spec_dir.parent

# SumatraPDF, downloaded and checked by scripts/build_windows_agent.ps1. It
# sits beside print_pdf_windows.ps1, where the script looks for it.
sumatra_exe = project_dir / "packaging" / "vendor" / "SumatraPDF.exe"
if not sumatra_exe.is_file():
    raise SystemExit(
        "packaging/vendor/SumatraPDF.exe is missing: build with scripts/build_windows_agent.ps1"
    )

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
        (str(sumatra_exe), "scripts"),
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
