import base64

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from werkzeug import urls

from .cdu_stock_summary import highest_stock_status


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
    duration_days = fields.Integer(
        string="Duration Days",
        related="prescription_id.cdu_days_supply",
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
    stock_summary_ids = fields.One2many(
        "cdu.dispense.stock.summary",
        "dispense_id",
        string="Available CDU Production Floor Stock Summary",
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
    _AUTO_REFRESH_FORM_FIELDS = frozenset(
        (
            "stock_option_ids",
            "stock_summary_ids",
            "stock_selection_ids",
        )
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

    def read(self, fields=None, load="_classic_read"):
        if self._should_auto_refresh_production_stock_on_read(fields):
            self._auto_refresh_production_stock(silent=True)
        return super().read(fields=fields, load=load)

    def _should_auto_refresh_production_stock_on_read(self, requested_fields):
        if self.env.context.get("cdu_skip_auto_refresh_production_stock"):
            return False
        if requested_fields and not self._AUTO_REFRESH_FORM_FIELDS.intersection(requested_fields):
            return False
        return any(dispense.state == "draft" for dispense in self)

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

            if not fulfilment_lines and prescription.product_line_ids:
                for prescription_line in prescription.product_line_ids:
                    self.env["cdu.dispense.stock.selection"].create(
                        {
                            "dispense_id": dispense.id,
                            "openmrs_drug_name": (
                                prescription_line.product_id.display_name
                                or prescription_line.imported_product_name
                            ),
                            "quantity_dispensed": 1,
                            "dosage_instructions": (
                                prescription_line.dosage_instructions
                            ),
                        }
                    )
                continue

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
                patient_line = line.picking_line_id._get_matching_patient_picking_lines().filtered(
                    lambda picking_line, prescription=prescription: (
                        picking_line.prescription_id == prescription
                    )
                )[:1]
                quantity = (
                    patient_line.packs_to_pick
                    or patient_line.cdu_bottles_required
                    or patient_line.bottles_required
                )
                if not quantity:
                    prescription_count = line.picking_line_id.prescription_count or 1
                    quantity = (
                        line.quantity_picked / prescription_count
                        if prescription_count
                        else line.quantity_picked
                    )
                product = (
                    patient_line.product_id
                    or line.picking_line_id.summary_line_id.product_id
                )
                prescription_line = prescription.product_line_ids.filtered(
                    lambda product_line, product=product: (
                        product
                        and product_line.product_id == product
                    )
                )[:1]
                if not prescription_line:
                    candidate_names = {
                        (line.openmrs_drug_name or "").strip().lower(),
                        (line.selected_orderable_name or "").strip().lower(),
                    }
                    prescription_line = prescription.product_line_ids.filtered(
                        lambda product_line, candidate_names=candidate_names: (
                            (
                                product_line.imported_product_name
                                or product_line.product_id.display_name
                                or ""
                            ).strip().lower()
                            in candidate_names
                        )
                    )[:1]
                self.env["cdu.dispense.stock.selection"].create(
                    {
                        "dispense_id": dispense.id,
                        "picking_fulfilment_line_id": line.id,
                        "openmrs_drug_name": line.openmrs_drug_name,
                        "openmrs_drug_uuid": line.picking_line_id.openmrs_drug_uuid,
                        "selected_orderable_code": line.selected_orderable_code,
                        "selected_orderable_id": line.selected_orderable_id,
                        "selected_orderable_name": line.selected_orderable_name,
                        "selected_pack_size": line.selected_pack_size,
                        "selected_lot": line.selected_lot,
                        "selected_lot_id": line.selected_lot_id,
                        "selected_lot_expiry": line.selected_lot_expiry,
                        "selected_stock_on_hand": line.selected_stock_on_hand,
                        "quantity_dispensed": quantity or 1,
                        "dosage_instructions": (
                            prescription_line.dosage_instructions
                            if prescription_line
                            else prescription.dosage_instructions
                        ),
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
                errors.append(_("%s: dispensed packs must be greater than zero.") % label)
            if line.quantity_dispensed and line.quantity_dispensed > line.selected_stock_on_hand:
                errors.append(
                    _("%s: dispensed packs (%s) exceed available packs (%s).")
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
        auth_action = self._auto_refresh_production_stock(silent=False)
        if auth_action:
            return auth_action
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

    def _auto_refresh_production_stock(self, silent=False):
        service = self.env["cdu.elmis.stock.service"]
        if not service.has_valid_current_user_elmis_token():
            return False if silent else service.action_open_elmis_auth_wizard(
                batch=self[:1].batch_id
            )
        for dispense in self.filtered(lambda record: record.state == "draft"):
            try:
                dispense._refresh_production_stock_options()
            except UserError:
                if not silent:
                    raise
        return False

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
        self._rebuild_stock_summaries()
        self._relink_selection_stock_options()

    def _rebuild_stock_summaries(self):
        Summary = self.env["cdu.dispense.stock.summary"]
        for dispense in self:
            dispense.stock_summary_ids.unlink()
            grouped_options = {}
            for option in dispense.stock_option_ids:
                key = (
                    option.orderable_code or "",
                    option.orderable_id or "",
                    option.orderable_name or "",
                )
                grouped_options.setdefault(key, self.env["cdu.dispense.stock.option"])
                grouped_options[key] |= option

            for (orderable_code, orderable_id, orderable_name), options in grouped_options.items():
                dated_options = options.filtered("expiration_date")
                occurred_options = options.filtered("occurred_date")
                Summary.create(
                    {
                        "dispense_id": dispense.id,
                        "orderable_code": orderable_code,
                        "orderable_id": orderable_id,
                        "orderable_name": orderable_name,
                        "lot_count": len(options),
                        "total_stock_on_hand": sum(options.mapped("stock_on_hand")),
                        "total_stock_on_hand_units": sum(
                            options.mapped("stock_on_hand_units")
                        ),
                        "earliest_expiration_date": min(
                            dated_options.mapped("expiration_date")
                        )
                        if dated_options
                        else False,
                        "latest_stock_date": max(occurred_options.mapped("occurred_date"))
                        if occurred_options
                        else False,
                        "stock_status": highest_stock_status(options),
                    }
                )

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
        self.ensure_one()
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
        report_action = self.with_context(
            cdu_label_layout="all"
        )._action_generate_labels()
        return {
            "type": "ir.actions.client",
            "tag": "cdu_print_dispensing_labels_and_continue",
            "params": {
                "report_action": report_action,
                "title": _("Dispensing confirmed"),
                "message": _(
                    "Labels were generated. Continuing the dispensing workflow."
                ),
                "notification_type": "success",
                "sticky": False,
                "next": self._get_next_dispensing_action(),
            },
        }

    def action_reject_to_call_center(self):
        self.ensure_one()
        self._ensure_dispensing_access()
        if self.state != "draft":
            raise UserError(_("Only an active dispensing task can be rejected."))
        return self.prescription_id.action_reject_to_call_center()

    def action_reject_to_facility(self):
        self.ensure_one()
        self._ensure_dispensing_access()
        if self.state != "draft":
            raise UserError(_("Only an active dispensing task can be rejected."))
        return self.prescription_id.action_reject_to_facility()

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

        return self._action_generate_labels()

    def _action_generate_labels(self):
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

    def _get_next_dispensing_action(self):
        self.ensure_one()
        if self.batch_id:
            next_prescription = self.env["cdu.prescription"].search(
                [
                    ("batch_id", "=", self.batch_id.id),
                    ("state", "=", "awaiting_dispensing"),
                ],
                order="next_drug_pickup_date asc, patient_first_name asc, id asc",
                limit=1,
            )
            if next_prescription:
                return next_prescription.action_open_dispensing()

        action = self.env.ref(
            "cdu_elmis.action_cdu_dispensing_work_queue"
        ).read()[0]
        action["views"] = [(False, "tree"), (False, "form")]
        return action

    def action_open_next_dispensing_task(self):
        self.ensure_one()
        self._ensure_dispensing_access()
        if self.state != "confirmed" or not self.labels_printed:
            raise UserError(_("Print labels before moving to the next dispensing task."))
        return self._get_next_dispensing_action()

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

    def get_barcode_data_uri(self, value, barcode_type="Code128", width=580, height=160):
        barcode = self.env["ir.actions.report"].barcode(
            barcode_type,
            value or "",
            width=width,
            height=height,
            humanreadable=0,
            quiet=1,
        )
        encoded = base64.b64encode(barcode).decode("ascii")
        return "data:image/png;base64,%s" % encoded

    def get_label_qr_value(self):
        self.ensure_one()
        prescription_number = self.prescription_id.name or self.name or ""
        return "\n".join(
            value
            for value in ((self.patient_name or "").strip(), prescription_number)
            if value
        )

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
            "views": [(False, "form")],
            "target": "current",
        }
