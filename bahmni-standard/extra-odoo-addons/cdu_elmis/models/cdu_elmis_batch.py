from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class CduBatch(models.Model):
    _inherit = "cdu.batch"

    stock_event_status = fields.Selection(
        [
            ("not_started", "Not Started"),
            ("pending", "Pending"),
            ("sent", "Sent"),
            ("failed", "Failed"),
            ("retry_limit_reached", "Retry Limit Reached"),
        ],
        string="eLMIS Stock Event Status",
        default="not_started",
        tracking=True,
    )
    stock_event_log_ids = fields.One2many(
        "cdu.elmis.api.log",
        "batch_id",
        string="eLMIS API Logs",
    )
    picking_confirmed_at = fields.Datetime(string="Picking Confirmed At")
    boxing_completed_at = fields.Datetime(string="Boxing Completed At")
    residual_returned_at = fields.Datetime(string="Residual Returned At")
    store_stock_refreshed_at = fields.Datetime(string="Store Stock Refreshed At")
    picking_ready = fields.Boolean(
        string="Ready for Picking Confirmation",
        compute="_compute_picking_readiness",
    )
    picking_readiness_message = fields.Text(
        string="Picking Readiness",
        compute="_compute_picking_readiness",
    )
    elmis_picking_line_ids = fields.One2many(
        "cdu.picking.line",
        "batch_id",
        string="eLMIS Picking Lines",
    )
    elmis_stock_option_ids = fields.One2many(
        "cdu.elmis.stock.option",
        "batch_id",
        string="Available eLMIS Stock",
    )

    def action_generate_picking_list(self):
        result = super().action_generate_picking_list()
        for batch in self:
            batch._generate_elmis_picking_lines()
        return result

    def _generate_elmis_picking_lines(self):
        for batch in self:
            batch.elmis_picking_line_ids.unlink()
            for item in batch.patient_picking_line_ids:
                product = item.product_id
                template = product.product_tmpl_id
                self.env["cdu.picking.line"].create(
                    {
                        "batch_id": batch.id,
                        "prescription_id": item.prescription_id.id,
                        "prescription_item_id": item.id,
                        "openmrs_drug_name": product.display_name,
                        "openmrs_drug_uuid": template.cdu_openmrs_drug_uuid,
                        "quantity_to_pick": item.bottles_required or 1,
                    }
                )

    @api.depends(
        "elmis_picking_line_ids.selected_stock_option_id",
        "elmis_picking_line_ids.quantity_picked",
        "elmis_picking_line_ids.quantity_to_pick",
        "elmis_picking_line_ids.selected_stock_on_hand",
    )
    def _compute_picking_readiness(self):
        for batch in self:
            errors = batch._get_picking_readiness_errors()
            batch.picking_ready = not errors
            batch.picking_readiness_message = (
                _("Ready for picking confirmation.")
                if not errors
                else "\n".join(errors)
            )

    def action_check_picking_readiness(self):
        self._ensure_batch_workflow_access()
        for batch in self:
            batch._ensure_picking_ready()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Picking readiness passed"),
                "message": _("All eLMIS picking selections are complete and valid."),
                "type": "success",
                "sticky": False,
            },
        }

    def action_confirm_elmis_picking(self):
        self._ensure_batch_workflow_access()
        params = self.env["ir.config_parameter"].sudo()
        store_facility_code = params.get_param("cdu.elmis.cdu_store_facility_code")
        production_facility_code = params.get_param(
            "cdu.elmis.cdu_production_floor_facility_code"
        )
        program_code = params.get_param("cdu.elmis.default_program_code")
        store_debit_reason = params.get_param("cdu.elmis.picking_debit_reason_name")
        production_credit_reason = params.get_param("cdu.elmis.picking_credit_reason_name")

        missing = []
        if not store_facility_code:
            missing.append(_("CDU Store Facility Code"))
        if not production_facility_code:
            missing.append(_("CDU Production Floor Facility Code"))
        if not program_code:
            missing.append(_("Default Program Code"))
        if not store_debit_reason:
            missing.append(_("Picking Store Debit Reason"))
        if not production_credit_reason:
            missing.append(_("Picking Production Credit Reason"))
        if missing:
            raise UserError(_("Missing eLMIS configuration: %s") % ", ".join(missing))

        service = self.env["cdu.elmis.stock.service"]
        for batch in self:
            if batch.picking_confirmed_at:
                raise UserError(_("Picking has already been confirmed for %s.") % batch.name)
            batch._ensure_picking_ready()
            batch.stock_event_status = "pending"
            store_items = batch._build_picking_stock_event_items(store_debit_reason)
            production_items = batch._build_picking_stock_event_items(
                production_credit_reason
            )
            try:
                service.post_internal_stock_event(
                    facility_code=store_facility_code,
                    program_code=program_code,
                    items=store_items,
                    call_type="PICKING_STORE_DEBIT",
                    batch=batch,
                    destination_facility_code=production_facility_code,
                )
                service.post_internal_stock_event(
                    facility_code=production_facility_code,
                    program_code=program_code,
                    items=production_items,
                    call_type="PICKING_PRODUCTION_CREDIT",
                    batch=batch,
                    source_facility_code=store_facility_code,
                )
            except Exception:
                batch.stock_event_status = "failed"
                raise

            batch.write(
                {
                    "stock_event_status": "sent",
                    "picking_confirmed_at": fields.Datetime.now(),
                }
            )
            service.invalidate_stock_cache(store_facility_code, program_code)
            service.invalidate_stock_cache(production_facility_code, program_code)

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Picking confirmed"),
                "message": _(
                    "Store debit and Production Floor credit events were submitted to eLMIS."
                ),
                "type": "success",
                "sticky": False,
            },
        }

    def _build_picking_stock_event_items(self, reason_name):
        self.ensure_one()
        grouped = {}
        for line in self.elmis_picking_line_ids:
            key = (
                line.selected_orderable_id or line.selected_orderable_code,
                line.selected_lot_id or line.selected_lot or "",
            )
            if key not in grouped:
                grouped[key] = {
                    "orderable": line.selected_orderable_code,
                    "orderableId": line.selected_orderable_id,
                    "quantity": 0,
                    "reason": reason_name,
                    "occurredDate": fields.Date.to_string(fields.Date.today()),
                }
                if line.selected_lot_id:
                    grouped[key]["lotId"] = line.selected_lot_id
                if line.selected_lot:
                    grouped[key]["lot"] = line.selected_lot
            grouped[key]["quantity"] += line.quantity_picked
        return list(grouped.values())

    def _ensure_picking_ready(self):
        self.ensure_one()
        errors = self._get_picking_readiness_errors()
        if errors:
            raise ValidationError(
                _("Picking cannot be confirmed yet:\n\n%s") % "\n".join(errors)
            )
        return True

    def _get_picking_readiness_errors(self):
        self.ensure_one()
        errors = []
        if not self.elmis_picking_line_ids:
            errors.append(_("No eLMIS picking lines have been generated."))
            return errors

        for index, line in enumerate(self.elmis_picking_line_ids, start=1):
            label = line.openmrs_drug_name or _("Line %s") % index
            if not line.selected_stock_option_id:
                errors.append(
                    _("%s: select an eLMIS stock option in Fulfil With.") % label
                )
            if line.quantity_picked <= 0:
                errors.append(_("%s: quantity picked must be greater than zero.") % label)
            if (
                line.selected_stock_option_id
                and line.quantity_picked
                and line.quantity_picked > line.selected_stock_on_hand
            ):
                errors.append(
                    _(
                        "%s: quantity picked (%s) exceeds available stock on hand (%s)."
                    )
                    % (label, line.quantity_picked, line.selected_stock_on_hand)
                )
        return errors

    def action_refresh_store_stock(self):
        service = self.env["cdu.elmis.stock.service"]
        params = self.env["ir.config_parameter"].sudo()
        store_facility_code = params.get_param("cdu.elmis.cdu_store_facility_code")
        program_code = params.get_param("cdu.elmis.default_program_code")
        if not store_facility_code:
            raise UserError(_("CDU Store Facility Code is not configured."))
        if not program_code:
            raise UserError(_("Default Program Code is not configured."))

        for batch in self:
            service.invalidate_stock_cache(store_facility_code, program_code)
            payload = service.get_stock_card_summaries(
                facility_code=store_facility_code,
                program_code=program_code,
                use_cache=False,
                batch=batch,
            )
            batch._replace_store_stock_options(
                payload,
                facility_code=store_facility_code,
                program_code=program_code,
            )
            batch.store_stock_refreshed_at = fields.Datetime.now()

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("eLMIS stock refreshed"),
                "message": _("Available CDU Store stock was refreshed from eLMIS."),
                "type": "success",
                "sticky": False,
            },
        }

    def _replace_store_stock_options(self, payload, facility_code, program_code):
        self.ensure_one()
        self.elmis_stock_option_ids.unlink()
        option_values = self._stock_options_from_payload(
            payload,
            facility_code=facility_code,
            program_code=program_code,
        )
        if option_values:
            self.env["cdu.elmis.stock.option"].create(option_values)

    def _stock_options_from_payload(self, payload, facility_code, program_code):
        summaries = self._extract_stock_summaries(payload)
        option_values = []
        for summary in summaries:
            if isinstance(summary.get("canFulfillForMe"), list):
                option_values.extend(
                    self._stock_options_from_resolved_summary(
                        summary,
                        facility_code=facility_code,
                        program_code=program_code,
                    )
                )
                continue

            orderable = summary.get("orderable") or summary.get("orderableCode")
            orderable_name = (
                summary.get("orderableName")
                or summary.get("orderableDisplayName")
                or orderable
            )
            for card in summary.get("stockCards") or []:
                stock_on_hand = card.get("stockOnHand") or 0
                if stock_on_hand <= 0:
                    continue
                option_values.append(
                    {
                        "batch_id": self.id,
                        "facility_code": facility_code,
                        "program_code": summary.get("program") or program_code,
                        "orderable_code": orderable,
                        "orderable_name": orderable_name,
                        "lot": card.get("lot"),
                        "stock_on_hand": stock_on_hand,
                        "expiration_date": fields.Date.to_date(card.get("expirationDate")),
                        "occurred_date": fields.Date.to_date(card.get("occurredDate")),
                    }
                )
        return sorted(
            option_values,
            key=lambda value: (
                value.get("expiration_date") or fields.Date.to_date("9999-12-31"),
                value.get("orderable_code") or "",
                value.get("lot") or "",
            ),
        )

    def _stock_options_from_resolved_summary(self, summary, facility_code, program_code):
        option_values = []
        for entry in summary.get("canFulfillForMe") or []:
            stock_on_hand = entry.get("stockOnHand") or 0
            if stock_on_hand <= 0:
                continue

            orderable = entry.get("orderable") or {}
            lot = entry.get("lot") or {}
            orderable_id = orderable.get("id")
            lot_id = lot.get("id")
            orderable_name = entry.get("orderableName") or orderable_id
            option_values.append(
                {
                    "batch_id": self.id,
                    "facility_code": facility_code,
                    "program_code": program_code,
                    "orderable_code": orderable_id,
                    "orderable_id": orderable_id,
                    "orderable_name": orderable_name,
                    "lot": entry.get("lotCode"),
                    "lot_id": lot_id,
                    "stock_on_hand": stock_on_hand,
                    "expiration_date": fields.Date.to_date(
                        entry.get("lotExpirationDate")
                    ),
                    "occurred_date": fields.Date.to_date(entry.get("occurredDate")),
                }
            )
        return option_values

    def _extract_stock_summaries(self, payload):
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            if isinstance(payload.get("content"), list):
                return payload["content"]
            if isinstance(payload.get("data"), list):
                return payload["data"]
            if isinstance(payload.get("items"), list):
                return payload["items"]
        return []
