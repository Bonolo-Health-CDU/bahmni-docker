from __future__ import annotations

import argparse
import json
import logging
import sys

from .agent import PrintAgent
from .config import load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bahmni Odoo print queue agent")
    parser.add_argument(
        "--config",
        default="config.local.ini",
        help="Path to agent INI config file",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process one poll cycle and exit",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Python logging level",
    )
    parser.add_argument(
        "--force-active",
        action="store_true",
        help="Override peer election and poll as the active instance",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print a JSON status snapshot and exit",
    )
    parser.add_argument(
        "--test-printers",
        action="store_true",
        help="Check configured printer connectivity and exit",
    )
    parser.add_argument(
        "--status-ui",
        action="store_true",
        help="Run the agent with the local status UI, without a tray icon",
    )
    parser.add_argument(
        "--tray",
        action="store_true",
        help="Run the agent as a tray application with local status UI",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open the status window automatically",
    )
    parser.add_argument(
        "--status-window-url",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--install-startup",
        action="store_true",
        help="Install a Windows login startup launcher for this config",
    )
    parser.add_argument(
        "--uninstall-startup",
        action="store_true",
        help="Remove the Windows login startup launcher",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.status_window_url:
        from .status_window import open_status_window

        open_status_window(args.status_window_url)
        return 0

    if args.install_startup:
        from .startup import install_startup

        launcher = install_startup(args.config)
        print("Installed startup launcher: %s" % launcher)
        return 0

    if args.uninstall_startup:
        from .startup import uninstall_startup

        removed = uninstall_startup()
        print("Removed startup launcher" if removed else "Startup launcher was not installed")
        return 0

    if args.tray:
        from .tray_app import run_tray_app

        return run_tray_app(
            args.config,
            force_active=args.force_active,
            open_window=not args.no_open,
        )

    if args.status_ui:
        from .tray_app import run_status_ui

        return run_status_ui(
            args.config,
            force_active=args.force_active,
            open_window=not args.no_open,
        )

    config = load_config(args.config)
    agent = PrintAgent(config)
    if args.force_active:
        if agent.election:
            agent.election.set_force_active(True)
        else:
            logging.getLogger(__name__).warning(
                "--force-active was ignored because election is disabled"
            )
    if args.status:
        print(
            json.dumps(
                agent.status_snapshot(check_odoo=True, check_printers=True),
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.test_printers:
        results = [result.to_dict() for result in agent.dispatcher.check_printers()]
        print(json.dumps(results, indent=2, sort_keys=True))
        return 0 if all(result["ok"] for result in results) else 1
    if args.once:
        agent.process_once()
    else:
        agent.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
