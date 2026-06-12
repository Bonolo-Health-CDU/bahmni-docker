import json
import re
import time
import uuid
from datetime import datetime
from urllib import error, request

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class CduCollectionPointSyncConfig(models.Model):
    _name = "cdu.collection.point.sync.config"
    _description = "CDU Collection Point Sync Configuration"
    _order = "name"

    name = fields.Char(required=True, default="Collect-and-Go Pickup Point Sync")
    active = fields.Boolean(default=True)
    enabled = fields.Boolean(default=True)
    request_endpoint = fields.Char(
        required=True,
        default="https://api.bonolohealth.gov.ls/api/Ministry%20of%20Health",
    )
    response_endpoint_template = fields.Char(
        required=True,
        default="https://api.bonolohealth.gov.ls/api/Ministry%20of%20Health/getMessage/{guid}",
    )
    request_type = fields.Integer(default=0, required=True)
    request_method = fields.Char(default="GetLocation", required=True)
    poll_attempts = fields.Integer(default=10, required=True)
    poll_interval_seconds = fields.Integer(default=3, required=True)
    resolve_cdu_location = fields.Boolean(
        string="Resolve CDU Location ID",
        default=True,
        help="When enabled, the sync will also look for the CDU location in the Collect-and-Go location response.",
    )
    cdu_location_lookup = fields.Char(
        string="CDU Location Lookup",
        default="CDU",
        help="Name, code, or remote LocationID used to identify the CDU location in the Collect-and-Go location response.",
    )
    resolved_cdu_location_id = fields.Char(
        string="Resolved CDU Location ID",
        readonly=True,
        help="The Collect-and-Go LocationID saved into cdu.collect_go.cdu_location_id.",
    )
    last_sync_at = fields.Datetime(readonly=True)
    last_sync_result = fields.Text(readonly=True)
    last_sync_log_id = fields.Many2one("cdu.collection.point.sync.log", readonly=True)
    sync_log_ids = fields.One2many("cdu.collection.point.sync.log", "config_id", string="Sync Logs")

    _sql_constraints = [
        ("positive_poll_attempts", "CHECK(poll_attempts > 0)", "Poll attempts must be greater than zero."),
        ("positive_poll_interval", "CHECK(poll_interval_seconds > 0)", "Poll interval must be greater than zero."),
    ]

    @api.constrains("enabled", "active")
    def _check_single_enabled_config(self):
        for record in self:
            if not (record.enabled and record.active):
                continue
            count = self.search_count([
                ("id", "!=", record.id),
                ("enabled", "=", True),
                ("active", "=", True),
            ])
            if count:
                raise ValidationError(_("Only one active enabled collection point sync configuration is supported."))

    def action_sync_now(self):
        for config in self:
            config._sync_locations()

    def _sync_locations(self):
        self.ensure_one()
        reference = str(uuid.uuid4())
        request_payload = {
            "Reference": reference,
            "Type": self.request_type,
            "Method": self.request_method,
            "Body": "{}",
        }
        log = self.env["cdu.collection.point.sync.log"].create({
            "name": "%s - %s" % (self.name, reference),
            "config_id": self.id,
            "reference_guid": reference,
            "requested_at": fields.Datetime.now(),
            "state": "requested",
            "request_payload": json.dumps(request_payload, indent=2, sort_keys=True),
        })
        self.write({
            "last_sync_log_id": log.id,
            "last_sync_result": _("Collection point sync request sent."),
        })

        try:
            post_response = self._http_json_request("POST", self.request_endpoint, request_payload)
            log.write({
                "request_acknowledged_at": fields.Datetime.now(),
                "post_response_payload": self._to_pretty_json(post_response),
            })
            message_payload = self._poll_for_message(reference)
            normalized = self._normalize_locations_payload(message_payload)
            counts = self._upsert_collection_points(normalized)
            cdu_location_id = self._resolve_cdu_location_id(normalized)
            summary = _(
                "Locations fetched: %(fetched)s, created: %(created)s, updated: %(updated)s."
            ) % {
                "fetched": len(normalized),
                "created": counts["created"],
                "updated": counts["updated"],
            }
            if cdu_location_id:
                summary = "%s %s" % (
                    summary,
                    _("CDU LocationID resolved: %s.") % cdu_location_id,
                )
            elif self.resolve_cdu_location:
                summary = "%s %s" % (
                    summary,
                    _("CDU LocationID was not found using lookup '%s'.") % self.cdu_location_lookup,
                )
            log.write({
                "responded_at": fields.Datetime.now(),
                "state": "completed",
                "raw_response_payload": self._to_pretty_json(message_payload),
                "normalized_payload": self._to_pretty_json(normalized),
                "fetched_count": len(normalized),
                "created_count": counts["created"],
                "updated_count": counts["updated"],
                "result_summary": summary,
            })
            self.write({
                "last_sync_at": fields.Datetime.now(),
                "last_sync_result": summary,
                "last_sync_log_id": log.id,
                "resolved_cdu_location_id": cdu_location_id or self.resolved_cdu_location_id,
            })
        except Exception as exc:
            message = str(exc)
            log.write({
                "responded_at": fields.Datetime.now(),
                "state": "failed",
                "error_message": message,
                "result_summary": message,
            })
            self.write({
                "last_sync_result": message,
                "last_sync_log_id": log.id,
            })
            raise
        return True

    def _poll_for_message(self, reference):
        self.ensure_one()
        url = self.response_endpoint_template.format(guid=reference)
        last_payload = False
        for attempt in range(self.poll_attempts):
            payload = self._http_json_request("GET", url)
            last_payload = payload
            if self._response_contains_message(payload):
                return payload
            if attempt < self.poll_attempts - 1:
                time.sleep(self.poll_interval_seconds)
        raise UserError(_("No response message was available for reference %s after %s attempts.") % (reference, self.poll_attempts))

    def _response_contains_message(self, payload):
        expanded = self._expand_json_strings(payload)
        if not expanded:
            return False
        if isinstance(expanded, dict) and not any(expanded.values()):
            return False
        if isinstance(expanded, list) and not expanded:
            return False
        return True

    def _normalize_locations_payload(self, payload):
        expanded = self._expand_json_strings(payload)
        location_dicts = self._collect_location_dicts(expanded)
        normalized = []
        for item in location_dicts:
            mapped = self._map_location_dict(item)
            if mapped:
                normalized.append(mapped)
        if not normalized:
            raise UserError(_("The Collect-and-Go response did not contain any recognizable locations."))
        return normalized

    def _collect_location_dicts(self, node):
        candidates = []
        if isinstance(node, list):
            if node and all(isinstance(item, dict) for item in node):
                if any(self._looks_like_location_dict(item) for item in node):
                    return node
            for item in node:
                candidates.extend(self._collect_location_dicts(item))
        elif isinstance(node, dict):
            if self._looks_like_location_dict(node):
                candidates.append(node)
            for value in node.values():
                candidates.extend(self._collect_location_dicts(value))
        return candidates

    def _looks_like_location_dict(self, value):
        if not isinstance(value, dict):
            return False
        keys = {key.lower() for key in value.keys()}
        return bool(keys & {
            "location",
            "locationname",
            "name",
            "pickupname",
            "code",
            "locationcode",
            "id",
            "locationid",
            "reference",
        })

    def _map_location_dict(self, value):
        name = self._first_non_empty(value, [
            "Location",
            "LocationName",
            "Name",
            "PickupName",
            "Description",
            "Title",
        ])
        if not name:
            return False
        address = value.get("Address") or {}
        country = address.get("Country") or {}
        region = value.get("Region") or {}
        telephone = value.get("Telephone") or {}
        remote_location_id = self._first_non_empty(value, [
            "LocationID",
            "LocationId",
            "Id",
            "ID",
        ])
        remote_sync_key = "%s|%s" % (
            str(remote_location_id or "").strip(),
            name.strip().lower(),
        )
        code = self._first_non_empty(value, [
            "Code",
            "LocationCode",
            "ShortCode",
        ]) or self._slugify("%s-%s" % (remote_location_id or "", name))
        return {
            "name": name.strip(),
            "code": code.strip() if isinstance(code, str) else str(code),
            "remote_sync_key": remote_sync_key,
            "remote_location_id": str(remote_location_id).strip() if remote_location_id not in (None, False, "") else False,
            "remote_location_type": value.get("LocationType") or False,
            "address_id": address.get("AddressID") or False,
            "city": address.get("City") or False,
            "address_line_1": address.get("Line1") or False,
            "address_line_2": address.get("Line2") or False,
            "address_line_3": address.get("Line3") or False,
            "postal_code": address.get("PostalCode") or False,
            "country_code_id": country.get("CountryCodeID") or False,
            "country_name": country.get("CountryName") or False,
            "region_id": region.get("RegionID") or False,
            "region_name": region.get("Name") or False,
            "gps_coordinates": value.get("GPSCoordinates") or False,
            "shop_name": value.get("ShopName") or False,
            "shop_number": value.get("ShopNumber") or False,
            "telephone_id": telephone.get("TelephoneID") or False,
            "telephone_number": telephone.get("Number") or False,
            "telephone_type": telephone.get("Type") or False,
            "effective_date": self._parse_datetime(value.get("EffectiveDate")),
            "expiry_date": self._parse_datetime(value.get("ExpiryDate")),
            "remote_timestamp": self._parse_datetime(value.get("TimeStamp")),
            "owner_id": str(value.get("OwnerID")).strip() if value.get("OwnerID") not in (None, False, "") else False,
            "external_reference": str(remote_location_id).strip() if remote_location_id not in (None, False, "") else False,
        }

    def _upsert_collection_points(self, locations):
        CollectionPoint = self.env["cdu.collection.point"]
        counts = {"created": 0, "updated": 0}
        for location in locations:
            point = False
            if location["remote_sync_key"]:
                point = CollectionPoint.search([("remote_sync_key", "=", location["remote_sync_key"])], limit=1)
            if not point and location["external_reference"]:
                point = CollectionPoint.search([
                    ("external_reference", "=", location["external_reference"]),
                    ("name", "=", location["name"]),
                ], limit=1)
            if not point:
                point = CollectionPoint.search([("code", "=", location["code"])], limit=1)
            if not point:
                point = CollectionPoint.search([("name", "=", location["name"])], limit=1)

            vals = {
                "name": location["name"],
                "code": location["code"],
                "remote_sync_key": location["remote_sync_key"],
                "remote_location_id": location["remote_location_id"],
                "remote_location_type": location["remote_location_type"],
                "address_id": location["address_id"],
                "city": location["city"],
                "address_line_1": location["address_line_1"],
                "address_line_2": location["address_line_2"],
                "address_line_3": location["address_line_3"],
                "postal_code": location["postal_code"],
                "country_code_id": location["country_code_id"],
                "country_name": location["country_name"],
                "region_id": location["region_id"],
                "region_name": location["region_name"],
                "gps_coordinates": location["gps_coordinates"],
                "shop_name": location["shop_name"],
                "shop_number": location["shop_number"],
                "telephone_id": location["telephone_id"],
                "telephone_number": location["telephone_number"],
                "telephone_type": location["telephone_type"],
                "effective_date": location["effective_date"],
                "expiry_date": location["expiry_date"],
                "remote_timestamp": location["remote_timestamp"],
                "owner_id": location["owner_id"],
                "external_reference": location["external_reference"],
                "active": True,
                "last_synced_at": fields.Datetime.now(),
                "sync_source": "Collect-and-Go",
            }
            if point:
                point.write(vals)
                counts["updated"] += 1
            else:
                CollectionPoint.create(vals)
                counts["created"] += 1
        return counts

    def _resolve_cdu_location_id(self, locations):
        self.ensure_one()
        if not self.resolve_cdu_location:
            return False
        lookup = (self.cdu_location_lookup or "").strip().lower()
        if not lookup:
            return False

        for location in locations:
            candidates = [
                location.get("name"),
                location.get("code"),
                location.get("remote_location_id"),
                location.get("external_reference"),
            ]
            normalized_candidates = [
                str(candidate or "").strip().lower()
                for candidate in candidates
                if str(candidate or "").strip()
            ]
            if any(candidate == lookup or lookup in candidate for candidate in normalized_candidates):
                location_id = location.get("external_reference") or location.get("remote_location_id")
                if location_id:
                    location_id = str(location_id).strip()
                    self.env["ir.config_parameter"].sudo().set_param(
                        "cdu.collect_go.cdu_location_id",
                        location_id,
                    )
                    return location_id
        return False

    def _expand_json_strings(self, value):
        if isinstance(value, str):
            stripped = value.strip()
            if stripped and stripped[0] in "[{":
                try:
                    return self._expand_json_strings(json.loads(stripped))
                except Exception:
                    return value
            return value
        if isinstance(value, list):
            return [self._expand_json_strings(item) for item in value]
        if isinstance(value, dict):
            return {key: self._expand_json_strings(item) for key, item in value.items()}
        return value

    def _first_non_empty(self, data, keys):
        for key in keys:
            value = data.get(key)
            if value not in (None, "", False):
                return value
        return False

    def _slugify(self, value):
        slug = re.sub(r"[^A-Za-z0-9]+", "-", value or "").strip("-").upper()
        return slug[:32] or "PICKUP"

    def _parse_datetime(self, value):
        if not value:
            return False
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
            try:
                return fields.Datetime.to_string(datetime.strptime(value, fmt))
            except Exception:
                continue
        return False

    def _http_json_request(self, method, url, payload=None):
        headers = {"Accept": "application/json"}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        req = request.Request(url, data=data, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=60) as response:
                body = response.read().decode("utf-8", errors="replace")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise UserError(_("Collect-and-Go request failed: %s") % detail)
        except error.URLError as exc:
            raise UserError(_("Could not reach Collect-and-Go: %s") % exc.reason)
        if not body:
            return {}
        try:
            return json.loads(body)
        except ValueError:
            return {"raw": body}

    def _to_pretty_json(self, payload):
        if payload in (False, None):
            return False
        try:
            return json.dumps(payload, indent=2, sort_keys=True)
        except TypeError:
            return str(payload)


class CduCollectionPointSyncLog(models.Model):
    _name = "cdu.collection.point.sync.log"
    _description = "CDU Collection Point Sync Log"
    _order = "requested_at desc, create_date desc"

    name = fields.Char(required=True)
    config_id = fields.Many2one("cdu.collection.point.sync.config", required=True, ondelete="cascade")
    reference_guid = fields.Char(required=True, copy=False, index=True)
    requested_at = fields.Datetime(required=True)
    request_acknowledged_at = fields.Datetime(readonly=True)
    responded_at = fields.Datetime(readonly=True)
    state = fields.Selection(
        [
            ("requested", "Requested"),
            ("completed", "Completed"),
            ("failed", "Failed"),
        ],
        default="requested",
        required=True,
    )
    request_payload = fields.Text(readonly=True)
    post_response_payload = fields.Text(readonly=True)
    raw_response_payload = fields.Text(readonly=True)
    normalized_payload = fields.Text(readonly=True)
    fetched_count = fields.Integer(readonly=True)
    created_count = fields.Integer(readonly=True)
    updated_count = fields.Integer(readonly=True)
    result_summary = fields.Text(readonly=True)
    error_message = fields.Text(readonly=True)
