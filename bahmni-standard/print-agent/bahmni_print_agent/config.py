from __future__ import annotations

from configparser import ConfigParser
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OdooConfig:
    url: str
    db: str
    username: str
    password: str
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class PrinterConfig:
    key: str
    type: str
    host: str = ""
    port: int = 9100
    command: str = ""
    queue: str = ""


@dataclass(frozen=True)
class AgentConfig:
    name: str
    poll_interval_seconds: float
    job_limit: int
    dry_run: bool
    dry_run_output_dir: Path
    odoo: OdooConfig
    printers: dict[str, PrinterConfig]


def load_config(path: str | Path) -> AgentConfig:
    config_path = Path(path).expanduser().resolve()
    parser = ConfigParser()
    read_files = parser.read(config_path)
    if not read_files:
        raise FileNotFoundError(config_path)

    odoo = OdooConfig(
        url=_required(parser, "odoo", "url").rstrip("/"),
        db=_required(parser, "odoo", "db"),
        username=_required(parser, "odoo", "username"),
        password=_required(parser, "odoo", "password"),
        timeout_seconds=parser.getfloat("odoo", "timeout_seconds", fallback=30.0),
    )

    output_dir = Path(
        parser.get("agent", "dry_run_output_dir", fallback="printed-output")
    )
    if not output_dir.is_absolute():
        output_dir = config_path.parent / output_dir

    printers = {}
    for section in parser.sections():
        if not section.startswith("printer."):
            continue
        key = section.split(".", 1)[1]
        printers[key] = PrinterConfig(
            key=key,
            type=parser.get(section, "type", fallback="dry_run"),
            host=parser.get(section, "host", fallback=""),
            port=parser.getint(section, "port", fallback=9100),
            command=parser.get(section, "command", fallback=""),
            queue=parser.get(section, "queue", fallback=""),
        )

    return AgentConfig(
        name=parser.get("agent", "name", fallback="CDU-PRINT-AGENT"),
        poll_interval_seconds=parser.getfloat(
            "agent", "poll_interval_seconds", fallback=5.0
        ),
        job_limit=parser.getint("agent", "job_limit", fallback=20),
        dry_run=parser.getboolean("agent", "dry_run", fallback=True),
        dry_run_output_dir=output_dir,
        odoo=odoo,
        printers=printers,
    )


def _required(parser: ConfigParser, section: str, option: str) -> str:
    value = parser.get(section, option, fallback="").strip()
    if not value:
        raise ValueError("Missing required config value: [%s] %s" % (section, option))
    return value
