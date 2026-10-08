import base64
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bahmni_print_agent.agent import PrintAgent
from bahmni_print_agent.config import AgentConfig, OdooConfig, PrinterConfig
from bahmni_print_agent.printers import PrinterDispatcher


class FakeClient:
    def __init__(self, jobs):
        self.jobs = list(jobs)
        self.done = []
        self.failed = []

    def get_pending_jobs(self, limit):
        return self.jobs[:limit]

    def mark_done(self, job_id, agent_name):
        self.done.append((job_id, agent_name))

    def mark_failed(self, job_id, error_message, agent_name):
        self.failed.append((job_id, error_message, agent_name))


class PrintAgentTests(unittest.TestCase):
    def _config(self, output_dir):
        return AgentConfig(
            name="test-agent",
            poll_interval_seconds=0.1,
            job_limit=10,
            dry_run=True,
            dry_run_output_dir=Path(output_dir),
            odoo=OdooConfig(
                url="http://odoo.example",
                db="odoo",
                username="print_agent",
                password="secret",
            ),
            printers={
                "product_printer": PrinterConfig(
                    key="product_printer",
                    type="dry_run",
                )
            },
        )

    def test_dry_run_writes_payload_file_and_marks_done(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            job = {
                "id": 12,
                "label_type": "medicine_label",
                "printer_key": "product_printer",
                "command_language": "tspl",
                "payload": "SIZE 50 mm, 25 mm\nPRINT 1\n",
                "payload_encoding": "text",
            }
            config = self._config(temp_dir)
            client = FakeClient([job])

            processed = PrintAgent(
                config,
                client=client,
                dispatcher=PrinterDispatcher(config),
            ).process_once()

            self.assertEqual(processed, 1)
            self.assertEqual(client.done, [(12, "test-agent")])
            self.assertEqual(client.failed, [])
            files = list(Path(temp_dir).glob("*.tspl"))
            self.assertEqual(len(files), 1)
            self.assertIn("PRINT 1", files[0].read_text())

    def test_base64_pdf_payload_is_decoded_in_dry_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = base64.b64encode(b"%PDF fake").decode("ascii")
            job = {
                "id": 99,
                "label_type": "dispensing_slip",
                "printer_key": "document_printer",
                "command_language": "pdf",
                "payload": payload,
                "payload_encoding": "base64",
            }
            config = self._config(temp_dir)

            output_path = PrinterDispatcher(config).dispatch(job)

            self.assertEqual(output_path.suffix, ".pdf")
            self.assertEqual(output_path.read_bytes(), b"%PDF fake")

    def test_raw_tcp_printer_retries_transient_connection_refusal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = self._config(temp_dir)
            config = AgentConfig(
                name=config.name,
                poll_interval_seconds=config.poll_interval_seconds,
                job_limit=config.job_limit,
                dry_run=False,
                dry_run_output_dir=config.dry_run_output_dir,
                odoo=config.odoo,
                printers={
                    "product_printer": PrinterConfig(
                        key="product_printer",
                        type="raw_tcp",
                        host="192.168.26.43",
                        port=9100,
                    )
                },
            )
            job = {
                "id": 26,
                "label_type": "medicine_label",
                "printer_key": "product_printer",
                "command_language": "tspl",
                "payload": "PRINT 1\n",
                "payload_encoding": "text",
            }
            socket_context = MagicMock()
            socket_obj = socket_context.__enter__.return_value

            with patch(
                "bahmni_print_agent.printers.socket.create_connection",
                side_effect=[ConnectionRefusedError("busy"), socket_context],
            ) as create_connection, patch("bahmni_print_agent.printers.time.sleep"):
                PrinterDispatcher(config).dispatch(job)

            self.assertEqual(create_connection.call_count, 2)
            socket_obj.sendall.assert_called_once_with(b"PRINT 1\n")

    def test_cups_queue_error_raises_after_lp_accepts_job(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = self._config(temp_dir)
            config = AgentConfig(
                name=config.name,
                poll_interval_seconds=config.poll_interval_seconds,
                job_limit=config.job_limit,
                dry_run=False,
                dry_run_output_dir=config.dry_run_output_dir,
                odoo=config.odoo,
                printers={
                    "dispensing_receipt_printer": PrinterConfig(
                        key="dispensing_receipt_printer",
                        type="command",
                        queue="CDU_RECEIPT",
                    )
                },
            )
            job = {
                "id": 39,
                "label_type": "box_manifest",
                "printer_key": "dispensing_receipt_printer",
                "command_language": "pdf",
                "payload": base64.b64encode(b"%PDF fake").decode("ascii"),
                "payload_encoding": "base64",
            }
            lp_result = subprocess.CompletedProcess(
                ["lp"],
                0,
                stdout="request id is CDU_RECEIPT-12 (1 file(s))\n",
                stderr="",
            )
            active_result = subprocess.CompletedProcess(
                ["lpstat"],
                0,
                stdout="CDU_RECEIPT-12 dinny 17408 Wed Oct 7 13:26:48 2026\n",
                stderr="",
            )
            printer_result = subprocess.CompletedProcess(
                ["lpstat"],
                0,
                stdout=(
                    "printer CDU_RECEIPT now printing CDU_RECEIPT-12.\n"
                    "\tThe printer may not exist or is unavailable at this time.\n"
                ),
                stderr="",
            )
            cancel_result = subprocess.CompletedProcess(["cancel"], 0, stdout="", stderr="")

            with patch(
                "bahmni_print_agent.printers.subprocess.run",
                side_effect=[
                    lp_result,
                    active_result,
                    printer_result,
                    cancel_result,
                ],
            ) as run:
                with self.assertRaisesRegex(RuntimeError, "did not print"):
                    PrinterDispatcher(config).dispatch(job)

            self.assertEqual(run.call_args_list[-1].args[0], ["cancel", "CDU_RECEIPT-12"])


if __name__ == "__main__":
    unittest.main()
