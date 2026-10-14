from __future__ import annotations

import os
import sys
from pathlib import Path


STARTUP_FILENAME = "Bahmni Print Agent.cmd"


def install_startup(config_path: str | Path) -> Path:
    startup_dir = _startup_dir()
    startup_dir.mkdir(parents=True, exist_ok=True)
    launcher = startup_dir / STARTUP_FILENAME
    command = _startup_command(Path(config_path).expanduser().resolve())
    working_dir = Path(config_path).expanduser().resolve().parent
    content = (
        "@echo off\n"
        "cd /d \"%s\"\n"
        "start \"Bahmni Print Agent\" %s\n"
    ) % (working_dir, command)
    launcher.write_text(
        content,
        encoding="utf-8",
    )
    return launcher


def uninstall_startup() -> bool:
    launcher = _startup_dir() / STARTUP_FILENAME
    if not launcher.exists():
        return False
    launcher.unlink()
    return True


def _startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is not set; Windows startup folder is unavailable.")
    return (
        Path(appdata)
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
    )


def _startup_command(config_path: Path) -> str:
    if getattr(sys, "frozen", False):
        return '"%s" --config "%s" --tray' % (sys.executable, config_path)
    return '"%s" -m bahmni_print_agent --config "%s" --tray' % (
        sys.executable,
        config_path,
    )
