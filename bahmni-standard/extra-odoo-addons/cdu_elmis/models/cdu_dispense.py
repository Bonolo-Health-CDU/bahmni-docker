from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from werkzeug import urls


class CduDispense(models.Model):
    _name = "cdu.dispense"
    _description = "CDU Dispensing"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"

    name = fields.Char(default="/", copy=False, readonly=True, tracking=True)
    prescription_id = fields.Many2one(
        "cdu.prescription",
        required=True,
        ondelete="restrict",
        index=True,
        tracking=True,
    )
    batch_id = fields.Many2one(
        "cdu.batch",
        related="prescription_id.batch_id",
        store=True,
        readonly=True,
    )
    patient_id = fields.Many2one(
        "res.partner",
        related="prescription_id.patient_id",
        store=True,
        readonly=True,
    )
    patient_name = fields.Char(
        related="prescription_id.patient_first_name",
        store=True,
        readonly=True,
    )
    patient_identifier = fields.Char(
        related="prescription_id.patient_identifier",
        store=True,
        readonly=True,
    )
    patient_phone = fields.Char(
        related="prescription_id.patient_phone",
        store=True,
        readonly=True,
    )
    regimen_prescribed_raw = fields.Char(
        related="prescription_id.regimen_prescribed_raw",
        store=True,
        readonly=True,
    )
    next_drug_pickup_date = fields.Date(
        related="prescription_id.next_drug_pickup_date",
        store=True,
        readonly=True,
    )
    collection_point_id = fields.Many2one(
        "cdu.collection.point",
        related="prescription_id.collection_point_id",
        store=True,
        readonly=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("confirmed", "Confirmed"),
        ],
        default="draft",
        tracking=True,
    )
    production_stock_refreshed_at = fields.Datetime(string="Production Floor Stock Refreshed At")
    stock_option_ids = fields.One2many(
        "cdu.dispense.stock.option",
        "dispense_id",
        string="Available Production Floor Stock",
    )
    stock_selection_ids = fields.One2many(
        "cdu.dispense.stock.selection",
        "dispense_id",
        string="Dispense Stock Selections",
    )
    dispensing_ready = fields.Boolean(compute="_compute_dispensing_readiness")
    dispensing_readiness_message = fields.Text(compute="_compute_dispensing_readiness")
    confirmed_by = fields.Many2one("res.users", readonly=True)
    confirmed_at = fields.Datetime(readonly=True)
    labels_printed_by = fields.Many2one("res.users", readonly=True)
    labels_printed_at = fields.Datetime(readonly=True)
    labels_printed = fields.Boolean(
        string="Labels Printed",
        compute="_compute_labels_printed",
        store=True,
    )

    _sql_constraints = [
        (
            "unique_prescription_dispense",
            "unique(prescription_id)",
            "Only one dispense record is allowed per prescription.",
        ),
    ]

    @api.model
    def create(self, vals):
        if vals.get("name", "/") == "/":
            vals["name"] = self.env["ir.sequence"].next_by_code("cdu.dispense") or "/"
        dispense = super().create(vals)
        dispense._prefill_from_picking()
        return dispense

    @api.depends("labels_printed_at")
    def _compute_labels_printed(self):
        for dispense in self:
            dispense.labels_printed = bool(dispense.labels_printed_at)

    def _ensure_dispensing_access(self):
        if not (
            self.env.user.has_group("cdu_prescription.group_cdu_dispensing_officer")
            or self.env.user.has_group("cdu_prescription.group_cdu_admin")
        ):
            raise AccessError(_("Only CDU dispensing officers can manage dispensing."))

    def _prefill_from_picking(self):
        for dispense in self:
            if dispense.stock_selection_ids:
                continue
            prescription = dispense.prescription_id
            batch = prescription.batch_id
            if not batch:
                continue

            all_fulfilment_lines = batch.elmis_picking_fulfilment_line_ids
            fulfilment_lines = all_fulfilment_lines
            prescription_product_ids = batch.patient_picking_line_ids.filtered(
                lambda line, prescription=prescription: line.prescription_id == prescription
            ).mapped("product_id").ids
            if prescription_product_ids:
                fulfilment_lines = fulfilment_lines.filtered(
                    lambda line: line.picking_line_id.summary_line_id.product_id.id
                    in prescription_product_ids
                )

            regimen_key = (prescription.regimen_prescribed_raw or "").strip().lower()
            if (not prescription_product_ids or not fulfilment_lines) and regimen_key:
                matched = all_fulfilment_lines.filtered(
                    lambda line: (line.openmrs_drug_name or "").strip().lower() == regimen_key
                )
                if matched:
                    fulfilment_lines = matched

            if not fulfilment_lines and prescription.regimen_prescribed_raw:
                self.env["cdu.dispense.stock.selection"].create(
                    {
                        "dispense_id": dispense.id,
                        "openmrs_drug_name": prescription.regimen_prescribed_raw,
                        "quantity_dispensed": 1,
                    }
                )
                continue

            for line in fulfilment_lines:
                prescription_count = line.picking_line_id.prescription_count or 1
                quantity = line.quantity_picked / prescription_count if prescription_count else line.quantity_picked
                self.env["cdu.dispense.stock.selection"].create(
                    {
                        "dispense_id": dispense.id,
                        "picking_fulfilment_line_id": line.id,
                        "openmrs_drug_name": line.openmrs_drug_name,
                        "openmrs_drug_uuid": line.picking_line_id.openmrs_drug_uuid,
                        "selected_orderable_code": line.selected_orderable_code,
                        "selected_orderable_id": line.selected_orderable_id,
                        "selected_orderable_name": line.selected_orderable_name,
                        "selected_lot": line.selected_lot,
                        "selected_lot_id": line.selected_lot_id,
                        "selected_lot_expiry": line.selected_lot_expiry,
                        "selected_stock_on_hand": line.selected_stock_on_hand,
                        "quantity_dispensed": quantity or 1,
                    }
                )

    @api.depends(
        "stock_selection_ids.stock_option_id",
        "stock_selection_ids.selected_orderable_name",
        "stock_selection_ids.quantity_dispensed",
        "stock_selection_ids.selected_stock_on_hand",
        "stock_selection_ids.dosage_instructions",
    )
    def _compute_dispensing_readiness(self):
        for dispense in self:
            errors = dispense._get_dispensing_readiness_errors()
            dispense.dispensing_ready = not errors
            dispense.dispensing_readiness_message = (
                _("Ready for dispensing confirmation.")
                if not errors
                else "\n".join(errors)
            )

    def _get_dispensing_readiness_errors(self):
        self.ensure_one()
        errors = []
        if not self.stock_selection_ids:
            errors.append(_("Add at least one dispense stock selection."))
            return errors

        for index, line in enumerate(self.stock_selection_ids, start=1):
            label = line.openmrs_drug_name or _("Selection %s") % index
            if not line.stock_option_id:
                errors.append(_("%s: select an eLMIS Production Floor stock option.") % label)
            if line.quantity_dispensed <= 0:
                errors.append(_("%s: dispensed quantity must be greater than zero.") % label)
            if line.quantity_dispensed and line.quantity_dispensed > line.selected_stock_on_hand:
                errors.append(
                    _("%s: dispensed quantity (%s) exceeds available SOH (%s).")
                    % (label, line.quantity_dispensed, line.selected_stock_on_hand)
                )
            if not (line.dosage_instructions or "").strip():
                errors.append(_("%s: dosing/instructions are required.") % label)
        return errors

    def _ensure_dispensing_ready(self):
        self.ensure_one()
        errors = self._get_dispensing_readiness_errors()
        if errors:
            raise ValidationError(
                _("Dispensing cannot be confirmed yet:\n\n%s") % "\n".join(errors)
            )

    def action_refresh_production_stock(self):
        self._ensure_dispensing_access()
        service = self.env["cdu.elmis.stock.service"]
        if not service.has_valid_current_user_elmis_token():
            return service.action_open_elmis_auth_wizard()
        for dispense in self:
            dispense._refresh_production_stock_options()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Production Floor stock refreshed"),
                "message": _("Available CDU Production Floor stock was refreshed from eLMIS."),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def _refresh_production_stock_options(self):
        self.ensure_one()
        service = self.env["cdu.elmis.stock.service"]
        params = self.env["ir.config_parameter"].sudo()
        facility_code = params.get_param("cdu.elmis.cdu_production_floor_facility_code")
        program_code = params.get_param("cdu.elmis.default_program_code")
        if not facility_code:
            raise UserError(_("CDU Production Floor Facility Code is not configured."))
        if not program_code:
            raise UserError(_("Default Program Code is not configured."))

        service.invalidate_stock_cache(facility_code, program_code)
        payload = service.get_stock_card_summaries(
            facility_code=facility_code,
            program_code=program_code,
            use_cache=False,
        )
        self._replace_production_stock_options(
            payload,
            facility_code=facility_code,
            program_code=program_code,
        )
        self.production_stock_refreshed_at = fields.Datetime.now()

    def _replace_production_stock_options(self, payload, facility_code, program_code):
        self.ensure_one()
        self.stock_option_ids.unlink()
        values = []
        if not self.batch_id:
            raise UserError(_("This dispense record is not linked to a workload batch."))

        for option in self.batch_id._stock_options_from_payload(
            payload,
            facility_code=facility_code,
            program_code=program_code,
        ):
            option.pop("batch_id", None)
            option["dispense_id"] = self.id
            values.append(option)
        if values:
            self.env["cdu.dispense.stock.option"].create(values)
        self._relink_selection_stock_options()

    def _relink_selection_stock_options(self):
        for selection in self.stock_selection_ids:
            domain = [("dispense_id", "=", self.id)]
            if selection.selected_orderable_id:
                domain.append(("orderable_id", "=", selection.selected_orderable_id))
            elif selection.selected_orderable_code:
                domain.append(("orderable_code", "=", selection.selected_orderable_code))
            else:
                continue

            if selection.selected_lot_id:
                domain.append(("lot_id", "=", selection.selected_lot_id))
            elif selection.selected_lot:
                domain.append(("lot", "=", selection.selected_lot))

            option = self.env["cdu.dispense.stock.option"].search(domain, limit=1)
            if option:
                selection.stock_option_id = option.id

    def action_confirm_dispensing(self):
        self._ensure_dispensing_access()
        for dispense in self:
            if dispense.state == "confirmed":
                raise UserError(_("Dispensing has already been confirmed for %s.") % dispense.name)
            dispense._ensure_dispensing_ready()
            dispense.write(
                {
                    "state": "confirmed",
                    "confirmed_by": self.env.user.id,
                    "confirmed_at": fields.Datetime.now(),
                }
            )
            if dispense.prescription_id.state == "awaiting_dispensing":
                dispense.prescription_id.write({"state": "awaiting_bagging_qa"})
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Dispensing confirmed"),
                "message": _("Prescription moved to Awaiting Bagging / QA."),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def action_mark_labels_printed(self):
        self._ensure_dispensing_access()
        for dispense in self:
            if dispense.state != "confirmed":
                raise UserError(_("Confirm dispensing before marking labels printed."))
            if not dispense.labels_printed_at:
                dispense.write(
                    {
                        "labels_printed_by": self.env.user.id,
                        "labels_printed_at": fields.Datetime.now(),
                    }
                )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Labels marked printed"),
                "message": _("Label printing checkpoint was recorded."),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def action_print_labels(self):
        self._ensure_dispensing_access()
        for dispense in self:
            if dispense.state != "confirmed":
                raise UserError(_("Confirm dispensing before printing labels."))

        if not self.env.context.get("cdu_skip_label_print_wizard") and len(self) == 1:
            return {
                "type": "ir.actions.act_window",
                "name": _("Preview / Print Labels"),
                "res_model": "cdu.label.print.wizard",
                "view_mode": "form",
                "target": "new",
                "context": {
                    "default_dispense_id": self.id,
                    "default_label_layout": self.env.context.get("cdu_label_layout", "all"),
                },
            }

        for dispense in self:
            if not dispense.labels_printed_at:
                dispense.write(
                    {
                        "labels_printed_by": self.env.user.id,
                        "labels_printed_at": fields.Datetime.now(),
                    }
                )
        label_layout = self.env.context.get("cdu_label_layout", "all")
        return self.env.ref("cdu_elmis.action_report_cdu_dispense_labels").with_context(
            cdu_label_layout=label_layout,
        ).report_action(
            self,
            config=False,
        )

    def get_barcode_url(self, value, barcode_type="Code128", width=580, height=160):
        query = urls.url_encode(
            {
                "barcode_type": barcode_type,
                "value": value or "",
                "width": width,
                "height": height,
                "humanreadable": 0,
                "quiet": 1,
            }
        )
        return "/report/barcode?%s" % query

    def action_open_or_create_for_prescription(self):
        self.ensure_one()
        return self._action_open()

    def _action_open(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Dispense Prescription"),
            "res_model": "cdu.dispense",
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }
