import base64
import json
import subprocess
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

from bahmni_print_agent.agent import PrintAgent
from bahmni_print_agent.config import (
    AgentConfig,
    ElectionConfig,
    OdooConfig,
    PrinterConfig,
    StatusUiConfig,
    load_config,
)
from bahmni_print_agent.election import PeerElection
from bahmni_print_agent.printers import PrinterDispatcher
from bahmni_print_agent.runtime import AgentRuntime
from bahmni_print_agent.status_server import StatusServer


class FakeClient:
    def __init__(self, jobs):
        self.jobs = list(jobs)
        self.done = []
        self.failed = []
        self.retried = []

    def get_pending_jobs(self, limit):
        return self.jobs[:limit]

    def check_connection(self):
        return True

    def mark_done(self, job_id, agent_name):
        self.done.append((job_id, agent_name))

    def mark_failed(self, job_id, error_message, agent_name):
        self.failed.append((job_id, error_message, agent_name))

    def retry_job(self, job_id):
        self.retried.append(job_id)
        return True


class FakeElection:
    def __init__(self, active):
        self._active = active

    @property
    def is_active(self):
        return self._active

    @property
    def role(self):
        return "active" if self._active else "standby"

    def snapshot(self):
        return {"enabled": True, "role": self.role}

    def start(self):
        return None

    def stop(self):
        return None


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

    def test_standby_agent_does_not_poll_odoo(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = self._config(temp_dir)
            client = FakeClient(
                [
                    {
                        "id": 12,
                        "label_type": "medicine_label",
                        "printer_key": "product_printer",
                        "command_language": "tspl",
                        "payload": "PRINT 1\n",
                        "payload_encoding": "text",
                    }
                ]
            )

            processed = PrintAgent(
                config,
                client=client,
                dispatcher=PrinterDispatcher(config),
                election=FakeElection(active=False),
            ).process_once()

            self.assertEqual(processed, 0)
            self.assertEqual(client.done, [])
            self.assertEqual(client.failed, [])

    def test_paused_agent_does_not_poll_odoo(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = self._config(temp_dir)
            client = FakeClient(
                [
                    {
                        "id": 12,
                        "label_type": "medicine_label",
                        "printer_key": "product_printer",
                        "command_language": "tspl",
                        "payload": "PRINT 1\n",
                        "payload_encoding": "text",
                    }
                ]
            )
            agent = PrintAgent(
                config,
                client=client,
                dispatcher=PrinterDispatcher(config),
            )

            agent.pause()
            processed = agent.process_once()

            self.assertEqual(processed, 0)
            self.assertEqual(client.done, [])
            self.assertTrue(agent.status_snapshot()["paused"])

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

    def test_status_snapshot_includes_recent_jobs_and_printer_checks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            job = {
                "id": 12,
                "label_type": "medicine_label",
                "printer_key": "product_printer",
                "command_language": "tspl",
                "payload": "PRINT 1\n",
                "payload_encoding": "text",
            }
            config = self._config(temp_dir)
            agent = PrintAgent(
                config,
                client=FakeClient([job]),
                dispatcher=PrinterDispatcher(config),
            )

            agent.process_once()
            snapshot = agent.status_snapshot(check_printers=True)

            self.assertEqual(snapshot["role"], "active")
            self.assertTrue(snapshot["odoo"]["reachable"])
            self.assertEqual(snapshot["recent_jobs"][0]["id"], 12)
            self.assertEqual(snapshot["recent_jobs"][0]["state"], "done")
            self.assertEqual(snapshot["printers"][0]["status"], "dry_run")

    def test_retry_failed_job_calls_odoo_client_and_updates_recent_job(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = self._config(temp_dir)
            client = FakeClient([])
            agent = PrintAgent(
                config,
                client=client,
                dispatcher=PrinterDispatcher(config),
            )
            agent._remember_job(
                {
                    "id": 45,
                    "label_type": "medicine_label",
                    "printer_key": "product_printer",
                },
                "failed",
                "Printer offline",
            )

            self.assertTrue(agent.retry_job(45))
            snapshot = agent.status_snapshot()

            self.assertEqual(client.retried, [45])
            self.assertEqual(snapshot["recent_jobs"][0]["state"], "pending")
            self.assertEqual(snapshot["recent_jobs"][0]["error"], "")

    def test_test_print_writes_sample_payloads_in_dry_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = AgentConfig(
                name="test-agent",
                poll_interval_seconds=0.1,
                job_limit=10,
                dry_run=True,
                dry_run_output_dir=Path(temp_dir),
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
                        test_language="tspl",
                    ),
                    "bagging_printer": PrinterConfig(
                        key="bagging_printer",
                        type="dry_run",
                        test_language="zpl",
                    ),
                    "document_printer": PrinterConfig(
                        key="document_printer",
                        type="dry_run",
                        test_language="pdf",
                    ),
                },
            )
            dispatcher = PrinterDispatcher(config)

            tspl_path = dispatcher.test_print("product_printer")
            zpl_path = dispatcher.test_print("bagging_printer")
            pdf_path = dispatcher.test_print("document_printer")

            self.assertEqual(tspl_path.suffix, ".tspl")
            self.assertIn("CDU TEST PRINT", tspl_path.read_text())
            self.assertEqual(zpl_path.suffix, ".zpl")
            self.assertTrue(zpl_path.read_text().startswith("^XA"))
            self.assertEqual(pdf_path.suffix, ".pdf")
            self.assertTrue(pdf_path.read_bytes().startswith(b"%PDF"))

    def test_status_server_exposes_pause_and_test_print_actions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = self._config(temp_dir)
            agent = PrintAgent(
                config,
                client=FakeClient([]),
                dispatcher=PrinterDispatcher(config),
            )
            runtime = AgentRuntime(agent)
            server = StatusServer(
                runtime,
                StatusUiConfig(host="127.0.0.1", port=0, refresh_seconds=1),
            )
            server.start()
            try:
                status = self._get_json(server.url + "api/status")
                self.assertEqual(status["agent_name"], "test-agent")
                self.assertFalse(status["paused"])

                pause = self._post_json(server.url + "api/pause", {})
                self.assertTrue(pause["ok"])
                self.assertTrue(pause["result"]["paused"])

                test_print = self._post_json(
                    server.url + "api/test-print",
                    {"key": "product_printer"},
                )
                self.assertTrue(test_print["ok"])
                self.assertTrue(Path(test_print["result"]["output_path"]).exists())
            finally:
                server.stop()

    def test_peer_election_prefers_lowest_priority_visible_peer(self):
        now = [100.0]
        election = PeerElection(
            "beta",
            ElectionConfig(
                hostname="beta",
                priority=10,
                peer_timeout_seconds=6,
            ),
            clock=lambda: now[0],
        )

        self.assertEqual(election.role, "active")
        election.record_peer("alpha", priority=5, role="active")

        self.assertEqual(election.role, "standby")
        self.assertEqual(election.snapshot()["active_hostname"], "alpha")

        now[0] += 7

        self.assertEqual(election.role, "active")
        self.assertEqual(election.snapshot()["peers"], [])

    def test_peer_election_uses_hostname_as_priority_tiebreaker(self):
        now = [100.0]
        election = PeerElection(
            "beta",
            ElectionConfig(hostname="beta", priority=10),
            clock=lambda: now[0],
        )

        election.record_peer("zulu", priority=10)
        self.assertEqual(election.role, "active")

        election.record_peer("aardvark", priority=10)
        self.assertEqual(election.role, "standby")

    def test_peer_election_can_be_forced_active(self):
        now = [100.0]
        election = PeerElection(
            "beta",
            ElectionConfig(hostname="beta", priority=10),
            clock=lambda: now[0],
        )
        election.record_peer("alpha", priority=1)
        self.assertEqual(election.role, "standby")

        election.set_force_active(True)

        self.assertEqual(election.role, "active")

    def test_config_loads_election_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agent.ini"
            config_path.write_text(
                "\n".join(
                    [
                        "[odoo]",
                        "url = http://odoo.example",
                        "db = odoo",
                        "username = print_agent",
                        "password = secret",
                        "",
                        "[agent]",
                        "dry_run_output_dir = printed-output",
                        "",
                        "[election]",
                        "enabled = true",
                        "hostname = cdu-pc-1",
                        "priority = 7",
                        "port = 47991",
                        "broadcast_interval_seconds = 1.5",
                        "peer_timeout_seconds = 4.5",
                        "broadcast_address = 192.168.1.255",
                        "force_active = true",
                        "",
                        "[ui]",
                        "host = 127.0.0.1",
                        "port = 9000",
                        "refresh_seconds = 2.5",
                        "",
                        "[printer.product_printer]",
                        "type = dry_run",
                        "test_language = tspl",
                    ]
                )
            )

            config = load_config(config_path)

            self.assertTrue(config.election.enabled)
            self.assertEqual(config.election.hostname, "cdu-pc-1")
            self.assertEqual(config.election.priority, 7)
            self.assertEqual(config.election.port, 47991)
            self.assertEqual(config.election.broadcast_interval_seconds, 1.5)
            self.assertEqual(config.election.peer_timeout_seconds, 4.5)
            self.assertEqual(config.election.broadcast_address, "192.168.1.255")
            self.assertTrue(config.election.force_active)
            self.assertEqual(config.status_ui.port, 9000)
            self.assertEqual(config.status_ui.refresh_seconds, 2.5)
            self.assertEqual(config.printers["product_printer"].test_language, "tspl")

    def _get_json(self, url):
        with urllib.request.urlopen(url, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def _post_json(self, url, payload):
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
