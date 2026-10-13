from __future__ import annotations

import logging
import threading

from .agent import PrintAgent

_logger = logging.getLogger(__name__)


class AgentRuntime:
    def __init__(self, agent: PrintAgent):
        self.agent = agent
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self.agent.run_forever,
                args=(self._stop_event,),
                name="bahmni-print-agent",
                daemon=True,
            )
            self._thread.start()
            _logger.info("Print agent runtime started")

    def stop(self):
        with self._lock:
            self._stop_event.set()
            thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5)
        _logger.info("Print agent runtime stopped")

    def pause(self):
        self.agent.pause()

    def resume(self):
        self.agent.resume()

    def force_active(self):
        if self.agent.election:
            self.agent.election.set_force_active(True)

    def retry_job(self, job_id: int) -> bool:
        return self.agent.retry_job(job_id)

    def check_printer(self, key: str) -> dict:
        return self.agent.check_printer(key)

    def test_print(self, key: str) -> dict:
        return self.agent.test_print(key)

    def snapshot(
        self,
        check_odoo: bool = False,
        check_printers: bool = False,
    ) -> dict:
        snapshot = self.agent.status_snapshot(
            check_odoo=check_odoo,
            check_printers=check_printers,
        )
        snapshot["runtime"] = {
            "running": bool(self._thread and self._thread.is_alive()),
            "stopping": self._stop_event.is_set(),
        }
        return snapshot
