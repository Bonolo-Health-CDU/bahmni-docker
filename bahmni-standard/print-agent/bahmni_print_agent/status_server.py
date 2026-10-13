from __future__ import annotations

import json
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .config import StatusUiConfig
from .runtime import AgentRuntime

_logger = logging.getLogger(__name__)


class StatusServer:
    def __init__(self, runtime: AgentRuntime, config: StatusUiConfig):
        self.runtime = runtime
        self.config = config
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        if not self._server:
            return "http://%s:%s/" % (self.config.host, self.config.port)
        host, port = self._server.server_address[:2]
        return "http://%s:%s/" % (host, port)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        handler = self._handler_class()
        self._server = ThreadingHTTPServer(
            (self.config.host, self.config.port),
            handler,
        )
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="bahmni-print-agent-status-ui",
            daemon=True,
        )
        self._thread.start()
        _logger.info("Print agent status UI listening on %s", self.url)

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def _handler_class(self):
        runtime = self.runtime
        refresh_seconds = self.config.refresh_seconds

        class StatusRequestHandler(BaseHTTPRequestHandler):
            server_version = "BahmniPrintAgentStatus/1.0"

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path in ("", "/", "/index.html"):
                    self._send_html(_status_html(refresh_seconds))
                    return
                if parsed.path == "/api/status":
                    query = parse_qs(parsed.query)
                    live = query.get("live", ["0"])[0] == "1"
                    self._send_json(
                        runtime.snapshot(
                            check_odoo=live,
                            check_printers=live,
                        )
                    )
                    return
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")

            def do_POST(self):
                parsed = urlparse(self.path)
                try:
                    payload = self._read_json()
                    query = parse_qs(parsed.query)
                    data = {
                        key: values[-1]
                        for key, values in query.items()
                        if values
                    }
                    data.update(payload)
                    result = self._handle_action(parsed.path, data)
                    self._send_json({"ok": True, "result": result})
                except Exception as error:
                    _logger.exception("Status UI action failed")
                    self._send_json(
                        {"ok": False, "error": str(error)},
                        status=HTTPStatus.INTERNAL_SERVER_ERROR,
                    )

            def log_message(self, format, *args):
                _logger.debug("Status UI: " + format, *args)

            def _handle_action(self, path: str, data: dict):
                if path == "/api/pause":
                    runtime.pause()
                    return runtime.snapshot()
                if path == "/api/resume":
                    runtime.resume()
                    return runtime.snapshot()
                if path == "/api/force-active":
                    runtime.force_active()
                    return runtime.snapshot()
                if path == "/api/test-printer":
                    return runtime.check_printer(self._required(data, "key"))
                if path == "/api/test-print":
                    return runtime.test_print(self._required(data, "key"))
                if path == "/api/retry":
                    return {"retried": runtime.retry_job(int(self._required(data, "job_id")))}
                raise ValueError("Unsupported action %s" % path)

            def _required(self, data: dict, key: str) -> str:
                value = str(data.get(key) or "").strip()
                if not value:
                    raise ValueError("Missing required value: %s" % key)
                return value

            def _read_json(self) -> dict:
                length = int(self.headers.get("Content-Length") or 0)
                if not length:
                    return {}
                body = self.rfile.read(length).decode("utf-8")
                return json.loads(body or "{}")

            def _send_html(self, html: str):
                body = html.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_json(self, value: dict, status: int = HTTPStatus.OK):
                body = json.dumps(value, indent=2, sort_keys=True).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return StatusRequestHandler


