from __future__ import annotations

import base64
import shlex
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import AgentConfig, PrinterConfig


@dataclass(frozen=True)
class PrinterCheckResult:
    key: str
    ok: bool
    status: str
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "ok": self.ok,
            "status": self.status,
            "detail": self.detail,
        }


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

    def test_print(self, key: str) -> Path | None:
        printer = self.config.printers.get(str(key))
        if not printer:
            raise RuntimeError("No printer configured for key %s" % key)
        command_language = self._test_command_language(printer)
        payload = self._test_payload(command_language)
        job = {
            "id": "test",
            "label_type": "test_print",
            "printer_key": printer.key,
            "command_language": command_language,
            "payload": (
                base64.b64encode(payload).decode("ascii")
                if command_language == "pdf"
                else payload.decode("utf-8")
            ),
            "payload_encoding": "base64" if command_language == "pdf" else "text",
        }
        return self.dispatch(job)

    def check_printers(self) -> list[PrinterCheckResult]:
        return [
            self.check_printer(key)
            for key in sorted(self.config.printers)
        ]

    def check_printer(self, key: str) -> PrinterCheckResult:
        printer = self.config.printers.get(str(key))
        if not printer:
            return PrinterCheckResult(
                key=str(key),
                ok=False,
                status="not_configured",
                detail="No printer is configured for this key.",
            )
        if self.config.dry_run or printer.type == "dry_run":
            return PrinterCheckResult(
                key=printer.key,
                ok=True,
                status="dry_run",
                detail="Dry-run mode writes payload files instead of printing.",
            )
        if printer.type == "raw_tcp":
            return self._check_raw_tcp_printer(printer)
        if printer.type == "command":
            return self._check_command_printer(printer)
        return PrinterCheckResult(
            key=printer.key,
            ok=False,
            status="unsupported",
            detail="Unsupported printer type %s." % printer.type,
        )

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

    def _check_raw_tcp_printer(self, printer: PrinterConfig) -> PrinterCheckResult:
        if not printer.host:
            return PrinterCheckResult(
                key=printer.key,
                ok=False,
                status="missing_host",
                detail="Printer %s is missing a host/IP address." % printer.key,
            )
        try:
            with socket.create_connection(
                (printer.host, printer.port),
                timeout=5,
            ):
                pass
        except OSError as error:
            return PrinterCheckResult(
                key=printer.key,
                ok=False,
                status="unreachable",
                detail="Cannot reach %s:%s: %s"
                % (printer.host, printer.port, error),
            )
        return PrinterCheckResult(
            key=printer.key,
            ok=True,
            status="reachable",
            detail="Connected to %s:%s." % (printer.host, printer.port),
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
            parts = self._command_parts(command, payload_path)
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

    def _check_command_printer(self, printer: PrinterConfig) -> PrinterCheckResult:
        if printer.queue:
            try:
                status = self._cups_printer_status(printer.queue)
            except OSError as error:
                return PrinterCheckResult(
                    key=printer.key,
                    ok=False,
                    status="queue_status_unavailable",
                    detail="Cannot check CUPS queue %s: %s"
                    % (printer.queue, error),
                )
            combined_status = " ".join((status.stdout, status.stderr)).lower()
            if status.returncode != 0 or any(
                marker in combined_status for marker in self.CUPS_ERROR_MARKERS
            ):
                return PrinterCheckResult(
                    key=printer.key,
                    ok=False,
                    status="queue_error",
                    detail=self._compact_status(
                        " ".join((status.stdout, status.stderr))
                    ),
                )
            return PrinterCheckResult(
                key=printer.key,
                ok=True,
                status="queue_ready",
                detail=self._compact_status(status.stdout),
            )
        if printer.command:
            return PrinterCheckResult(
                key=printer.key,
                ok=True,
                status="command_configured",
                detail="A print command is configured; live printer status is not checked.",
            )
        return PrinterCheckResult(
            key=printer.key,
            ok=False,
            status="missing_command",
            detail="Printer %s requires a command or queue." % printer.key,
        )

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

    def _command_parts(self, command: str, payload_path: Path) -> list[str]:
        replacements = {
            "{file}": payload_path.as_posix(),
            "{config_dir}": self.config.config_dir.as_posix(),
        }
        parts = shlex.split(command)
        replaced_parts = []
        for part in parts:
            for placeholder, value in replacements.items():
                part = part.replace(placeholder, value)
            replaced_parts.append(part)
        return replaced_parts

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

    def _test_command_language(self, printer: PrinterConfig) -> str:
        if printer.test_language in ("tspl", "zpl", "pdf"):
            return printer.test_language
        key = printer.key.lower()
        if printer.type == "command" or printer.queue:
            return "pdf"
        if "product" in key or "tsc" in key:
            return "tspl"
        if "bag" in key or "box" in key or "zebra" in key:
            return "zpl"
        return "tspl"

    def _test_payload(self, command_language: str) -> bytes:
        if command_language == "tspl":
            return self._sample_tspl_payload().encode("utf-8")
        if command_language == "zpl":
            return self._sample_zpl_payload().encode("utf-8")
        if command_language == "pdf":
            return self._sample_pdf_payload()
        raise RuntimeError("Unsupported test print language %s" % command_language)

    def _sample_tspl_payload(self) -> str:
        return "\n".join(
            [
                "SIZE 50 mm, 25 mm",
                "GAP 3 mm, 0 mm",
                "DIRECTION 1",
                "CLS",
                'TEXT 20,20,"3",0,1,1,"CDU TEST PRINT"',
                'TEXT 20,55,"2",0,1,1,"Print Agent"',
                'BARCODE 20,85,"128",50,1,0,2,2,"CDU-TEST"',
                "PRINT 1",
                "",
            ]
        )

    def _sample_zpl_payload(self) -> str:
        return "\n".join(
            [
                "^XA",
                "^PW400",
                "^LL220",
                "^FO20,20^A0N,30,30^FDCDU TEST PRINT^FS",
                "^FO20,60^A0N,24,24^FDPrint Agent^FS",
                "^FO20,95^BY2^BCN,70,Y,N,N^FDCDU-TEST^FS",
                "^XZ",
                "",
            ]
        )

    def _sample_pdf_payload(self) -> bytes:
        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            "CDU Print Agent Test Page",
            "If this printed, the document printer is reachable.",
            "Generated: %s" % generated_at,
        ]
        content_lines = ["BT", "/F1 18 Tf", "72 720 Td"]
        for index, line in enumerate(lines):
            if index:
                content_lines.append("0 -32 Td")
            content_lines.append("(%s) Tj" % self._escape_pdf_text(line))
        content_lines.append("ET")
        content = "\n".join(content_lines).encode("ascii")
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /Font << /F1 4 0 R >> >> "
                b"/Contents 5 0 R >>"
            ),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
        ]
        pdf = bytearray(b"%PDF-1.4\n")
        offsets = [0]
        for index, body in enumerate(objects, start=1):
            offsets.append(len(pdf))
            pdf.extend(b"%d 0 obj\n%s\nendobj\n" % (index, body))
        xref_offset = len(pdf)
        pdf.extend(b"xref\n0 %d\n" % (len(objects) + 1))
        pdf.extend(b"0000000000 65535 f \n")
        for offset in offsets[1:]:
            pdf.extend(b"%010d 00000 n \n" % offset)
        pdf.extend(
            b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objects) + 1, xref_offset)
        )
        return bytes(pdf)

    def _escape_pdf_text(self, value: str) -> str:
        return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
