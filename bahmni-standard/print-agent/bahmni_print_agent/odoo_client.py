from __future__ import annotations

import socket
import xmlrpc.client

from .config import OdooConfig

# The connection check behind the status pill must answer quickly, even
# while polling waits much longer for a slow Odoo.
CHECK_TIMEOUT_SECONDS = 5.0


def _transport(url: str, timeout: float):
    base = xmlrpc.client.SafeTransport if url.startswith("https") else xmlrpc.client.Transport

    class TimeoutTransport(base):
        def make_connection(self, host):
            connection = super().make_connection(host)
            connection.timeout = timeout
            return connection

    return TimeoutTransport()


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
        check_timeout = min(CHECK_TIMEOUT_SECONDS, config.timeout_seconds)
        self._check_common = xmlrpc.client.ServerProxy(
            "%s/xmlrpc/2/common" % config.url,
            allow_none=True,
            transport=_transport(config.url, check_timeout),
        )
        self._check_models = xmlrpc.client.ServerProxy(
            "%s/xmlrpc/2/object" % config.url,
            allow_none=True,
            transport=_transport(config.url, check_timeout),
        )
        self._uid = None

    @property
    def uid(self) -> int:
        if self._uid is None:
            self._uid = self._authenticate(self._common)
        return self._uid

    def _authenticate(self, common) -> int:
        uid = common.authenticate(
            self.config.db,
            self.config.username,
            self.config.password,
            {},
        )
        if not uid:
            raise RuntimeError("Odoo authentication failed for %s" % self.config.username)
        return uid

    def get_pending_jobs(self, limit: int):
        return self._execute("bahmni.print.job", "api_get_pending_jobs", [limit])

    def check_connection(self) -> bool:
        """Ask Odoo a small question as the agent's user; raises unless it answers.

        The answer itself does not matter (check_access_rights returns
        False rather than failing): any answer means Odoo is reachable and
        accepts the agent's credentials.
        """
        if self._uid is None:
            self._uid = self._authenticate(self._check_common)
        self._check_models.execute_kw(
            self.config.db,
            self._uid,
            self.config.password,
            "bahmni.print.job",
            "check_access_rights",
            ["read"],
            {"raise_exception": False},
        )
        return True

    def mark_done(self, job_id: int, agent_name: str):
        return self._execute("bahmni.print.job", "api_mark_done", [job_id, agent_name])

    def mark_failed(self, job_id: int, error_message: str, agent_name: str):
        return self._execute(
            "bahmni.print.job",
            "api_mark_failed",
            [job_id, error_message, agent_name],
        )

    def retry_job(self, job_id: int):
        return self._execute(
            "bahmni.print.job",
            "write",
            [[int(job_id)], {"state": "pending"}],
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
