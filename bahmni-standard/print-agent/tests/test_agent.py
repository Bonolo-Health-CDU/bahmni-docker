import base64
import json
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
from xmlrpc.server import SimpleXMLRPCRequestHandler, SimpleXMLRPCServer
from dataclasses import replace
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
from bahmni_print_agent.odoo_client import OdooPrintQueueClient
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


class FakeOdooServer:
    """A stand-in Odoo XML-RPC server that can be stopped."""

    def __init__(self):
        self.calls = []
        class Handler(SimpleXMLRPCRequestHandler):
            rpc_paths = ("/xmlrpc/2/common", "/xmlrpc/2/object")

        self._server = SimpleXMLRPCServer(
            ("127.0.0.1", 0), requestHandler=Handler, logRequests=False, allow_none=True
        )
        self._server.register_function(self._authenticate, "authenticate")
        self._server.register_function(self._execute_kw, "execute_kw")
        self.url = "http://127.0.0.1:%s" % self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def _authenticate(self, db, login, password, context):
        self.calls.append("authenticate")
        return 7 if password == "secret" else False

    def _execute_kw(self, db, uid, password, model, method, args, kwargs=None):
        self.calls.append(method)
        if method == "api_get_pending_jobs":
            return []
        return True

    def stop(self):
        self._server.shutdown()
        self._server.server_close()


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
            payload = base64.b64encode(b"%PDF-1.4 fake").decode("ascii")
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
            self.assertEqual(output_path.read_bytes(), b"%PDF-1.4 fake")

    def test_invalid_manifest_is_failed_without_blocking_valid_pdf(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            valid_pdf = b"%PDF-1.4 manifest"
            manifest = {
                "id": 100,
                "label_type": "box_manifest",
                "printer_key": "document_printer",
                "command_language": "pdf",
                "payload": base64.b64encode(
                    b'<div class="footer">Generated by Collect-N-Go</div>\n^XA\n^XZ'
                ).decode("ascii"),
                "payload_encoding": "base64",
            }
            valid_manifest = dict(
                manifest,
                id=101,
                payload=base64.b64encode(valid_pdf).decode("ascii"),
            )
            config = self._config(temp_dir)
            client = FakeClient([manifest, valid_manifest])

            with self.assertLogs("bahmni_print_agent.agent", level="ERROR"):
                processed = PrintAgent(config, client=client).process_once()

            self.assertEqual(processed, 2)
            self.assertEqual(client.done, [(101, "test-agent")])
            self.assertEqual(len(client.failed), 1)
            self.assertEqual(client.failed[0][0], 100)
            self.assertIn("Invalid PDF payload", client.failed[0][1])
            files = list(Path(temp_dir).glob("*.pdf"))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].read_bytes(), valid_pdf)

    def test_document_printer_rejects_label_commands_before_dispatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = replace(
                self._config(temp_dir),
                dry_run=False,
                printers={
                    "document_printer": PrinterConfig(
                        key="document_printer", type="command", queue="CDU_DOCUMENT"
                    )
                },
            )
            dispatcher = PrinterDispatcher(config)
            with patch.object(dispatcher, "_send_command") as send_command:
                for language, payload in (("zpl", "^XA\n^XZ"), ("tspl", "PRINT 1")):
                    with self.subTest(language=language):
                        with self.assertRaisesRegex(RuntimeError, "requires PDF"):
                            dispatcher.dispatch({
                                "id": 102,
                                "label_type": "box_label",
                                "printer_key": "document_printer",
                                "command_language": language,
                                "payload": payload,
                                "payload_encoding": "text",
                            })
                send_command.assert_not_called()

    def test_manifest_rejects_label_format_or_wrong_printer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dispatcher = PrinterDispatcher(self._config(temp_dir))
            for language, printer_key in (
                ("zpl", "box_label_printer"),
                ("pdf", "dispensing_receipt_printer"),
            ):
                with self.subTest(language=language, printer_key=printer_key):
                    with self.assertRaisesRegex(RuntimeError, "Box manifests must be PDF"):
                        dispatcher.dispatch({
                            "id": 103,
                            "label_type": "box_manifest",
                            "printer_key": printer_key,
                            "command_language": language,
                            "payload": "^XA\n^XZ",
                            "payload_encoding": "text",
                        })

    def test_valid_manifest_pdf_is_decoded_before_print_command(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = replace(
                self._config(temp_dir),
                dry_run=False,
                printers={
                    "document_printer": PrinterConfig(
                        key="document_printer", type="command", queue="CDU_DOCUMENT"
                    )
                },
            )
            payload = b"%PDF-1.4 manifest"
            job = {
                "id": 104,
                "label_type": "box_manifest",
                "printer_key": "document_printer",
                "command_language": "pdf",
                "payload": base64.b64encode(payload).decode("ascii"),
                "payload_encoding": "base64",
            }
            dispatcher = PrinterDispatcher(config)
            with patch.object(dispatcher, "_send_command") as send_command:
                dispatcher.dispatch(job)
                send_command.assert_called_once_with(
                    config.printers["document_printer"], job, payload
                )

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
                "label_type": "dispensing_slip",
                "printer_key": "dispensing_receipt_printer",
                "command_language": "pdf",
                "payload": base64.b64encode(b"%PDF-1.4 fake").decode("ascii"),
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

    def test_odoo_check_contacts_odoo_every_time(self):
        server = FakeOdooServer()
        client = OdooPrintQueueClient(
            OdooConfig(url=server.url, db="odoo", username="print_agent", password="secret")
        )
        try:
            self.assertTrue(client.check_connection())
            self.assertTrue(client.check_connection())
            self.assertEqual(server.calls.count("check_access_rights"), 2)
        finally:
            server.stop()

        # Logged in earlier, but Odoo is gone now: the check must fail.
        with self.assertRaises(OSError):
            client.check_connection()

    def test_odoo_check_fails_with_wrong_password(self):
        server = FakeOdooServer()
        try:
            client = OdooPrintQueueClient(
                OdooConfig(url=server.url, db="odoo", username="print_agent", password="wrong")
            )
            with self.assertRaisesRegex(RuntimeError, "authentication failed"):
                client.check_connection()
        finally:
            server.stop()

    def test_odoo_check_gives_up_quickly_when_odoo_does_not_answer(self):
        # Accepts the connection but never replies, like a hung server.
        silent = socket.socket()
        silent.bind(("127.0.0.1", 0))
        silent.listen()
        try:
            client = OdooPrintQueueClient(
                OdooConfig(
                    url="http://127.0.0.1:%s" % silent.getsockname()[1],
                    db="odoo",
                    username="print_agent",
                    password="secret",
                    timeout_seconds=0.5,
                )
            )
            started = time.monotonic()
            with self.assertRaises(OSError):
                client.check_connection()
            self.assertLess(time.monotonic() - started, 3)
        finally:
            silent.close()

    def test_status_pill_shows_odoo_unavailable_after_it_stops(self):
        server = FakeOdooServer()
        with tempfile.TemporaryDirectory() as temp_dir:
            config = replace(
                self._config(temp_dir),
                odoo=OdooConfig(url=server.url, db="odoo", username="print_agent", password="secret"),
            )
            agent = PrintAgent(config, dispatcher=PrinterDispatcher(config))
            try:
                agent.process_once()
                self.assertTrue(agent.status_snapshot(max_age_seconds=30)["odoo"]["reachable"])
            finally:
                server.stop()

            # A failed poll is what the page shows; its periodic refresh must
            # not overwrite it with an old "reachable".
            with self.assertRaises(OSError):
                agent.process_once()
            status = agent.status_snapshot(max_age_seconds=30)["odoo"]
            self.assertFalse(status["reachable"])
            self.assertTrue(status["last_error"])

            # A standby agent does not poll: the periodic check finds out instead.
            agent._set_odoo_status(True)
            self.assertFalse(agent.status_snapshot(max_age_seconds=0)["odoo"]["reachable"])
            # The Refresh button checks at once.
            agent._set_odoo_status(True)
            self.assertFalse(agent.status_snapshot(check_odoo=True)["odoo"]["reachable"])

    def test_status_page_reuses_recent_checks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = self._config(temp_dir)
            client = FakeClient([])
            client.check_connection = MagicMock(return_value=True)
            dispatcher = PrinterDispatcher(config)
            agent = PrintAgent(config, client=client, dispatcher=dispatcher)

            with patch.object(dispatcher, "check_printers", wraps=dispatcher.check_printers) as check_printers:
                first = agent.status_snapshot(max_age_seconds=30)
                second = agent.status_snapshot(max_age_seconds=30)
                agent.status_snapshot(check_odoo=True, check_printers=True)

            self.assertEqual(client.check_connection.call_count, 2)
            self.assertEqual(check_printers.call_count, 2)
            self.assertEqual(second["printers"], first["printers"])
            self.assertEqual(first["printers"][0]["status"], "dry_run")

    def _pdf_command_config(self, temp_dir):
        config = self._config(temp_dir)
        return replace(
            config,
            dry_run=False,
            printers={
                "document_printer": PrinterConfig(
                    key="document_printer",
                    type="command",
                    command='powershell -File {agent_dir}/scripts/print_pdf_windows.ps1 -PrinterName "CDU DOCUMENT" -File {file}',
                )
            },
        )

    def test_pdf_command_uses_the_packaged_script_and_sumatra(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle = Path(temp_dir) / "_MEI12345"
            (bundle / "scripts").mkdir(parents=True)
            sumatra = bundle / "scripts" / "SumatraPDF.exe"
            sumatra.write_bytes(b"MZ")
            dispatcher = PrinterDispatcher(self._pdf_command_config(temp_dir))
            job = {
                "id": 31,
                "label_type": "box_manifest",
                "printer_key": "document_printer",
                "command_language": "pdf",
                "payload": base64.b64encode(b"%PDF-1.4 manifest").decode(),
                "payload_encoding": "base64",
            }

            with patch("sys._MEIPASS", str(bundle), create=True), patch.dict(
                "os.environ", {"CDU_SUMATRA_PDF": ""}
            ), patch("bahmni_print_agent.printers.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess([], 0, "", "")
                dispatcher.dispatch(job)
                self.assertTrue(dispatcher.check_printer("document_printer").ok)

            parts = run.call_args.args[0]
            self.assertEqual(parts[2], bundle.as_posix() + "/scripts/print_pdf_windows.ps1")
            self.assertEqual(parts[4], "CDU DOCUMENT")
            self.assertEqual(run.call_args.kwargs["env"]["CDU_SUMATRA_PDF"], str(sumatra))

    def test_pdf_printer_check_reports_missing_sumatra(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dispatcher = PrinterDispatcher(self._pdf_command_config(temp_dir))
            with patch("sys._MEIPASS", temp_dir, create=True), patch.dict(
                "os.environ", {"CDU_SUMATRA_PDF": "", "ProgramFiles": temp_dir, "ProgramFiles(x86)": temp_dir}
            ):
                result = dispatcher.check_printer("document_printer")
            self.assertFalse(result.ok)
            self.assertEqual(result.status, "missing_pdf_printer")

    def test_failed_print_command_reports_the_scripts_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dispatcher = PrinterDispatcher(self._pdf_command_config(temp_dir))
            job = {
                "id": 32,
                "label_type": "box_manifest",
                "printer_key": "document_printer",
                "command_language": "pdf",
                "payload": base64.b64encode(b"%PDF-1.4 manifest").decode(),
                "payload_encoding": "base64",
            }
            failure = subprocess.CalledProcessError(3, ["powershell"], "", "SumatraPDF.exe was not found.")
            with patch("bahmni_print_agent.printers.subprocess.run", side_effect=failure):
                with self.assertRaisesRegex(RuntimeError, "exit code 3: SumatraPDF.exe was not found"):
                    dispatcher.dispatch(job)

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