def _status_html(refresh_seconds: float) -> str:
    refresh_ms = max(int(refresh_seconds * 1000), 1000)
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Bahmni Print Agent</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f4f6f8;
      --panel: #ffffff;
      --line: #d7dee7;
      --text: #1d2733;
      --muted: #627082;
      --ok: #167044;
      --warn: #9a6500;
      --bad: #b42318;
      --idle: #5e6875;
      --button: #20384f;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 18px 22px;
      background: #ffffff;
      border-bottom: 1px solid var(--line);
    }
    h1 {
      margin: 0;
      font-size: 20px;
      font-weight: 650;
      letter-spacing: 0;
    }
    main {
      display: grid;
      gap: 14px;
      padding: 16px;
      max-width: 1180px;
      margin: 0 auto;
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }
    section h2 {
      margin: 0;
      padding: 12px 14px;
      font-size: 15px;
      border-bottom: 1px solid var(--line);
    }
    .summary {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
    }
    .metric {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      min-height: 78px;
    }
    .label {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
    }
    .value {
      margin-top: 6px;
      font-size: 18px;
      font-weight: 650;
      overflow-wrap: anywhere;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 28px;
      padding: 4px 10px;
      border-radius: 999px;
      color: #fff;
      background: var(--idle);
      font-weight: 650;
    }
    .ok { background: var(--ok); }
    .warn { background: var(--warn); }
    .bad { background: var(--bad); }
    .idle { background: var(--idle); }
    .toolbar {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
    }
    button {
      min-height: 34px;
      border: 0;
      border-radius: 6px;
      padding: 7px 11px;
      background: var(--button);
      color: #fff;
      font-weight: 650;
      cursor: pointer;
    }
    button.secondary {
      background: #edf1f5;
      color: var(--text);
      border: 1px solid var(--line);
    }
    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }
    th, td {
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      overflow-wrap: anywhere;
    }
    th {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      background: #f9fafb;
    }
    tr:last-child td { border-bottom: 0; }
    .actions {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }
    .message {
      min-height: 22px;
      color: var(--muted);
      padding: 0 16px 12px;
    }
    @media (max-width: 780px) {
      header { align-items: flex-start; flex-direction: column; }
      .summary { grid-template-columns: 1fr 1fr; }
      table { table-layout: auto; }
      th:nth-child(4), td:nth-child(4) { display: none; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Bahmni Print Agent</h1>
    <span id="health" class="pill idle">Loading</span>
  </header>
  <main>
    <div class="summary">
      <div class="metric"><div class="label">Agent</div><div id="agentName" class="value">-</div></div>
      <div class="metric"><div class="label">Role</div><div id="role" class="value">-</div></div>
      <div class="metric"><div class="label">Odoo</div><div id="odoo" class="value">-</div></div>
      <div class="metric"><div class="label">Last Poll</div><div id="lastPoll" class="value">-</div></div>
    </div>
    <section>
      <h2>Controls</h2>
      <div class="toolbar">
        <button onclick="postAction('/api/resume')">Resume</button>
        <button class="secondary" onclick="postAction('/api/pause')">Pause</button>
        <button class="secondary" onclick="postAction('/api/force-active')">Force Active</button>
        <button class="secondary" onclick="loadStatus()">Refresh</button>
      </div>
      <div id="message" class="message"></div>
    </section>
    <section>
      <h2>Printers</h2>
      <table>
        <thead><tr><th>Key</th><th>Status</th><th>Detail</th><th>Actions</th></tr></thead>
        <tbody id="printers"></tbody>
      </table>
    </section>
    <section>
      <h2>Recent Jobs</h2>
      <table>
        <thead><tr><th>Time</th><th>Job</th><th>Printer</th><th>Result</th><th>Actions</th></tr></thead>
        <tbody id="jobs"></tbody>
      </table>
    </section>
    <section>
      <h2>Peers</h2>
      <table>
        <thead><tr><th>Host</th><th>Priority</th><th>Role</th><th>Last Seen</th></tr></thead>
        <tbody id="peers"></tbody>
      </table>
    </section>
  </main>
  <script>
    const refreshMs = """ + str(refresh_ms) + """;

    async function loadStatus() {
      const response = await fetch('/api/status?live=1', {cache: 'no-store'});
      const status = await response.json();
      renderStatus(status);
    }

    async function postAction(path, payload = {}) {
      setMessage('Working...');
      const response = await fetch(path, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      const data = await response.json();
      if (!data.ok) {
        setMessage(data.error || 'Action failed');
        return;
      }
      setMessage('Done');
      await loadStatus();
    }

    function renderStatus(status) {
      document.getElementById('agentName').textContent = status.agent_name || '-';
      document.getElementById('role').textContent = (status.paused ? 'Paused, ' : '') + (status.role || '-');
      document.getElementById('odoo').textContent = status.odoo && status.odoo.reachable ? 'Reachable' : 'Unavailable';
      document.getElementById('lastPoll').textContent = status.last_poll_at || '-';
      renderHealth(status);
      renderPrinters(status.printers || []);
      renderJobs(status.recent_jobs || []);
      renderPeers(status.election || {});
      if (status.odoo && status.odoo.last_error) {
        setMessage(status.odoo.last_error);
      }
    }

    function renderHealth(status) {
      const printers = status.printers || [];
      const failedPrinters = printers.filter((printer) => !printer.ok);
      const health = document.getElementById('health');
      health.className = 'pill';
      if (status.paused) {
        health.classList.add('idle');
        health.textContent = 'Paused';
      } else if (!status.odoo || !status.odoo.reachable) {
        health.classList.add('bad');
        health.textContent = 'Server unavailable';
      } else if (failedPrinters.length) {
        health.classList.add('warn');
        health.textContent = 'Printer attention';
      } else {
        health.classList.add('ok');
        health.textContent = 'Ready';
      }
    }

    function renderPrinters(printers) {
      const body = document.getElementById('printers');
      body.innerHTML = '';
      if (!printers.length) {
        body.innerHTML = '<tr><td colspan="4">No printers configured.</td></tr>';
        return;
      }
      for (const printer of printers) {
        const row = document.createElement('tr');
        row.innerHTML = `
          <td>${escapeHtml(printer.key)}</td>
          <td>${printer.ok ? 'Ready' : 'Attention'} (${escapeHtml(printer.status)})</td>
          <td>${escapeHtml(printer.detail || '')}</td>
          <td><div class="actions">
            <button class="secondary" onclick="postAction('/api/test-printer', {key: '${jsString(printer.key)}'})">Test</button>
            <button onclick="postAction('/api/test-print', {key: '${jsString(printer.key)}'})">Print</button>
          </div></td>`;
        body.appendChild(row);
      }
    }

    function renderJobs(jobs) {
      const body = document.getElementById('jobs');
      body.innerHTML = '';
      if (!jobs.length) {
        body.innerHTML = '<tr><td colspan="5">No jobs processed since this agent started.</td></tr>';
        return;
      }
      for (const job of jobs) {
        const row = document.createElement('tr');
        const canRetry = job.state === 'failed' && job.id;
        row.innerHTML = `
          <td>${escapeHtml(job.time || '')}</td>
          <td>${escapeHtml(String(job.id || ''))}<br>${escapeHtml(job.label_type || '')}</td>
          <td>${escapeHtml(job.printer_key || '')}</td>
          <td>${escapeHtml(job.state || '')}${job.error ? '<br>' + escapeHtml(job.error) : ''}</td>
          <td>${canRetry ? `<button onclick="postAction('/api/retry', {job_id: '${jsString(job.id)}'})">Retry</button>` : ''}</td>`;
        body.appendChild(row);
      }
    }

    function renderPeers(election) {
      const body = document.getElementById('peers');
      const peers = election.peers || [];
      body.innerHTML = '';
      if (!election.enabled) {
        body.innerHTML = '<tr><td colspan="4">Election disabled.</td></tr>';
        return;
      }
      if (!peers.length) {
        body.innerHTML = '<tr><td colspan="4">No peer agents visible.</td></tr>';
        return;
      }
      for (const peer of peers) {
        const row = document.createElement('tr');
        row.innerHTML = `
          <td>${escapeHtml(peer.hostname || '')}</td>
          <td>${escapeHtml(String(peer.priority || ''))}</td>
          <td>${escapeHtml(peer.role || '')}</td>
          <td>${escapeHtml(String(peer.last_seen_seconds_ago || 0))}s</td>`;
        body.appendChild(row);
      }
    }

    function setMessage(value) {
      document.getElementById('message').textContent = value || '';
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, (character) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      })[character]);
    }

    function jsString(value) {
      return String(value).replace(/\\\\/g, '\\\\\\\\').replace(/'/g, "\\\\'");
    }

    loadStatus();
    setInterval(loadStatus, refreshMs);
  </script>
</body>
</html>"""
