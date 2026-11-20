from __future__ import annotations

from configparser import ConfigParser
from dataclasses import dataclass, field
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
    test_language: str = ""


@dataclass(frozen=True)
class ElectionConfig:
    enabled: bool = False
    hostname: str = ""
    priority: int = 100
    port: int = 47990
    broadcast_interval_seconds: float = 2.0
    peer_timeout_seconds: float = 6.0
    bind_host: str = ""
    broadcast_address: str = "<broadcast>"
    force_active: bool = False


@dataclass(frozen=True)
class StatusUiConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    refresh_seconds: float = 3.0


@dataclass(frozen=True)
class AgentConfig:
    name: str
    poll_interval_seconds: float
    job_limit: int
    dry_run: bool
    dry_run_output_dir: Path
    odoo: OdooConfig
    printers: dict[str, PrinterConfig]
    config_dir: Path = Path(".")
    election: ElectionConfig = field(default_factory=ElectionConfig)
    status_ui: StatusUiConfig = field(default_factory=StatusUiConfig)


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
            test_language=parser.get(section, "test_language", fallback="").strip(),
        )

    election = ElectionConfig(
        enabled=parser.getboolean("election", "enabled", fallback=False),
        hostname=parser.get("election", "hostname", fallback="").strip(),
        priority=parser.getint("election", "priority", fallback=100),
        port=parser.getint("election", "port", fallback=47990),
        broadcast_interval_seconds=parser.getfloat(
            "election",
            "broadcast_interval_seconds",
            fallback=2.0,
        ),
        peer_timeout_seconds=parser.getfloat(
            "election",
            "peer_timeout_seconds",
            fallback=6.0,
        ),
        bind_host=parser.get("election", "bind_host", fallback="").strip(),
        broadcast_address=parser.get(
            "election",
            "broadcast_address",
            fallback="<broadcast>",
        ).strip()
        or "<broadcast>",
        force_active=parser.getboolean("election", "force_active", fallback=False),
    )

    status_ui = StatusUiConfig(
        host=parser.get("ui", "host", fallback="127.0.0.1").strip() or "127.0.0.1",
        port=parser.getint("ui", "port", fallback=8765),
        refresh_seconds=parser.getfloat("ui", "refresh_seconds", fallback=3.0),
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
        config_dir=config_path.parent,
        election=election,
        status_ui=status_ui,
    )


def _required(parser: ConfigParser, section: str, option: str) -> str:
    value = parser.get(section, option, fallback="").strip()
    if not value:
        raise ValueError("Missing required config value: [%s] %s" % (section, option))
    return value
