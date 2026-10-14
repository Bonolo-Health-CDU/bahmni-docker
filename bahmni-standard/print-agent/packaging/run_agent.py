from __future__ import annotations

import sys

from bahmni_print_agent.__main__ import main


if len(sys.argv) == 1:
    sys.argv.append("--tray")

raise SystemExit(main(sys.argv[1:]))
