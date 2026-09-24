from __future__ import annotations

import socket
import xmlrpc.client

from .config import OdooConfig


class OdooPrintQueueClient:
    def __init__(self, config: OdooConfig):
        self.config = config
        socket.setdefaulttimeout(config.timeout_seconds)
        self._common = xmlrpc.client.ServerProxy(
            "%s/xmlrpc/2/common" % config.url,
            allow_none=True,
        )
        self._models = xmlrpc.client.ServerProxy(
            "%s/xmlrpc/2/object" % config.url,
            allow_none=True,
        )
        self._uid = None

    @property
    def uid(self) -> int:
        if self._uid is None:
            self._uid = self._common.authenticate(
                self.config.db,
                self.config.username,
                self.config.password,
                {},
            )
            if not self._uid:
                raise RuntimeError("Odoo authentication failed for %s" % self.config.username)
        return self._uid

    def get_pending_jobs(self, limit: int):
        return self._execute("bahmni.print.job", "api_get_pending_jobs", [limit])

    def mark_done(self, job_id: int, agent_name: str):
        return self._execute("bahmni.print.job", "api_mark_done", [job_id, agent_name])

    def mark_failed(self, job_id: int, error_message: str, agent_name: str):
        return self._execute(
            "bahmni.print.job",
            "api_mark_failed",
            [job_id, error_message, agent_name],
        )

    def _execute(self, model: str, method: str, args: list):
        return self._models.execute_kw(
            self.config.db,
            self.uid,
            self.config.password,
            model,
            method,
            args,
        )
