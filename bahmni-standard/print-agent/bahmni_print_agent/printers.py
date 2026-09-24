from __future__ import annotations

import base64
import shlex
import socket
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from .config import AgentConfig, PrinterConfig


class PrinterDispatcher:
    def __init__(self, config: AgentConfig):
        self.config = config

    def dispatch(self, job: dict) -> Path | None:
        payload = self._decode_payload(job)
        printer = self.config.printers.get(str(job["printer_key"]))
        if self.config.dry_run:
            return self._write_dry_run_file(job, payload)
        if not printer:
            raise RuntimeError("No printer configured for key %s" % job["printer_key"])
        if printer.type == "dry_run":
            return self._write_dry_run_file(job, payload)
        if printer.type == "raw_tcp":
            self._send_raw_tcp(printer, payload)
            return None
        if printer.type == "command":
            self._send_command(printer, job, payload)
            return None
        raise RuntimeError("Unsupported printer type %s for %s" % (printer.type, printer.key))

    def _decode_payload(self, job: dict) -> bytes:
        payload = job.get("payload") or ""
        if job.get("payload_encoding") == "base64":
            return base64.b64decode(payload)
        return payload.encode("utf-8")

    def _write_dry_run_file(self, job: dict, payload: bytes) -> Path:
        self.config.dry_run_output_dir.mkdir(parents=True, exist_ok=True)
        extension = self._extension(job)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = "%s-job-%s-%s-%s.%s" % (
            timestamp,
            job.get("id"),
            self._safe_name(job.get("label_type")),
            self._safe_name(job.get("printer_key")),
            extension,
        )
        output_path = self.config.dry_run_output_dir / filename
        output_path.write_bytes(payload)
        return output_path

    def _send_raw_tcp(self, printer: PrinterConfig, payload: bytes):
        if not printer.host:
            raise RuntimeError("Printer %s is missing host" % printer.key)
        with socket.create_connection((printer.host, printer.port), timeout=10) as sock:
            sock.sendall(payload)

    def _send_command(self, printer: PrinterConfig, job: dict, payload: bytes):
        with tempfile.NamedTemporaryFile(
            suffix=".%s" % self._extension(job),
            delete=False,
        ) as payload_file:
            payload_file.write(payload)
            payload_path = Path(payload_file.name)
        try:
            command = printer.command
            if not command:
                if not printer.queue:
                    raise RuntimeError(
                        "Printer %s requires command or queue" % printer.key
                    )
                command = "lp -d %s {file}" % printer.queue
            parts = [
                payload_path.as_posix() if part == "{file}" else part
                for part in shlex.split(command)
            ]
            if "{file}" in command and payload_path.as_posix() not in parts:
                parts = [part.replace("{file}", payload_path.as_posix()) for part in parts]
            subprocess.run(parts, check=True)
        finally:
            try:
                payload_path.unlink()
            except FileNotFoundError:
                pass

    def _extension(self, job: dict) -> str:
        command_language = job.get("command_language")
        if command_language == "pdf":
            return "pdf"
        if command_language in ("tspl", "zpl"):
            return command_language
        return "prn"

    def _safe_name(self, value) -> str:
        value = str(value or "unknown")
        return "".join(char if char.isalnum() or char in ("-", "_") else "-" for char in value)
