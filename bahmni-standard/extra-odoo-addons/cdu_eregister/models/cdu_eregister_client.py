import json
import logging
from urllib.parse import urlsplit

import requests

from odoo import _, models
from odoo.exceptions import UserError


_logger = logging.getLogger(__name__)

FHIR_JSON = "application/fhir+json"
LOG_BODY_LIMIT = 20000


class FhirRequestError(UserError):
    """A FHIR call that failed, with the HTTP status and server diagnostics."""

    def __init__(self, message, status_code=0, body=None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body or {}


class CduEregisterClient(models.AbstractModel):
    """Thin FHIR REST client for the prescription repository behind OpenHIM.

    Every call goes through OpenHIM with the CDU client's basic-auth
    credentials; OpenHIM decides which methods and paths the CDU may use.
    """

    _name = "cdu.eregister.client"
    _description = "CDU eRegister FHIR Client"

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    def _param(self, key, default=""):
        value = self.env["ir.config_parameter"].sudo().get_param(key)
        return (value if value not in (None, False) else default) or default

    def _base_url(self):
        return self._param("cdu.eregister.base_url").strip().rstrip("/")

    def _cdu_organization_id(self):
        return self._param("cdu.eregister.cdu_organization_id").strip()

    def _timeout(self):
        try:
            return max(int(self._param("cdu.eregister.timeout_seconds", "30")), 1)
        except ValueError:
            return 30

    def _page_size(self):
        try:
            return min(max(int(self._param("cdu.eregister.page_size", "50")), 1), 200)
        except ValueError:
            return 50

    def is_configured(self):
        return bool(
            self._base_url()
            and self._param("cdu.eregister.username")
            and self._param("cdu.eregister.password")
            and self._cdu_organization_id()
        )

    def _ensure_configured(self):
        if not self.is_configured():
            raise UserError(
                _(
                    "The eRegister integration is not configured. Set the FHIR base URL, "
                    "username, password and CDU organization in "
                    "CDU > Configuration > eRegister Integration."
                )
            )

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------
    def get(self, path, params=None, call_type="READ_RESOURCE", prescription=False, log_success=False):
        """GET a resource or search. Searches bypass HAPI's search cache."""
        return self._request(
            "GET",
            path,
            params=params,
            headers={"Cache-Control": "no-cache"},
            call_type=call_type,
            prescription=prescription,
            log_success=log_success,
        )

    def put(self, path, resource, version_id=None, call_type="PUSH_STATUS", prescription=False):
        headers = {"Content-Type": FHIR_JSON}
        if version_id:
            headers["If-Match"] = 'W/"%s"' % version_id
        return self._request(
            "PUT",
            path,
            body=resource,
            headers=headers,
            call_type=call_type,
            prescription=prescription,
            log_success=True,
        )

    def read_reference(self, reference, prescription=False):
        """Read a relative reference such as 'Organization/A2681'."""
        if not reference or "/" not in reference or reference.startswith("urn:"):
            raise UserError(_("Cannot read FHIR reference %r.") % reference)
        return self.get(reference, prescription=prescription)

    def search_all(self, path, params, call_type, max_pages=20):
        """Run a search and follow Bundle 'next' links.

        Returns (resources_by_reference, match_resources, sources). Included
        resources are indexed as 'Type/id' so references inside matches can be
        resolved without another call. sources maps each match's 'Type/id' to
        the HTTP exchange that returned it: {"method", "url", "status_code"}.
        """
        index = {}
        matches = []
        sources = {}
        request_path, request_params = path, params
        for _page in range(max_pages):
            status_code, bundle = self._exchange(
                "GET",
                request_path,
                params=request_params,
                headers={"Cache-Control": "no-cache"},
                call_type=call_type,
            )
            source = {
                "method": "GET",
                "url": self._endpoint(self._url(request_path), request_params),
                "status_code": status_code,
            }
            for entry in bundle.get("entry") or []:
                resource = entry.get("resource") or {}
                if not resource.get("resourceType"):
                    continue
                reference = "%s/%s" % (resource["resourceType"], resource.get("id"))
                index[reference] = resource
                if (entry.get("search") or {}).get("mode", "match") == "match":
                    matches.append(resource)
                    sources[reference] = source
            next_url = next(
                (link.get("url") for link in bundle.get("link") or [] if link.get("relation") == "next"),
                None,
            )
            if not next_url:
                break
            request_path, request_params = next_url, None
        return index, matches, sources

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------
    def _url(self, path):
        """Absolute URL for a relative path or a server-generated link.

        HAPI builds paging links from its public address, which is not
        necessarily reachable from this container. Keep the path and query and
        re-root them on the configured base URL.
        """
        base = self._base_url()
        if not path.startswith(("http://", "https://")):
            return "%s/%s" % (base, path.lstrip("/"))
        link = urlsplit(path)
        base_path = urlsplit(base).path.rstrip("/")
        link_path = link.path
        if base_path and link_path.startswith(base_path):
            link_path = link_path[len(base_path):]
        url = base + (link_path.rstrip("/") if link_path.strip("/") else "")
        return url + ("?" + link.query if link.query else "")

    def _send(self, method, url, params=None, body=None, headers=None):
        """Perform the HTTP call. Returns (status_code, text). Patched in tests."""
        response = requests.request(
            method,
            url,
            params=params,
            data=json.dumps(body) if body is not None else None,
            headers=headers,
            auth=(self._param("cdu.eregister.username"), self._param("cdu.eregister.password")),
            timeout=self._timeout(),
        )
        return response.status_code, response.text

    def _request(self, method, path, params=None, body=None, headers=None,
                 call_type="READ_RESOURCE", prescription=False, log_success=False):
        return self._exchange(
            method,
            path,
            params=params,
            body=body,
            headers=headers,
            call_type=call_type,
            prescription=prescription,
            log_success=log_success,
        )[1]

    def _exchange(self, method, path, params=None, body=None, headers=None,
                  call_type="READ_RESOURCE", prescription=False, log_success=False):
        """Perform a call and return (status_code, json_payload)."""
        self._ensure_configured()
        url = self._url(path)
        all_headers = {"Accept": FHIR_JSON}
        all_headers.update(headers or {})

        status_code = 0
        text = ""
        payload = {}
        error_message = False
        try:
            status_code, text = self._send(method, url, params=params, body=body, headers=all_headers)
            payload = self._json(text)
            if not 200 <= status_code < 300:
                error_message = self._error_message(status_code, payload, text)
        except requests.RequestException as exc:
            error_message = _("Could not reach the FHIR repository at %(url)s: %(error)s") % {
                "url": url,
                "error": exc,
            }

        if error_message or log_success:
            self._log(
                call_type=call_type,
                method=method,
                url=url,
                params=params,
                body=body,
                status_code=status_code,
                response_text=text,
                success=not error_message,
                error_message=error_message,
                prescription=prescription,
            )
        if error_message:
            _logger.warning("eRegister FHIR %s %s failed: %s", method, url, error_message)
            raise FhirRequestError(error_message, status_code=status_code, body=payload)
        return status_code, payload

    def _json(self, text):
        if not text:
            return {}
        try:
            value = json.loads(text)
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}

    def _error_message(self, status_code, payload, text):
        if payload.get("resourceType") == "OperationOutcome":
            details = [
                issue.get("diagnostics") or (issue.get("details") or {}).get("text") or issue.get("code")
                for issue in payload.get("issue") or []
                if issue.get("severity") in ("error", "fatal")
            ]
            details = [detail for detail in details if detail]
            if details:
                return "HTTP %s: %s" % (status_code, "; ".join(details))
        return "HTTP %s: %s" % (status_code, (text or "").strip()[:500] or _("no response body"))

    def _log(self, call_type, method, url, params, body, status_code, response_text,
             success, error_message, prescription=False):
        return self.env["cdu.eregister.api.log"].sudo().create(
            {
                "call_type": call_type,
                "endpoint": self._endpoint(url, params),
                "http_method": method,
                "request_payload": json.dumps(body, indent=2)[:LOG_BODY_LIMIT] if body is not None else False,
                "response_body": (response_text or "")[:LOG_BODY_LIMIT] or False,
                "http_status_code": status_code or 0,
                "success": success,
                "error_message": error_message or False,
                "prescription_id": prescription.id if prescription else False,
            }
        )

    def _endpoint(self, url, params=None):
        endpoint = url
        if params:
            endpoint += " " + json.dumps(params, sort_keys=True)
        return endpoint[:2000]

    def log_resource_outcome(self, call_type, source, resource, success, error_message=False, prescription=False):
        """Record what happened to a resource returned by a search.

        The row carries the HTTP exchange that delivered the resource (method,
        URL and status from `source`, as returned by search_all) and the
        resource itself as the response, plus the processing outcome.
        """
        reference = "%s/%s" % (resource.get("resourceType"), resource.get("id"))
        return self.env["cdu.eregister.api.log"].sudo().create(
            {
                "call_type": call_type,
                "endpoint": source["url"],
                "http_method": source["method"],
                "http_status_code": source["status_code"],
                "response_body": json.dumps(resource, indent=2)[:LOG_BODY_LIMIT],
                "success": success,
                "error_message": error_message or False,
                "prescription_id": prescription.id if prescription else False,
                "fhir_reference": reference,
            }
        )
