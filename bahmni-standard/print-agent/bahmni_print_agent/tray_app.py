from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from .agent import PrintAgent
from .config import load_config
from .runtime import AgentRuntime
from .status_server import StatusServer

_logger = logging.getLogger(__name__)


def run_tray_app(
    config_path: str | Path,
    force_active: bool = False,
    open_window: bool = True,
) -> int:
    config = load_config(config_path)
    agent = PrintAgent(config)
    if force_active and agent.election:
        agent.election.set_force_active(True)

    runtime = AgentRuntime(agent)
    status_server = StatusServer(runtime, config.status_ui)
    runtime.start()
    status_server.start()

    if open_window:
        launch_status_window(status_server.url)

    try:
        return _run_pystray(runtime, status_server)
    finally:
        status_server.stop()
        runtime.stop()


def run_status_ui(
    config_path: str | Path,
    force_active: bool = False,
    open_window: bool = True,
) -> int:
    config = load_config(config_path)
    agent = PrintAgent(config)
    if force_active and agent.election:
        agent.election.set_force_active(True)

    runtime = AgentRuntime(agent)
    status_server = StatusServer(runtime, config.status_ui)
    runtime.start()
    status_server.start()
    if open_window:
        launch_status_window(status_server.url)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        return 0
    finally:
        status_server.stop()
        runtime.stop()


def launch_status_window(url: str):
    command = _status_window_command(url)
    try:
        subprocess.Popen(command)
    except OSError:
        _logger.exception("Could not open status window process")
        webbrowser.open(url)


def _status_window_command(url: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--status-window-url", url]
    return [sys.executable, "-m", "bahmni_print_agent", "--status-window-url", url]


def _run_pystray(runtime: AgentRuntime, status_server: StatusServer) -> int:
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        _logger.warning("pystray and Pillow are not installed; status UI remains open")
        _wait_without_tray()
        return 0

    stop_event = threading.Event()

    def open_status(icon, item):
        launch_status_window(status_server.url)

    def pause(icon, item):
        runtime.pause()

    def resume(icon, item):
        runtime.resume()

    def force_active(icon, item):
        runtime.force_active()

    def test_printers(icon, item):
        launch_status_window(status_server.url)

    def quit_app(icon, item):
        stop_event.set()
        icon.stop()

    icon = pystray.Icon(
        "bahmni-print-agent",
        _tray_image(Image, ImageDraw, "gray"),
        "Bahmni Print Agent",
        menu=pystray.Menu(
            pystray.MenuItem("Open Status", open_status, default=True),
            pystray.MenuItem("Resume", resume),
            pystray.MenuItem("Pause", pause),
            pystray.MenuItem("Force Active", force_active),
            pystray.MenuItem("Test Printers", test_printers),
            pystray.MenuItem("Exit", quit_app),
        ),
    )

    def refresh_icon():
        while not stop_event.is_set():
            snapshot = runtime.snapshot(check_odoo=True, check_printers=True)
            icon.icon = _tray_image(Image, ImageDraw, _tray_color(snapshot))
            icon.title = _tray_title(snapshot)
            stop_event.wait(5)

    refresh_thread = threading.Thread(
        target=refresh_icon,
        name="bahmni-print-agent-tray-refresh",
        daemon=True,
    )
    refresh_thread.start()
    icon.run()
    stop_event.set()
    return 0


def _tray_color(snapshot: dict) -> str:
    if snapshot.get("paused"):
        return "gray"
    odoo = snapshot.get("odoo") or {}
    if odoo.get("reachable") is None:
        return "gray"
    if odoo.get("reachable") is False:
        return "red"
    printers = snapshot.get("printers") or []
    if any(not printer.get("ok") for printer in printers):
        return "yellow"
    return "green"


def _tray_title(snapshot: dict) -> str:
    return "Bahmni Print Agent - %s" % (
        "Paused" if snapshot.get("paused") else snapshot.get("role", "active").title()
    )


def _tray_image(Image, ImageDraw, color: str):
    colors = {
        "green": (22, 112, 68),
        "yellow": (154, 101, 0),
        "red": (180, 35, 24),
        "gray": (94, 104, 117),
    }
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((8, 8, 56, 56), fill=colors.get(color, colors["gray"]))
    draw.rectangle((28, 18, 36, 46), fill=(255, 255, 255, 230))
    draw.rectangle((18, 28, 46, 36), fill=(255, 255, 255, 230))
    return image


def _wait_without_tray():
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        return
