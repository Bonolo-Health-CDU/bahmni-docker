import base64
import json
import re
import secrets
from datetime import datetime, timedelta
from urllib import error, parse, request

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


GRAPH_API_ROOT = "https://graph.microsoft.com/v1.0"
AUTHORITY_ROOT = "https://login.microsoftonline.com/consumers/oauth2/v2.0"
AUTHORIZE_URL = "%s/authorize" % AUTHORITY_ROOT
TOKEN_URL = "%s/token" % AUTHORITY_ROOT
ONEDRIVE_SCOPES = ["openid", "offline_access", "User.Read", "Files.ReadWrite"]


class CduOneDriveSource(models.Model):
    _name = "cdu.onedrive.source"
    _description = "CDU OneDrive Source"
    _order = "name"

    name = fields.Char(required=True, default="Primary OneDrive Source")
    active = fields.Boolean(default=True)
    enabled = fields.Boolean(default=True)
    client_id = fields.Char(required=True)
    client_secret = fields.Char(required=True)
    redirect_uri = fields.Char(compute="_compute_redirect_uri", readonly=True)
    drive_id = fields.Char(
        string="Resolved Drive ID",
        readonly=True,
        help="Automatically resolved from the connected OneDrive account.",
    )
    root_folder_id = fields.Char(string="Root Folder ID")
    root_folder_path = fields.Char(
        string="Root Folder Path",
        help="Used when Root Folder ID is not provided. Example: CDU Reports",
    )
    poll_interval_minutes = fields.Integer(default=15, required=True)
    filename_pattern = fields.Char(
        default=r"(?i).*{facility}.*\.csv$",
        help="Regular expression for candidate report files. Use {facility} as a placeholder for the facility name.",
    )
    refresh_token = fields.Text(copy=False)
    access_token = fields.Text(copy=False)
    access_token_expires_at = fields.Datetime(copy=False)
    connected = fields.Boolean(readonly=True)
    connected_at = fields.Datetime(readonly=True)
    connected_account_label = fields.Char(readonly=True)
    authorization_state = fields.Char(copy=False)
    last_polled_at = fields.Datetime(readonly=True)
    last_successful_poll_at = fields.Datetime(readonly=True)
    last_poll_result = fields.Text(readonly=True)
    last_poll_log_id = fields.Many2one("cdu.onedrive.poll.log", readonly=True)
    poll_log_ids = fields.One2many("cdu.onedrive.poll.log", "source_id", string="Poll Logs")

    _sql_constraints = [
        ("positive_poll_interval", "CHECK(poll_interval_minutes > 0)", "Poll interval must be greater than zero."),
    ]

    @api.constrains("enabled", "active")
    def _check_single_enabled_source(self):
        for record in self:
            if not (record.enabled and record.active):
                continue
            enabled_count = self.search_count([
                ("id", "!=", record.id),
                ("enabled", "=", True),
                ("active", "=", True),
            ])
            if enabled_count:
                raise ValidationError(_("Only one active enabled OneDrive source is supported in this phase."))

    @api.depends()
    def _compute_redirect_uri(self):
        for record in self:
            record.redirect_uri = record._redirect_uri()

    def action_authorize_account(self):
        self.ensure_one()
        self._validate_auth_config()
        state = secrets.token_urlsafe(24)
        self.write({"authorization_state": state})
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self._redirect_uri(),
            "response_mode": "query",
            "scope": " ".join(ONEDRIVE_SCOPES),
            "state": state,
            "prompt": "select_account",
        }
        return {
            "type": "ir.actions.act_url",
            "url": "%s?%s" % (AUTHORIZE_URL, parse.urlencode(params)),
            "target": "self",
        }

    def action_disconnect_account(self):
        self.ensure_one()
        self.write({
            "refresh_token": False,
            "access_token": False,
            "access_token_expires_at": False,
            "connected": False,
            "connected_at": False,
            "connected_account_label": False,
            "drive_id": False,
            "authorization_state": False,
            "last_poll_result": _("OneDrive account disconnected."),
        })
        return True

    def action_fetch_now(self):
        for source in self:
            source._run_poll(manual=True)

    @api.model
    def cron_poll_enabled_sources(self):
        for source in self.search([("enabled", "=", True), ("active", "=", True)]):
            if source._is_due():
                source._run_poll(manual=False)

    def _is_due(self):
        self.ensure_one()
        if not self.last_polled_at:
            return True
        last_polled = fields.Datetime.to_datetime(self.last_polled_at)
        now = fields.Datetime.to_datetime(fields.Datetime.now())
        return now >= (last_polled + timedelta(minutes=self.poll_interval_minutes))

    def _run_poll(self, manual=False):
        self.ensure_one()
        log = self.env["cdu.onedrive.poll.log"].create({
            "name": "%s - %s" % (self.name, fields.Datetime.now()),
            "source_id": self.id,
            "trigger_mode": "manual" if manual else "scheduled",
            "started_at": fields.Datetime.now(),
            "state": "running",
        })
        self.write({
            "last_polled_at": log.started_at,
            "last_poll_log_id": log.id,
            "last_poll_result": _("Poll started."),
        })

        try:
            token = self._get_access_token()
            self._ensure_drive_id(token)
            folders = self._list_root_folders(token)
            stats = self._process_folders(token, folders, log)
            state = "completed_with_errors" if stats["failures"] or stats["unmatched_folders"] else "completed"
            summary = _(
                "Folders: %(folders)s, files discovered: %(discovered)s, files downloaded: %(downloaded)s, "
                "imports created: %(imports)s, duplicates skipped: %(duplicates)s, failures: %(failures)s."
            ) % {
                "folders": stats["folders_seen"],
                "discovered": stats["files_discovered"],
                "downloaded": stats["files_downloaded"],
                "imports": stats["imports_created"],
                "duplicates": stats["duplicates_skipped"],
                "failures": stats["failures"],
            }
            log.write({
                "state": state,
                "finished_at": fields.Datetime.now(),
                "folders_seen": stats["folders_seen"],
                "matched_folders": stats["matched_folders"],
                "unmatched_folders": stats["unmatched_folders"],
                "files_discovered": stats["files_discovered"],
                "files_downloaded": stats["files_downloaded"],
                "imports_created": stats["imports_created"],
                "duplicates_skipped": stats["duplicates_skipped"],
                "failed_files": stats["failures"],
                "unmatched_folder_names": "\n".join(stats["unmatched_folder_names"]),
                "skipped_file_names": "\n".join(stats["skipped_files"]),
                "result_summary": summary,
            })
            self.write({
                "last_successful_poll_at": fields.Datetime.now(),
                "last_poll_result": summary,
                "last_poll_log_id": log.id,
            })
        except Exception as exc:
            message = str(exc)
            log.write({
                "state": "failed",
                "finished_at": fields.Datetime.now(),
                "error_message": message,
                "result_summary": message,
            })
            self.write({
                "last_poll_result": message,
                "last_poll_log_id": log.id,
            })
        return True

    def _process_folders(self, token, folders, log):
        Facility = self.env["cdu.facility"]
        stats = {
            "folders_seen": 0,
            "matched_folders": 0,
            "unmatched_folders": 0,
            "files_discovered": 0,
            "files_downloaded": 0,
            "imports_created": 0,
            "duplicates_skipped": 0,
            "failures": 0,
            "unmatched_folder_names": [],
            "skipped_files": [],
        }

        for folder in folders:
            if "folder" not in folder:
                continue
            stats["folders_seen"] += 1
            folder_name = folder.get("name", "")
            facility = Facility.search([
                ("name", "=", folder_name),
                ("enabled", "=", True),
                ("active", "=", True),
            ], limit=1)
            if not facility:
                stats["unmatched_folders"] += 1
                stats["unmatched_folder_names"].append(folder_name)
                continue

            stats["matched_folders"] += 1
            files = self._list_child_items(token, folder["id"])
            candidates = self._candidate_files(facility, files)
            stats["files_discovered"] += len(candidates)
            for remote_file in candidates:
                try:
                    result, downloaded = self._ingest_remote_file(token, facility, folder, remote_file, log)
                    if downloaded:
                        stats["files_downloaded"] += 1
                    if result == "duplicate":
                        stats["duplicates_skipped"] += 1
                    elif result == "imported":
                        stats["imports_created"] += 1
                    elif result == "failed":
                        stats["failures"] += 1
                except Exception as exc:
                    stats["failures"] += 1
                    stats["skipped_files"].append("%s: %s" % (remote_file.get("name"), exc))
        return stats

    def _candidate_files(self, facility, items):
        pattern = self._compiled_filename_pattern(facility.name)
        candidates = []
        for item in items:
            if "file" not in item:
                continue
            file_name = item.get("name") or ""
            if not file_name.lower().endswith(".csv"):
                continue
            if facility.name.lower() not in file_name.lower():
                continue
            if pattern and not pattern.search(file_name):
                continue
            candidates.append(item)
        return candidates

    def _compiled_filename_pattern(self, facility_name):
        pattern = (self.filename_pattern or "").strip()
        if not pattern:
            return False
        try:
            pattern = pattern.replace("{facility}", re.escape(facility_name))
            return re.compile(pattern)
        except re.error as exc:
            raise ValidationError(_("Invalid filename pattern: %s") % exc)

    def _ingest_remote_file(self, token, facility, folder, remote_file, log):
        ReportRun = self.env["cdu.report.run"]
        existing = ReportRun.search([
            ("remote_file_id", "=", remote_file.get("id")),
            ("remote_file_etag", "=", remote_file.get("eTag")),
            ("state", "in", ["completed", "completed_with_errors", "duplicate"]),
        ], limit=1)
        if existing:
            return "duplicate", False

        file_bytes = self._download_file(token, remote_file)
        run = ReportRun.create({
            "source_facility_id": facility.id,
            "source_file_name": remote_file.get("name"),
            "source_file": base64.b64encode(file_bytes),
            "ingestion_channel": "onedrive",
            "remote_file_id": remote_file.get("id"),
            "remote_file_etag": remote_file.get("eTag"),
            "remote_folder_name": folder.get("name"),
            "remote_folder_path": self._remote_folder_path(folder),
            "poll_log_id": log.id,
            "poll_triggered_at": fields.Datetime.now(),
        })
        run._import_file(file_bytes=file_bytes)
        if run.state == "duplicate":
            return "duplicate", True
        return ("failed", True) if run.state == "failed" else ("imported", True)

    def _remote_folder_path(self, folder):
        parent_path = ((folder.get("parentReference") or {}).get("path") or "").replace("/drive/root:", "")
        if parent_path:
            return "%s/%s" % (parent_path.strip("/"), folder.get("name"))
        return folder.get("name")

    def _validate_auth_config(self):
        self.ensure_one()
        if not self.client_id or not self.client_secret:
            raise UserError(_("Please configure the Microsoft app Client ID and Client Secret first."))

    def _redirect_uri(self):
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        return "%s/cdu/onedrive/callback" % base_url.rstrip("/")

    def _exchange_authorization_code(self, code, state):
        self.ensure_one()
        if state != self.authorization_state:
            raise ValidationError(_("The OneDrive authorization state did not match. Please try connecting again."))
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._redirect_uri(),
            "scope": " ".join(ONEDRIVE_SCOPES),
        }
        token_data = self._token_request(payload)
        self._store_token_data(token_data)
        self.write({"authorization_state": False})
        return token_data

    def _get_access_token(self):
        self.ensure_one()
        if not self.refresh_token:
            raise UserError(_("Connect a OneDrive account before polling for reports."))

        if self.access_token and self.access_token_expires_at:
            expires_at = fields.Datetime.to_datetime(self.access_token_expires_at)
            if expires_at and expires_at > fields.Datetime.to_datetime(fields.Datetime.now()) + timedelta(minutes=2):
                return self.access_token

        token_data = self._refresh_access_token()
        return token_data.get("access_token")

    def _refresh_access_token(self):
        self.ensure_one()
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "redirect_uri": self._redirect_uri(),
            "scope": " ".join(ONEDRIVE_SCOPES),
        }
        token_data = self._token_request(payload)
        self._store_token_data(token_data, preserve_refresh_token=True)
        return token_data

    def _store_token_data(self, token_data, preserve_refresh_token=False):
        self.ensure_one()
        refresh_token = token_data.get("refresh_token") or (self.refresh_token if preserve_refresh_token else False)
        access_token = token_data.get("access_token")
        expires_in = int(token_data.get("expires_in") or 0)
        values = {
            "refresh_token": refresh_token,
            "access_token": access_token,
            "access_token_expires_at": (
                fields.Datetime.to_datetime(fields.Datetime.now()) + timedelta(seconds=max(expires_in - 60, 0))
            ) if access_token else False,
            "connected": bool(refresh_token and access_token),
            "connected_at": fields.Datetime.now() if refresh_token and access_token else False,
        }
        if access_token:
            account = self._get_account_profile(access_token)
            label_parts = [account.get("displayName"), account.get("userPrincipalName"), account.get("id")]
            values["connected_account_label"] = next((part for part in label_parts if part), False)
            values["drive_id"] = self._resolve_drive_id(access_token)
        self.write(values)

    def _token_request(self, payload):
        response = self._http_request(
            "POST",
            TOKEN_URL,
            data=parse.urlencode(payload).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if not response.get("access_token"):
            raise ValidationError(_("Microsoft login did not return an access token."))
        return response

    def _get_account_profile(self, token):
        return self._http_request("GET", "%s/me" % GRAPH_API_ROOT, headers=self._auth_headers(token))

    def _ensure_drive_id(self, token):
        self.ensure_one()
        if not self.drive_id:
            self.write({"drive_id": self._resolve_drive_id(token)})
        return self.drive_id

    def _resolve_drive_id(self, token):
        response = self._http_request("GET", "%s/me/drive?$select=id" % GRAPH_API_ROOT, headers=self._auth_headers(token))
        drive_id = response.get("id")
        if not drive_id:
            raise ValidationError(_("Could not resolve the OneDrive drive ID for the connected account."))
        return drive_id

    def _list_root_folders(self, token):
        if self.root_folder_id:
            return self._list_child_items(token, self.root_folder_id)
        if not self.root_folder_path:
            raise ValidationError(_("Please configure a Root Folder ID or Root Folder Path."))
        encoded_path = "/".join(parse.quote(segment) for segment in self.root_folder_path.strip("/").split("/") if segment)
        if self.drive_id:
            url = "%s/drives/%s/root:/%s:/children" % (GRAPH_API_ROOT, parse.quote(self.drive_id), encoded_path)
        else:
            url = "%s/me/drive/root:/%s:/children" % (GRAPH_API_ROOT, encoded_path)
        return self._paged_items(url, token)

    def _list_child_items(self, token, item_id):
        if self.drive_id:
            url = "%s/drives/%s/items/%s/children" % (
                GRAPH_API_ROOT,
                parse.quote(self.drive_id),
                parse.quote(item_id),
            )
        else:
            url = "%s/me/drive/items/%s/children" % (GRAPH_API_ROOT, parse.quote(item_id))
        return self._paged_items(url, token)

    def _paged_items(self, url, token):
        items = []
        next_url = url
        while next_url:
            response = self._http_request("GET", next_url, headers=self._auth_headers(token))
            items.extend(response.get("value", []))
            next_url = response.get("@odata.nextLink")
        return items

    def _download_file(self, token, remote_file):
        download_url = remote_file.get("@microsoft.graph.downloadUrl")
        if download_url:
            return self._http_request("GET", download_url, expect_json=False)
        if self.drive_id:
            url = "%s/drives/%s/items/%s/content" % (
                GRAPH_API_ROOT,
                parse.quote(self.drive_id),
                parse.quote(remote_file.get("id")),
            )
        else:
            url = "%s/me/drive/items/%s/content" % (GRAPH_API_ROOT, parse.quote(remote_file.get("id")))
        return self._http_request("GET", url, headers=self._auth_headers(token), expect_json=False)

    def _auth_headers(self, token):
        return {
            "Authorization": "Bearer %s" % token,
            "Accept": "application/json",
        }

    def _http_request(self, method, url, headers=None, data=None, expect_json=True):
        headers = headers or {}
        req = request.Request(url, data=data, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=60) as response:
                payload = response.read()
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ValidationError(_("OneDrive request failed: %s") % detail)
        except error.URLError as exc:
            raise ValidationError(_("Could not reach OneDrive: %s") % exc.reason)

        if not expect_json:
            return payload
        if not payload:
            return {}
        return json.loads(payload.decode("utf-8"))


class CduOneDrivePollLog(models.Model):
    _name = "cdu.onedrive.poll.log"
    _description = "CDU OneDrive Poll Log"
    _order = "started_at desc, create_date desc"

    name = fields.Char(required=True)
    source_id = fields.Many2one("cdu.onedrive.source", required=True, ondelete="cascade")
    trigger_mode = fields.Selection(
        [("manual", "Manual"), ("scheduled", "Scheduled")],
        required=True,
        default="scheduled",
    )
    started_at = fields.Datetime(required=True)
    finished_at = fields.Datetime(readonly=True)
    state = fields.Selection(
        [
            ("running", "Running"),
            ("completed", "Completed"),
            ("completed_with_errors", "Completed With Errors"),
            ("failed", "Failed"),
        ],
        default="running",
        required=True,
    )
    folders_seen = fields.Integer(readonly=True)
    matched_folders = fields.Integer(readonly=True)
    unmatched_folders = fields.Integer(readonly=True)
    files_discovered = fields.Integer(readonly=True)
    files_downloaded = fields.Integer(readonly=True)
    imports_created = fields.Integer(readonly=True)
    duplicates_skipped = fields.Integer(readonly=True)
    failed_files = fields.Integer(readonly=True)
    unmatched_folder_names = fields.Text(readonly=True)
    skipped_file_names = fields.Text(readonly=True)
    result_summary = fields.Text(readonly=True)
    error_message = fields.Text(readonly=True)
    report_run_ids = fields.One2many("cdu.report.run", "poll_log_id", string="Imported Reports")
