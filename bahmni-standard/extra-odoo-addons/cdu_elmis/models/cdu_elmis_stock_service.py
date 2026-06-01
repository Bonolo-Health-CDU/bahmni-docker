import json
import base64
from datetime import timedelta
from urllib.parse import urljoin

import requests

from odoo import _, fields, models
from odoo.exceptions import UserError


class CduElmisStockService(models.AbstractModel):
    _name = "cdu.elmis.stock.service"
    _description = "CDU eLMIS Stock Service"

    def get_stock_card_summaries(
        self,
        facility_code,
        orderable_code=None,
        program_code=None,
        use_cache=True,
        batch=None,
    ):
        program_code = program_code or self._get_required_param(
            "cdu.elmis.default_program_code",
            "Default Program Code",
        )
        orderable_key = orderable_code or "ALL"

        if use_cache:
            cached = self.get_cached_stock(facility_code, program_code, orderable_key)
            if cached is not None:
                return cached

        base_url = self._get_required_param("cdu.elmis.base_url", "eLMIS Base URL")
        endpoint = urljoin(base_url.rstrip("/") + "/", "api/v2/stockCardSummariesResolv")
        facility_id = self._get_facility_id(facility_code)
        program_id = self._get_program_id(program_code)
        params = {
            "facilityId": facility_id,
            "programId": program_id,
            "nonEmptyOnly": "true",
        }
        if orderable_code:
            params["orderableCode"] = orderable_code

        try:
            auth = self._auth_context(use_user_token=True)
            response = requests.get(
                endpoint,
                params=params,
                headers=auth["headers"],
                timeout=30,
                verify=self._get_verify_ssl(),
            )
            response_text = response.text
            success = response.status_code == 200
            self._log_call(
                call_type="STOCK_QUERY",
                endpoint=response.url,
                http_method="GET",
                response_body=response_text,
                http_status_code=response.status_code,
                success=success,
                error_message=None if success else response_text[:250],
                batch=batch,
                auth_mode=auth["auth_mode"],
                elmis_username=auth["elmis_username"],
            )
            if not success:
                raise UserError("eLMIS stock query failed: %s" % response_text[:250])

            payload = response.json()
            self.set_cached_stock(facility_code, program_code, orderable_key, payload)
            return payload
        except requests.RequestException as error:
            self._log_call(
                call_type="STOCK_QUERY",
                endpoint=endpoint,
                http_method="GET",
                http_status_code=0,
                success=False,
                error_message=str(error)[:250],
                batch=batch,
                auth_mode="user_token",
                elmis_username=self.env.user.cdu_elmis_username,
            )
            raise UserError("Could not reach eLMIS for stock query: %s" % error) from error

    def post_stock_event(self, facility_code, program_code, items, call_type, batch=None):
        if not items:
            raise UserError("Cannot submit an empty eLMIS stock event.")

        base_url = self._get_required_param("cdu.elmis.base_url", "eLMIS Base URL")
        endpoint = urljoin(base_url.rstrip("/") + "/", "api/public/stockEvents")
        payload = {
            "facility": facility_code,
            "program": program_code,
            "items": items,
        }

        try:
            auth = self._auth_context(use_user_token=True)
            response = requests.post(
                endpoint,
                data=json.dumps(payload),
                headers=auth["headers"],
                timeout=30,
                verify=self._get_verify_ssl(),
            )
            response_text = response.text
            success = response.status_code == 201
            log = self._log_call(
                call_type=call_type,
                endpoint=endpoint,
                http_method="POST",
                request_payload=json.dumps(payload, indent=2, sort_keys=True),
                response_body=response_text,
                http_status_code=response.status_code,
                success=success,
                error_message=None if success else response_text[:250],
                batch=batch,
                auth_mode=auth["auth_mode"],
                elmis_username=auth["elmis_username"],
            )
            if not success:
                raise UserError("eLMIS stock event failed: %s" % response_text[:250])
            return log
        except requests.RequestException as error:
            self._log_call(
                call_type=call_type,
                endpoint=endpoint,
                http_method="POST",
                request_payload=json.dumps(payload, indent=2, sort_keys=True),
                http_status_code=0,
                success=False,
                error_message=str(error)[:250],
                batch=batch,
                auth_mode="user_token",
                elmis_username=self.env.user.cdu_elmis_username,
            )
            raise UserError("Could not reach eLMIS for stock event: %s" % error) from error

    def post_internal_stock_event(
        self,
        facility_code,
        program_code,
        items,
        call_type,
        batch=None,
        box=None,
        source_facility_code=None,
        destination_facility_code=None,
    ):
        if not items:
            raise UserError("Cannot submit an empty eLMIS stock event.")

        if source_facility_code and destination_facility_code:
            raise UserError("An eLMIS stock event cannot have both source and destination.")

        base_url = self._get_required_param("cdu.elmis.base_url", "eLMIS Base URL")
        endpoint = urljoin(base_url.rstrip("/") + "/", "api/stockEvents")
        facility_id = self._get_facility_id(facility_code)
        program_id = self._get_program_id(program_code)
        source_node_id = (
            self._get_valid_source_node_id(program_id, facility_id, source_facility_code)
            if source_facility_code
            else None
        )
        destination_node_id = (
            self._get_valid_destination_node_id(
                program_id, facility_id, destination_facility_code
            )
            if destination_facility_code
            else None
        )
        payload = {
            "facilityId": facility_id,
            "programId": program_id,
            "signature": self.env.user.name,
            "documentNumber": "%s-%s" % (call_type, fields.Datetime.now()),
            "isActive": True,
            "lineItems": [
                self._to_internal_stock_event_line(
                    item,
                    source_node_id=source_node_id,
                    destination_node_id=destination_node_id,
                )
                for item in items
            ],
        }

        try:
            auth = self._auth_context(use_user_token=True)
            response = requests.post(
                endpoint,
                data=json.dumps(payload),
                headers=auth["headers"],
                timeout=30,
                verify=self._get_verify_ssl(),
            )
            response_text = response.text
            success = response.status_code == 201
            log = self._log_call(
                call_type=call_type,
                endpoint=endpoint,
                http_method="POST",
                request_payload=json.dumps(payload, indent=2, sort_keys=True),
                response_body=response_text,
                http_status_code=response.status_code,
                success=success,
                error_message=None if success else response_text[:250],
                batch=batch,
                box=box,
                auth_mode=auth["auth_mode"],
                elmis_username=auth["elmis_username"],
            )
            if not success:
                raise UserError("eLMIS stock event failed: %s" % response_text[:250])
            return log
        except requests.RequestException as error:
            self._log_call(
                call_type=call_type,
                endpoint=endpoint,
                http_method="POST",
                request_payload=json.dumps(payload, indent=2, sort_keys=True),
                http_status_code=0,
                success=False,
                error_message=str(error)[:250],
                batch=batch,
                box=box,
                auth_mode="user_token",
                elmis_username=self.env.user.cdu_elmis_username,
            )
            raise UserError("Could not reach eLMIS for stock event: %s" % error) from error

    def get_cached_stock(self, facility_code, program_code, orderable_code="ALL"):
        cache = self.env["cdu.elmis.stock.cache"].sudo().search(
            [
                ("facility_code", "=", facility_code),
                ("program_code", "=", program_code),
                ("orderable_code", "=", orderable_code or "ALL"),
                ("expires_at", ">", fields.Datetime.now()),
            ],
            limit=1,
        )
        if not cache:
            return None
        return json.loads(cache.response_json)

    def set_cached_stock(self, facility_code, program_code, orderable_code, data):
        ttl_seconds = int(
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("cdu.elmis.stock_cache_ttl_seconds", default=300)
            or 300
        )
        now = fields.Datetime.now()
        values = {
            "facility_code": facility_code,
            "program_code": program_code,
            "orderable_code": orderable_code or "ALL",
            "response_json": json.dumps(data, indent=2, sort_keys=True),
            "fetched_at": now,
            "expires_at": now + timedelta(seconds=ttl_seconds),
        }
        cache = self.env["cdu.elmis.stock.cache"].sudo().search(
            [
                ("facility_code", "=", values["facility_code"]),
                ("program_code", "=", values["program_code"]),
                ("orderable_code", "=", values["orderable_code"]),
            ],
            limit=1,
        )
        if cache:
            cache.write(values)
        else:
            cache = self.env["cdu.elmis.stock.cache"].sudo().create(values)
        return cache

    def invalidate_stock_cache(self, facility_code, program_code=None, orderable_code=None):
        domain = [("facility_code", "=", facility_code)]
        if program_code:
            domain.append(("program_code", "=", program_code))
        if orderable_code:
            domain.append(("orderable_code", "=", orderable_code))
        return self.env["cdu.elmis.stock.cache"].sudo().search(domain).unlink()

    def _log_call(
        self,
        call_type,
        endpoint,
        http_method,
        request_payload=None,
        response_body=None,
        http_status_code=0,
        success=False,
        error_message=None,
        batch=None,
        box=None,
        auth_mode=None,
        elmis_username=None,
    ):
        return self.env["cdu.elmis.api.log"].sudo().create(
            {
                "call_type": call_type,
                "endpoint": endpoint,
                "http_method": http_method,
                "request_payload": request_payload,
                "response_body": response_body,
                "http_status_code": http_status_code,
                "success": success,
                "error_message": error_message,
                "auth_mode": auth_mode,
                "elmis_username": elmis_username,
                "batch_id": batch.id if batch else False,
                "box_id": box.id if box else False,
                "user_id": self.env.user.id,
            }
        )

    def _auth_context(self, use_user_token=False):
        if use_user_token:
            token = self._get_current_user_elmis_token()
            return {
                "headers": self._headers_for_token(token),
                "auth_mode": "user_token",
                "elmis_username": self.env.user.cdu_elmis_username,
            }
        api_key = self._get_required_param("cdu.elmis.api_key", "eLMIS API Key")
        return {
            "headers": self._headers_for_token(api_key),
            "auth_mode": "system_api_key",
            "elmis_username": False,
        }

    def _headers_for_token(self, token):
        return {
            "Authorization": "Bearer %s" % token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _auth_headers(self):
        return self._auth_context()["headers"]

    def _get_current_user_elmis_token(self):
        user = self.env.user
        if not user.cdu_elmis_access_token or not user.cdu_elmis_token_expires_at:
            raise UserError(
                _(
                    "Please authenticate with eLMIS before performing this stock action."
                )
            )
        expires_at = fields.Datetime.to_datetime(user.cdu_elmis_token_expires_at)
        if expires_at <= fields.Datetime.now() + timedelta(seconds=30):
            raise UserError(
                _(
                    "Your eLMIS session has expired. Please authenticate with eLMIS again."
                )
            )
        return user.cdu_elmis_access_token

    def has_valid_current_user_elmis_token(self):
        try:
            self._get_current_user_elmis_token()
            return True
        except UserError:
            return False

    def action_open_elmis_auth_wizard(self, batch=None):
        return {
            "type": "ir.actions.act_window",
            "name": _("Authenticate with eLMIS"),
            "res_model": "cdu.elmis.auth.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_username": self.env.user.cdu_elmis_username
                or self.env.user.login,
                "default_batch_id": batch.id if batch else False,
            },
        }

    def authenticate_elmis_user(self, username, password):
        base_url = self._get_required_param("cdu.elmis.base_url", "eLMIS Base URL")
        endpoint = urljoin(base_url.rstrip("/") + "/", "api/oauth/token")
        client_id = self._get_required_param(
            "cdu.elmis.user_client_id",
            "eLMIS User OAuth Client ID",
        )
        client_secret = self._get_required_param(
            "cdu.elmis.user_client_secret",
            "eLMIS User OAuth Client Secret",
        )
        client_credentials = "%s:%s" % (client_id, client_secret)
        basic_token = base64.b64encode(client_credentials.encode("utf-8")).decode(
            "ascii"
        )
        try:
            response = requests.post(
                endpoint,
                params={"grant_type": "password"},
                data={"username": username, "password": password},
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Authorization": "Basic %s" % basic_token,
                },
                timeout=30,
                verify=self._get_verify_ssl(),
            )
        except requests.RequestException as error:
            raise UserError(_("Could not reach eLMIS authentication service: %s") % error) from error

        if response.status_code != 200:
            raise UserError(_("eLMIS authentication failed: %s") % response.text[:250])

        payload = response.json()
        access_token = payload.get("access_token")
        if not access_token:
            raise UserError(_("eLMIS did not return an access token."))
        expires_in = int(payload.get("expires_in") or 3600)
        return {
            "access_token": access_token,
            "expires_at": fields.Datetime.now() + timedelta(seconds=expires_in),
        }

    def _get_required_param(self, key, label):
        value = self.env["ir.config_parameter"].sudo().get_param(key)
        if not value:
            raise UserError("%s is not configured." % label)
        return value

    def _get_verify_ssl(self):
        params = self.env["ir.config_parameter"].sudo()
        return params.get_param("cdu.elmis.verify_ssl", "True") == "True"

    def resolve_configured_reference_ids(self):
        params = self.env["ir.config_parameter"].sudo()
        store_code = self._get_required_param(
            "cdu.elmis.cdu_store_facility_code",
            "CDU Store Facility Code",
        )
        production_code = self._get_required_param(
            "cdu.elmis.cdu_production_floor_facility_code",
            "CDU Production Floor Facility Code",
        )
        program_code = self._get_required_param(
            "cdu.elmis.default_program_code",
            "Default Program Code",
        )

        store_id = self._get_facility_by_code(store_code)["id"]
        production_id = self._get_facility_by_code(production_code)["id"]
        program_id = self._get_program_by_code(program_code)["id"]

        params.set_param("cdu.elmis.cdu_store_facility_id", store_id)
        params.set_param("cdu.elmis.cdu_production_floor_facility_id", production_id)
        params.set_param("cdu.elmis.default_program_id", program_id)

        reason_configs = [
            ("cdu.elmis.picking_debit_reason_name", "cdu.elmis.picking_debit_reason_id"),
            ("cdu.elmis.picking_credit_reason_name", "cdu.elmis.picking_credit_reason_id"),
            ("cdu.elmis.consumption_reason_name", "cdu.elmis.consumption_reason_id"),
            ("cdu.elmis.residual_debit_reason_name", "cdu.elmis.residual_debit_reason_id"),
            ("cdu.elmis.residual_credit_reason_name", "cdu.elmis.residual_credit_reason_id"),
        ]
        for name_key, id_key in reason_configs:
            reason_name = self._get_required_param(name_key, name_key)
            params.set_param(id_key, self._get_reason_by_name(reason_name)["id"])

        params.set_param(
            "cdu.elmis.store_to_production_destination_node_id",
            self._resolve_valid_destination_node_id(
                program_id,
                store_id,
                production_id,
                production_code,
            ),
        )
        params.set_param(
            "cdu.elmis.production_from_store_source_node_id",
            self._resolve_valid_source_node_id(
                program_id,
                production_id,
                store_id,
                store_code,
            ),
        )
        params.set_param(
            "cdu.elmis.production_to_store_destination_node_id",
            self._resolve_valid_destination_node_id(
                program_id,
                production_id,
                store_id,
                store_code,
            ),
        )
        params.set_param(
            "cdu.elmis.store_from_production_source_node_id",
            self._resolve_valid_source_node_id(
                program_id,
                store_id,
                production_id,
                production_code,
            ),
        )
        return True

    def _to_internal_stock_event_line(
        self, item, source_node_id=None, destination_node_id=None
    ):
        quantity = item.get("quantity")
        quantity_as_int = int(quantity)
        if quantity != quantity_as_int:
            raise UserError("eLMIS stock event quantities must be whole numbers.")

        orderable_id = item.get("orderableId")
        if not orderable_id:
            orderable_id = self._get_orderable_by_code(item.get("orderable"))["id"]
        line = {
            "orderableId": orderable_id,
            "quantity": quantity_as_int,
            "reasonId": self._get_reason_id(item.get("reason")),
            "occurredDate": item.get("occurredDate"),
        }
        if item.get("lotId"):
            line["lotId"] = item["lotId"]
        elif item.get("lot"):
            line["lotId"] = self._get_lot_id_for_orderable(
                item["lot"],
                orderable_id,
            )
        if source_node_id:
            line["sourceId"] = source_node_id
        if destination_node_id:
            line["destinationId"] = destination_node_id
        return line

    def _get_facility_by_code(self, facility_code):
        payload = self._get_reference_payload("api/facilities", {"code": facility_code})
        return self._first_matching(
            payload.get("content") or [],
            "code",
            facility_code,
            "Facility",
        )

    def _get_facility_id(self, facility_code):
        params = self.env["ir.config_parameter"].sudo()
        store_code = params.get_param("cdu.elmis.cdu_store_facility_code")
        production_code = params.get_param(
            "cdu.elmis.cdu_production_floor_facility_code"
        )
        if facility_code == store_code:
            facility_id = params.get_param("cdu.elmis.cdu_store_facility_id")
            if facility_id:
                return facility_id
        if facility_code == production_code:
            facility_id = params.get_param("cdu.elmis.cdu_production_floor_facility_id")
            if facility_id:
                return facility_id
        return self._get_facility_by_code(facility_code)["id"]

    def _get_program_by_code(self, program_code):
        payload = self._get_reference_payload("api/programs", {"code": program_code})
        return self._first_matching(payload, "code", program_code, "Program")

    def _get_program_id(self, program_code):
        params = self.env["ir.config_parameter"].sudo()
        if program_code == params.get_param("cdu.elmis.default_program_code"):
            program_id = params.get_param("cdu.elmis.default_program_id")
            if program_id:
                return program_id
        return self._get_program_by_code(program_code)["id"]

    def _get_orderable_by_code(self, orderable_code):
        payload = self._get_reference_payload("api/orderables", {"code": orderable_code})
        return self._first_matching(
            payload.get("content") or [],
            "productCode",
            orderable_code,
            "Orderable",
        )

    def _get_reason_by_name(self, reason_name):
        payload = self._get_reference_payload("api/stockCardLineItemReasons")
        return self._first_matching(payload, "name", reason_name, "Stock reason")

    def _get_reason_id(self, reason_name):
        params = self.env["ir.config_parameter"].sudo()
        reason_param_pairs = [
            ("cdu.elmis.picking_debit_reason_name", "cdu.elmis.picking_debit_reason_id"),
            ("cdu.elmis.picking_credit_reason_name", "cdu.elmis.picking_credit_reason_id"),
            ("cdu.elmis.consumption_reason_name", "cdu.elmis.consumption_reason_id"),
            ("cdu.elmis.residual_debit_reason_name", "cdu.elmis.residual_debit_reason_id"),
            ("cdu.elmis.residual_credit_reason_name", "cdu.elmis.residual_credit_reason_id"),
        ]
        for name_key, id_key in reason_param_pairs:
            if params.get_param(name_key) == reason_name:
                reason_id = params.get_param(id_key)
                if reason_id:
                    return reason_id
        return self._get_reason_by_name(reason_name)["id"]

    def _get_lot_id_for_orderable(self, lot_code, orderable_id):
        payload = self._get_reference_payload(
            "api/lots",
            {
                "exactCode": lot_code,
                "orderableId": orderable_id,
            },
        )
        lot = self._first_matching(
            payload.get("content") or [],
            "lotCode",
            lot_code,
            "Lot",
        )
        return lot["id"]

    def _get_valid_source_node_id(self, program_id, facility_id, source_facility_code):
        params = self.env["ir.config_parameter"].sudo()
        store_code = params.get_param("cdu.elmis.cdu_store_facility_code")
        production_code = params.get_param(
            "cdu.elmis.cdu_production_floor_facility_code"
        )
        if source_facility_code == store_code:
            node_id = params.get_param("cdu.elmis.production_from_store_source_node_id")
            if node_id:
                return node_id
        if source_facility_code == production_code:
            node_id = params.get_param("cdu.elmis.store_from_production_source_node_id")
            if node_id:
                return node_id

        source_facility_id = self._get_facility_id(source_facility_code)
        return self._resolve_valid_source_node_id(
            program_id,
            facility_id,
            source_facility_id,
            source_facility_code,
        )

    def _resolve_valid_source_node_id(
        self, program_id, facility_id, source_facility_id, source_facility_code
    ):
        payload = self._get_reference_payload(
            "api/validSources",
            {
                "programId": program_id,
                "facilityId": facility_id,
                "size": 1000,
            },
        )
        return self._get_source_destination_node_id(
            payload,
            source_facility_id,
            "source",
            source_facility_code,
        )

    def _get_valid_destination_node_id(
        self, program_id, facility_id, destination_facility_code
    ):
        params = self.env["ir.config_parameter"].sudo()
        store_code = params.get_param("cdu.elmis.cdu_store_facility_code")
        production_code = params.get_param(
            "cdu.elmis.cdu_production_floor_facility_code"
        )
        if destination_facility_code == production_code:
            node_id = params.get_param("cdu.elmis.store_to_production_destination_node_id")
            if node_id:
                return node_id
        if destination_facility_code == store_code:
            node_id = params.get_param("cdu.elmis.production_to_store_destination_node_id")
            if node_id:
                return node_id

        destination_facility_id = self._get_facility_id(destination_facility_code)
        return self._resolve_valid_destination_node_id(
            program_id,
            facility_id,
            destination_facility_id,
            destination_facility_code,
        )

    def _resolve_valid_destination_node_id(
        self, program_id, facility_id, destination_facility_id, destination_facility_code
    ):
        payload = self._get_reference_payload(
            "api/validDestinations",
            {
                "programId": program_id,
                "facilityId": facility_id,
                "size": 1000,
            },
        )
        return self._get_source_destination_node_id(
            payload,
            destination_facility_id,
            "destination",
            destination_facility_code,
        )

    def _get_source_destination_node_id(
        self, payload, reference_facility_id, assignment_type, facility_code
    ):
        for assignment in payload.get("content") or []:
            node = assignment.get("node") or {}
            if node.get("referenceId") == reference_facility_id:
                return node["id"]
        raise UserError(
            "No valid eLMIS %s assignment found for facility %s."
            % (assignment_type, facility_code)
        )

    def _get_reference_payload(self, path, params=None):
        base_url = self._get_required_param("cdu.elmis.base_url", "eLMIS Base URL")
        endpoint = urljoin(base_url.rstrip("/") + "/", path)
        response = requests.get(
            endpoint,
            params=params or {},
            headers=self._auth_headers(),
            timeout=30,
            verify=self._get_verify_ssl(),
        )
        if response.status_code != 200:
            raise UserError("eLMIS reference lookup failed: %s" % response.text[:250])
        return response.json()

    def _first_matching(self, records, field_name, expected_value, label):
        for record in records:
            if record.get(field_name) == expected_value:
                return record
        raise UserError("%s not found in eLMIS: %s" % (label, expected_value))
