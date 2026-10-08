from __future__ import annotations

import base64
import shlex
import socket
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

from .config import AgentConfig, PrinterConfig


class PrinterDispatcher:
    CUPS_JOB_ID_PREFIX = "request id is "
    CUPS_WAIT_SECONDS = 30
    CUPS_ERROR_MARKERS = (
        "disabled",
        "held",
        "media-jam",
        "not connected",
        "spool-area-full",
        "stopped",
        "unavailable",
        "may not exist",
    )

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
        last_error = None
        attempts = 3
        for attempt in range(1, attempts + 1):
            try:
                with socket.create_connection(
                    (printer.host, printer.port),
                    timeout=10,
                ) as sock:
                    sock.sendall(payload)
                return
            except OSError as error:
                last_error = error
                if attempt < attempts:
                    time.sleep(2)
        raise RuntimeError(
            "Unable to reach printer %s at %s:%s after %s attempts: %s"
            % (printer.key, printer.host, printer.port, attempts, last_error)
        )

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
            completed = subprocess.run(
                parts,
                check=True,
                capture_output=True,
                text=True,
            )
            if printer.queue:
                cups_job_id = self._extract_cups_job_id(completed.stdout)
                if cups_job_id:
                    self._wait_for_cups_job(printer.queue, cups_job_id)
        finally:
            try:
                payload_path.unlink()
            except FileNotFoundError:
                pass

    def _extract_cups_job_id(self, output: str) -> str:
        for line in (output or "").splitlines():
            line = line.strip()
            if line.startswith(self.CUPS_JOB_ID_PREFIX):
                return line[len(self.CUPS_JOB_ID_PREFIX) :].split()[0]
        return ""

    def _wait_for_cups_job(self, queue: str, cups_job_id: str):
        deadline = time.monotonic() + self.CUPS_WAIT_SECONDS
        while time.monotonic() < deadline:
            active = self._cups_job_status("not-completed", cups_job_id)
            printer_status = self._cups_printer_status(queue)
            combined_status = " ".join(
                (active.stdout, active.stderr, printer_status.stdout, printer_status.stderr)
            ).lower()
            if any(marker in combined_status for marker in self.CUPS_ERROR_MARKERS):
                self._cancel_cups_job(cups_job_id)
                raise RuntimeError(
                    "CUPS job %s on queue %s did not print: %s"
                    % (cups_job_id, queue, self._compact_status(combined_status))
                )

            if cups_job_id in active.stdout:
                time.sleep(1)
                continue

            completed = self._cups_job_status("completed", cups_job_id)
            if cups_job_id in completed.stdout:
                return

            return

        self._cancel_cups_job(cups_job_id)
        raise RuntimeError(
            "CUPS job %s on queue %s did not complete within %s seconds"
            % (cups_job_id, queue, self.CUPS_WAIT_SECONDS)
        )

    def _cups_job_status(self, which_jobs: str, cups_job_id: str):
        return subprocess.run(
            ["lpstat", "-W", which_jobs, "-o"],
            capture_output=True,
            text=True,
            check=False,
        )

    def _cups_printer_status(self, queue: str):
        return subprocess.run(
            ["lpstat", "-p", queue, "-l"],
            capture_output=True,
            text=True,
            check=False,
        )

    def _cancel_cups_job(self, cups_job_id: str):
        subprocess.run(
            ["cancel", cups_job_id],
            capture_output=True,
            text=True,
            check=False,
        )

    def _compact_status(self, status: str) -> str:
        return " ".join(status.split())[:240]

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
