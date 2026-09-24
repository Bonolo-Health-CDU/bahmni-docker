from __future__ import annotations

import argparse
import logging

from .agent import PrintAgent
from .config import load_config


def main() -> int:
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
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config(args.config)
    agent = PrintAgent(config)
    if args.once:
        agent.process_once()
    else:
        agent.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
