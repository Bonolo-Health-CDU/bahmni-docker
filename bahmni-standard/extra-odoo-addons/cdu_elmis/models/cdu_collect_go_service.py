import json
import uuid
from urllib import error, request

from odoo import _, fields, models
from odoo.exceptions import UserError


class CduCollectGoService(models.AbstractModel):
    _name = "cdu.collect.go.service"
    _description = "CDU Collect-and-Go Service"

    def submit_box_create_parcel(self, box):
        return self._submit_box_create_parcel(box)

    def retry_box_create_parcel(self, box):
        box.ensure_one()
        if box.collect_go_status != "failed":
            raise UserError(_("Only failed Collect-and-Go submissions can be retried."))

        original_log = self._retry_root_log(box.collect_go_last_log_id)
        if not original_log:
            raise UserError(_("This box does not have a failed Collect-and-Go log to retry."))

        retry_count = len(original_log.retry_ids) + 1
        max_retry_count = self._max_retry_count()
        if retry_count > max_retry_count:
            box.write({"collect_go_status": "retry_limit_reached"})
            raise UserError(
                _(
                    "Collect-and-Go retry limit reached. "
                    "Maximum allowed retries: %(max)s."
                )
                % {"max": max_retry_count}
            )

        return self._submit_box_create_parcel(
            box,
            retry_of=original_log,
            retry_count=retry_count,
        )

    def _submit_box_create_parcel(self, box, retry_of=False, retry_count=0):
        box.ensure_one()
        self._validate_box_for_submission(box)

        reference = str(uuid.uuid4())
        endpoint = self._build_endpoint()
        body = {"Parcels": [self._build_box_parcel(box)]}
        payload = {
            "Reference": reference,
            "Type": 0,
            "Method": "CreateParcel",
            "Body": json.dumps(body, separators=(",", ":")),
        }

        status_code = False
        bridge_status_code = False
        response_text = ""
        success = False
        error_message = False

        try:
            response = self._http_request("POST", endpoint, payload)
            status_code = response["status_code"]
            response_text = response["body"]
            response_payload = self._safe_json_loads(response_text)
            bridge_status_code = response_payload.get("statusCode") if response_payload else False
            success = status_code == 200 and bridge_status_code == 200
            if not success:
                error_message = self._extract_bridge_error(response_payload, response_text)
        except UserError as exc:
            error_message = str(exc)

        log = self._log_call(
            call_type="CREATE_PARCEL",
            endpoint=endpoint,
            http_method="POST",
            request_payload=payload,
            response_body=response_text,
            http_status_code=status_code,
            bridge_status_code=bridge_status_code,
            success=success,
            error_message=error_message,
            box=box,
            reference_guid=reference,
            retry_count=retry_count,
            retry_of=retry_of,
        )

        if success:
            box.write(
                {
                    "collect_go_status": "submitted",
                    "collect_go_reference_guid": reference,
                    "collect_go_last_log_id": log.id,
                    "collect_go_submitted_at": fields.Datetime.now(),
                    "collect_go_error": False,
                }
            )
            return log

        box.write(
            {
                "collect_go_status": "failed",
                "collect_go_reference_guid": reference,
                "collect_go_last_log_id": log.id,
                "collect_go_error": error_message,
            }
        )
        if retry_count and retry_count >= self._max_retry_count():
            box.collect_go_status = "retry_limit_reached"
        raise UserError(_("Collect-and-Go submission failed: %s") % error_message)

    def poll_box_message(self, box, raise_on_error=True):
        box.ensure_one()
        if box.collect_go_status != "submitted":
            raise UserError(_("Only boxes submitted to Collect-and-Go can be checked."))
        if not box.collect_go_reference_guid:
            raise UserError(_("This box does not have a Collect-and-Go reference GUID."))

        endpoint = self._build_endpoint("getMessage/%s" % box.collect_go_reference_guid)
        status_code = False
        response_text = ""
        success = False
        error_message = False
        message_payload = {}

        try:
            response = self._http_request("GET", endpoint)
            status_code = response["status_code"]
            response_text = response["body"]
            message_payload = self._safe_json_loads(response_text)
            message_type = message_payload.get("type")
            success = status_code == 200 and message_type == 1
            if not success:
                error_message = self._extract_message_error(message_payload, response_text)
        except UserError as exc:
            error_message = str(exc)

        log = self._log_call(
            call_type="GET_MESSAGE",
            endpoint=endpoint,
            http_method="GET",
            response_body=response_text,
            http_status_code=status_code,
            success=success,
            error_message=error_message,
            box=box,
            reference_guid=box.collect_go_reference_guid,
        )

        if success:
            box._mark_collect_go_processed(log)
            return {
                "state": "processed",
                "log": log,
                "error_message": False,
            }

        if message_payload.get("type") == 2:
            box.write(
                {
                    "collect_go_status": "failed",
                    "collect_go_last_log_id": log.id,
                    "collect_go_error": error_message,
                }
            )
            if raise_on_error:
                raise UserError(_("Collect-and-Go processing failed: %s") % error_message)
            return {
                "state": "failed",
                "log": log,
                "error_message": error_message,
            }

        box.write(
            {
                "collect_go_last_log_id": log.id,
                "collect_go_error": error_message,
            }
        )
        if raise_on_error:
            raise UserError(_("Collect-and-Go response is not ready yet: %s") % error_message)
        return {
            "state": "not_ready",
            "log": log,
            "error_message": error_message,
        }

    def cron_poll_parcel_status_updates(self):
        params = self.env["ir.config_parameter"].sudo()
        if params.get_param("cdu.collect_go.status_poll_enabled", "True") == "False":
            return False
        return self.poll_parcel_status_updates(raise_on_error=False)

    def poll_parcel_status_updates(self, boxes=False, raise_on_error=True):
        endpoint = self._build_endpoint("getParcelStatus")
        status_code = False
        response_text = ""
        success = False
        error_message = False
        status_payload = {}
        items = []

        try:
            response = self._http_request("GET", endpoint)
            status_code = response["status_code"]
            response_text = response["body"]
            status_payload = self._safe_json_loads(response_text)
            items = self._extract_parcel_status_items(status_payload)
            success = status_code == 200
            if not success:
                error_message = self._extract_message_error(status_payload, response_text)
        except UserError as exc:
            error_message = str(exc)

        self._log_call(
            call_type="GET_PARCEL_STATUS",
            endpoint=endpoint,
            http_method="GET",
            response_body=response_text,
            http_status_code=status_code,
            success=success,
            error_message=error_message,
        )

        if not success:
            if raise_on_error:
                raise UserError(_("Collect-and-Go parcel status poll failed: %s") % error_message)
            return {
                "success": False,
                "received_count": 0,
                "matched_count": 0,
                "error_message": error_message,
            }

        matched_count = self._apply_parcel_status_items(items, boxes=boxes)
        return {
            "success": True,
            "received_count": len(items),
            "matched_count": matched_count,
            "error_message": False,
        }

    def action_test_connection(self):
        endpoint = self._build_endpoint("TestConnection")
        status_code = False
        response_text = ""
        success = False
        error_message = False

        try:
            response = self._http_request("GET", endpoint)
            status_code = response["status_code"]
            response_text = response["body"]
            success = status_code == 200 and response_text.strip().lower() == "true"
            if not success:
                error_message = _("Unexpected Collect-and-Go test response: %s") % response_text
        except UserError as exc:
            error_message = str(exc)

        self._log_call(
            call_type="TEST_CONNECTION",
            endpoint=endpoint,
            http_method="GET",
            response_body=response_text,
            http_status_code=status_code,
            success=success,
            error_message=error_message,
        )

        if not success:
            raise UserError(_("Collect-and-Go test connection failed: %s") % error_message)

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Collect-and-Go connection successful"),
                "message": _("The configured Collect-and-Go endpoint responded successfully."),
                "type": "success",
                "sticky": False,
            },
        }

    def _build_endpoint(self, suffix=None):
        params = self.env["ir.config_parameter"].sudo()
        base_url = (params.get_param("cdu.collect_go.base_url") or "").strip()
        if not base_url:
            raise UserError(_("Configure the Collect-and-Go Base URL before testing the connection."))
        endpoint = base_url.rstrip("/")
        if suffix:
            endpoint = "%s/%s" % (endpoint, suffix.lstrip("/"))
        return endpoint

    def _validate_box_for_submission(self, box):
        if box.state != "confirmed":
            raise UserError(_("Confirm the box before submitting it to Collect-and-Go."))
        if box.dispatch_status == "dispatched":
            raise UserError(_("This box has already been dispatched."))
        if box.collect_go_status in ("submitted", "processed", "retry_limit_reached"):
            raise UserError(_("This box has already been submitted to Collect-and-Go."))
        if not box.line_ids:
            raise UserError(_("Add at least one parcel before submitting the box."))
        if not box.collection_point_id:
            raise UserError(_("The box must have a collection point before submission."))
        if not box.collection_point_id.external_reference:
            raise UserError(
                _(
                    "%s does not have a Collect-and-Go Reference. "
                    "Set it on the collection point before submitting this box."
                )
                % box.collection_point_id.display_name
            )

        missing_parcels = box.line_ids.filtered(lambda line: not line.parcel_reference)
        if missing_parcels:
            raise UserError(_("Every parcel in the box must have a parcel reference."))

    def _build_box_parcel(self, box):
        params = self.env["ir.config_parameter"].sudo()
        now = self._collect_go_datetime()
        collection_location = box.collection_point_id.external_reference
        return {
            "ChildParcels": [self._build_child_parcel(line, now) for line in box.line_ids],
            "Customer": None,
            "ExternalReference": box.name,
            "LocationID": collection_location,
            "ParcelID": None,
            "ParcelStatusType": 0,
            "Pin": None,
            "ScheduledDeliveryDate": now,
            "TrackingStatuses": [self._build_tracking_status(now)],
            "Volume": box.parcel_count,
            "TemplateID": None,
            "ParcelType": self._int_param(params, "cdu.collect_go.box_parcel_type", 2),
        }

    def _build_child_parcel(self, line, now):
        params = self.env["ir.config_parameter"].sudo()
        prescription = line.prescription_id
        collection_location = line.collection_point_id.external_reference
        return {
            "ChildParcels": None,
            "Customer": self._build_customer(prescription),
            "ExternalReference": line.parcel_reference,
            "LocationID": collection_location,
            "ParcelID": None,
            "ParcelStatusType": 0,
            "Pin": None,
            "ScheduledDeliveryDate": self._date_to_collect_go_datetime(
                line.next_drug_pickup_date
            )
            or now,
            "TrackingStatuses": [self._build_tracking_status(now)],
            "Volume": None,
            "TemplateID": None,
            "ParcelType": self._int_param(params, "cdu.collect_go.standard_parcel_type", 0),
        }

    def _build_customer(self, prescription):
        first_name, surname = self._split_patient_name(prescription.patient_first_name)
        return {
            "ContactNumber": prescription.patient_phone or "",
            "CustomerExternalReference": prescription.patient_identifier or prescription.name,
            "IDNumber": prescription.national_id or "",
            "Name": first_name,
            "NextCollectionDate": self._date_to_collect_go_datetime(
                prescription.next_drug_pickup_date
            ),
            "Surname": surname,
            "Title": "",
        }

    def _build_tracking_status(self, date_value):
        params = self.env["ir.config_parameter"].sudo()
        location_id = (params.get_param("cdu.collect_go.cdu_location_id") or "").strip()
        if not location_id:
            raise UserError(
                _(
                    "Collect-and-Go CDU Location ID is not configured. "
                    "Sync pickup points or set cdu.collect_go.cdu_location_id before dispatch handover."
                )
            )
        return {
            "Date": date_value,
            "LocationID": location_id,
            "ParcelTrackingStatusID": None,
            "TrackingStatusType": self._int_param(
                params, "cdu.collect_go.dispatch_tracking_status_type", 0
            ),
        }

    def _split_patient_name(self, patient_name):
        parts = (patient_name or "").strip().split()
        if not parts:
            return "", ""
        if len(parts) == 1:
            return parts[0], ""
        return " ".join(parts[:-1]), parts[-1]

    def _collect_go_datetime(self):
        dt = fields.Datetime.context_timestamp(self, fields.Datetime.now())
        return dt.isoformat()

    def _date_to_collect_go_datetime(self, date_value):
        if not date_value:
            return False
        return "%sT00:00:00+02:00" % fields.Date.to_string(date_value)

    def _int_param(self, params, key, default):
        try:
            return int(params.get_param(key) or default)
        except ValueError:
            return default

    def _safe_json_loads(self, value):
        if not value:
            return {}
        try:
            return json.loads(value)
        except ValueError:
            return {}

    def _extract_bridge_error(self, response_payload, response_text):
        if response_payload:
            return response_payload.get("message") or response_payload.get("error") or response_text
        return response_text or _("No response body returned.")

    def _extract_message_error(self, response_payload, response_text):
        if response_payload:
            body = response_payload.get("body")
            if isinstance(body, str) and body.strip():
                body_payload = self._safe_json_loads(body)
                if body_payload:
                    return (
                        body_payload.get("Error")
                        or body_payload.get("Description")
                        or body_payload.get("Code")
                        or body
                    )
                return body
            return response_payload.get("message") or response_payload.get("error") or response_text
        return response_text or _("No response body returned.")

    def _extract_parcel_status_items(self, response_payload):
        payload = self._unwrap_collect_go_body(response_payload)
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("ParcelStatuses", "parcelStatuses", "Parcels", "parcels", "Statuses", "statuses", "Items", "items", "Data", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [payload] if self._parcel_external_reference(payload) else []

    def _unwrap_collect_go_body(self, response_payload):
        if not isinstance(response_payload, dict):
            return response_payload
        body = response_payload.get("body") or response_payload.get("Body")
        if isinstance(body, str) and body.strip():
            return self._safe_json_loads(body) or body
        return response_payload

    def _apply_parcel_status_items(self, items, boxes=False):
        BoxLine = self.env["cdu.box.line"].sudo()
        allowed_references = set()
        if boxes:
            allowed_references = set(boxes.mapped("line_ids.parcel_reference"))

        matched_lines = BoxLine.browse()
        now = fields.Datetime.now()
        for item in items:
            parcel_reference = self._parcel_external_reference(item)
            if not parcel_reference:
                continue
            if allowed_references and parcel_reference not in allowed_references:
                continue
            lines = BoxLine.search([("parcel_reference", "=", parcel_reference)])
            if not lines:
                continue
            values = {
                "collect_go_parcel_status_type": self._parcel_status_type(item),
                "collect_go_parcel_status": self._parcel_status_label(item),
                "collect_go_status_received_at": now,
                "collect_go_status_date": self._parcel_status_date(item),
                "collect_go_status_raw_json": self._to_pretty_json(item),
            }
            lines.write(values)
            matched_lines |= lines

        for box in matched_lines.mapped("box_id"):
            box._update_collect_go_status_summary()
        return len(matched_lines)

    def _parcel_external_reference(self, item):
        return self._first_value(
            item,
            "ExternalReference",
            "externalReference",
            "ParcelExternalReference",
            "parcelExternalReference",
            "ParcelReference",
            "parcelReference",
        )

    def _parcel_status_type(self, item):
        value = self._first_value(item, "ParcelStatusType", "parcelStatusType", "StatusType", "statusType")
        if value is False:
            tracking_status = self._latest_tracking_status(item)
            value = self._first_value(tracking_status, "TrackingStatusType", "trackingStatusType")
        try:
            return int(value)
        except (TypeError, ValueError):
            return False

    def _parcel_status_label(self, item):
        label = self._first_value(
            item,
            "ParcelStatus",
            "parcelStatus",
            "ParcelStatusName",
            "parcelStatusName",
            "Status",
            "status",
            "Description",
            "description",
        )
        if label:
            return label
        status_type = self._parcel_status_type(item)
        return _("Status Type %s") % status_type if status_type is not False else _("Status update received")

    def _parcel_status_date(self, item):
        value = self._first_value(item, "Date", "date", "StatusDate", "statusDate", "Timestamp", "timestamp")
        if value:
            return value
        tracking_status = self._latest_tracking_status(item)
        return self._first_value(tracking_status, "Date", "date", "Timestamp", "timestamp") or False

    def _latest_tracking_status(self, item):
        tracking_statuses = item.get("TrackingStatuses") or item.get("trackingStatuses") or []
        if not isinstance(tracking_statuses, list) or not tracking_statuses:
            return {}
        tracking_statuses = [status for status in tracking_statuses if isinstance(status, dict)]
        return tracking_statuses[-1] if tracking_statuses else {}

    def _first_value(self, item, *keys):
        if not isinstance(item, dict):
            return False
        for key in keys:
            value = item.get(key)
            if value not in (None, False, ""):
                return value
        return False

    def _http_request(self, method, endpoint, payload=None):
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(endpoint, data=data, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=self._timeout_seconds()) as response:
                body = response.read().decode("utf-8")
                return {"status_code": response.status, "body": body}
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return {"status_code": exc.code, "body": body}
        except error.URLError as exc:
            raise UserError(_("Could not reach Collect-and-Go: %s") % exc.reason) from exc

    def _timeout_seconds(self):
        params = self.env["ir.config_parameter"].sudo()
        raw_timeout = params.get_param("cdu.collect_go.timeout_seconds") or "30"
        try:
            return max(int(raw_timeout), 1)
        except ValueError:
            return 30

    def _max_retry_count(self):
        params = self.env["ir.config_parameter"].sudo()
        raw_count = params.get_param("cdu.collect_go.max_retry_count") or "3"
        try:
            return max(int(raw_count), 0)
        except ValueError:
            return 3

    def _retry_root_log(self, log):
        if not log:
            return False
        return log.retry_of or log

    def _log_call(
        self,
        call_type,
        endpoint,
        http_method,
        request_payload=None,
        response_body=None,
        http_status_code=False,
        bridge_status_code=False,
        success=False,
        error_message=False,
        box=False,
        reference_guid=False,
        retry_count=0,
        retry_of=False,
    ):
        return self.env["cdu.collect.go.api.log"].sudo().create(
            {
                "call_type": call_type,
                "reference_guid": reference_guid,
                "endpoint": endpoint,
                "http_method": http_method,
                "request_payload": self._to_pretty_json(request_payload),
                "response_body": self._to_pretty_json(response_body),
                "http_status_code": http_status_code,
                "bridge_status_code": bridge_status_code,
                "success": success,
                "error_message": error_message,
                "box_id": box.id if box else False,
                "user_id": self.env.user.id,
                "timestamp": fields.Datetime.now(),
                "retry_count": retry_count,
                "retry_of": retry_of.id if retry_of else False,
            }
        )

    def _to_pretty_json(self, value):
        if value in (None, False):
            return False
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                return value
        return json.dumps(value, indent=2, sort_keys=True)
