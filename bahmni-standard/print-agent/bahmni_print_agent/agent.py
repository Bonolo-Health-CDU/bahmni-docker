from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import datetime

from .config import AgentConfig
from .election import PeerElection
from .odoo_client import OdooPrintQueueClient
from .printers import PrinterDispatcher

_logger = logging.getLogger(__name__)


class PrintAgent:
    def __init__(
        self,
        config: AgentConfig,
        client: OdooPrintQueueClient | None = None,
        dispatcher: PrinterDispatcher | None = None,
        election: PeerElection | None = None,
    ):
        self.config = config
        self.client = client or OdooPrintQueueClient(config.odoo)
        self.dispatcher = dispatcher or PrinterDispatcher(config)
        self.election = (
            election
            if election is not None
            else PeerElection(config.name, config.election)
            if config.election.enabled
            else None
        )
        self.recent_jobs = deque(maxlen=50)
        self.odoo_reachable: bool | None = None
        self.last_odoo_error = ""
        self.last_poll_at = ""
        self._paused = False
        self._lock = threading.RLock()

    def run_forever(self, stop_event: threading.Event | None = None):
        _logger.info("Starting print agent %s", self.config.name)
        stop_event = stop_event or threading.Event()
        self.start()
        try:
            while not stop_event.is_set():
                try:
                    self.process_once()
                except Exception:
                    _logger.exception("Print agent polling failed")
                stop_event.wait(self.config.poll_interval_seconds)
        finally:
            self.stop()

    def start(self):
        if self.election:
            self.election.start()

    def stop(self):
        if self.election:
            self.election.stop()

    def process_once(self) -> int:
        if self.paused:
            _logger.info("Print agent %s is paused", self.config.name)
            return 0
        if not self.is_active:
            _logger.info(
                "Print agent %s is %s; polling is paused",
                self.config.name,
                self.role,
            )
            return 0
        self.last_poll_at = self._now_iso()
        try:
            jobs = self.client.get_pending_jobs(self.config.job_limit)
            self._set_odoo_status(True)
        except Exception as error:
            self._set_odoo_status(False, str(error))
            raise
        if not jobs:
            _logger.info("No pending print jobs")
            return 0
        processed = 0
        for job in jobs:
            processed += 1
            job_id = int(job["id"])
            try:
                result = self.dispatcher.dispatch(job)
                self.client.mark_done(job_id, self.config.name)
                self._remember_job(job, "done")
                if result:
                    _logger.info(
                        "Printed job %s to dry-run file %s",
                        job_id,
                        result,
                    )
                else:
                    _logger.info("Printed job %s", job_id)
            except Exception as error:
                _logger.exception("Print job %s failed", job_id)
                self.client.mark_failed(job_id, str(error), self.config.name)
                self._remember_job(job, "failed", str(error))
        return processed

    def pause(self):
        with self._lock:
            self._paused = True

    def resume(self):
        with self._lock:
            self._paused = False

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    @property
    def role(self) -> str:
        return self.election.role if self.election else "active"

    @property
    def is_active(self) -> bool:
        return self.election.is_active if self.election else True

    def check_odoo(self) -> dict:
        try:
            self.client.check_connection()
            self._set_odoo_status(True)
        except Exception as error:
            self._set_odoo_status(False, str(error))
        return {
            "reachable": self.odoo_reachable,
            "last_error": self.last_odoo_error,
        }

    def retry_job(self, job_id: int) -> bool:
        result = self.client.retry_job(int(job_id))
        with self._lock:
            for job in self.recent_jobs:
                if int(job.get("id") or 0) == int(job_id):
                    job["state"] = "pending"
                    job["error"] = ""
                    job["retried_at"] = self._now_iso()
                    break
        return bool(result)

    def check_printer(self, key: str) -> dict:
        return self.dispatcher.check_printer(key).to_dict()

    def test_print(self, key: str) -> dict:
        result = self.dispatcher.test_print(key)
        return {
            "key": key,
            "ok": True,
            "output_path": result.as_posix() if result else "",
        }

    def status_snapshot(
        self,
        check_odoo: bool = False,
        check_printers: bool = False,
    ) -> dict:
        odoo_status = self.check_odoo() if check_odoo else {
            "reachable": self.odoo_reachable,
            "last_error": self.last_odoo_error,
        }
        printer_status = (
            [result.to_dict() for result in self.dispatcher.check_printers()]
            if check_printers
            else []
        )
        return {
            "agent_name": self.config.name,
            "role": self.role,
            "paused": self.paused,
            "odoo": odoo_status,
            "printers": printer_status,
            "last_poll_at": self.last_poll_at,
            "recent_jobs": self._recent_jobs_snapshot(),
            "election": self.election.snapshot() if self.election else {
                "enabled": False,
                "role": "active",
            },
        }

    def _set_odoo_status(self, reachable: bool, error: str = ""):
        with self._lock:
            self.odoo_reachable = reachable
            self.last_odoo_error = error

    def _remember_job(self, job: dict, state: str, error: str = ""):
        with self._lock:
            self.recent_jobs.appendleft(
                {
                    "time": self._now_iso(),
                    "id": job.get("id"),
                    "label_type": job.get("label_type"),
                    "printer_key": job.get("printer_key"),
                    "state": state,
                    "error": error,
                }
            )

    def _recent_jobs_snapshot(self) -> list[dict]:
        with self._lock:
            return [dict(job) for job in self.recent_jobs]

    def _now_iso(self) -> str:
        return datetime.now().isoformat(timespec="seconds")
