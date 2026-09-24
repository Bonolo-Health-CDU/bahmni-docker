import base64
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
