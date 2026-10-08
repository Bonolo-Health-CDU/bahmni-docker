from __future__ import annotations

import logging
import time

from .config import AgentConfig
from .odoo_client import OdooPrintQueueClient
from .printers import PrinterDispatcher

_logger = logging.getLogger(__name__)


class PrintAgent:
    def __init__(
        self,
        config: AgentConfig,
        client: OdooPrintQueueClient | None = None,
        dispatcher: PrinterDispatcher | None = None,
    ):
        self.config = config
        self.client = client or OdooPrintQueueClient(config.odoo)
        self.dispatcher = dispatcher or PrinterDispatcher(config)

    def run_forever(self):
        _logger.info("Starting print agent %s", self.config.name)
        while True:
            try:
                self.process_once()
            except Exception:
                _logger.exception("Print agent polling failed")
            time.sleep(self.config.poll_interval_seconds)

    def process_once(self) -> int:
        jobs = self.client.get_pending_jobs(self.config.job_limit)
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
        return processed
