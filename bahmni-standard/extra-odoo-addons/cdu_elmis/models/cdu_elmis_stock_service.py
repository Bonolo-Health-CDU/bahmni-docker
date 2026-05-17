import json
from datetime import timedelta
from urllib.parse import urljoin

import requests

from odoo import fields, models
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
            response = requests.get(
                endpoint,
                params=params,
                headers=self._auth_headers(),
                timeout=30,
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
            response = requests.post(
                endpoint,
                data=json.dumps(payload),
                headers=self._auth_headers(),
                timeout=30,
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
            )
            raise UserError("Could not reach eLMIS for stock event: %s" % error) from error

    def post_internal_stock_event(
        self,
        facility_code,
        program_code,
        items,
        call_type,
        batch=None,
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
            response = requests.post(
                endpoint,
                data=json.dumps(payload),
                headers=self._auth_headers(),
                timeout=30,
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
                "batch_id": batch.id if batch else False,
                "user_id": self.env.user.id,
            }
        )

    def _auth_headers(self):
        api_key = self._get_required_param("cdu.elmis.api_key", "eLMIS API Key")
        return {
            "Authorization": "Bearer %s" % api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _get_required_param(self, key, label):
        value = self.env["ir.config_parameter"].sudo().get_param(key)
        if not value:
            raise UserError("%s is not configured." % label)
        return value

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
        )
        if response.status_code != 200:
            raise UserError("eLMIS reference lookup failed: %s" % response.text[:250])
        return response.json()

    def _first_matching(self, records, field_name, expected_value, label):
        for record in records:
            if record.get(field_name) == expected_value:
                return record
        raise UserError("%s not found in eLMIS: %s" % (label, expected_value))
