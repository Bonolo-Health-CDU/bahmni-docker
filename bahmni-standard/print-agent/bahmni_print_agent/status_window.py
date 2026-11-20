from __future__ import annotations

import logging
import webbrowser

_logger = logging.getLogger(__name__)


def open_status_window(url: str):
    try:
        import webview
    except ImportError:
        _logger.warning("pywebview is not installed; opening %s in the browser", url)
        webbrowser.open(url)
        return

    webview.create_window(
        "Bahmni Print Agent",
        url,
        width=1120,
        height=760,
        min_size=(780, 560),
    )
    webview.start()
