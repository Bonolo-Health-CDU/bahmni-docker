from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare, float_is_zero


class CduPrescription(models.Model):
    _inherit = "cdu.prescription"

    dispense_ids = fields.One2many(
        "cdu.dispense",
        "prescription_id",
        string="Dispensing Jobs",
        readonly=True,
    )
    dispensing_status = fields.Selection(
        [
            ("not_dispensed", "Not dispensed"),
            ("fully_dispensed", "Fully dispensed"),
            ("partially_dispensed", "Partially dispensed"),
            ("cancelled", "Cancelled"),
        ],
        string="Dispensing Status",
        compute="_compute_dispensing_status",
        store=True,
        compute_sudo=True,
        readonly=True,
    )
    dispensing_back_order_packs = fields.Float(
        string="Back Order Packs",
        compute="_compute_dispensing_status",
        store=True,
        compute_sudo=True,
        readonly=True,
    )
    dispensing_back_order_units = fields.Float(
        string="Back Order Units",
        compute="_compute_dispensing_status",
        store=True,
        compute_sudo=True,
        readonly=True,
    )
    dispensing_back_order_days = fields.Float(
        string="Back Order Days",
        compute="_compute_dispensing_status",
        store=True,
        compute_sudo=True,
        readonly=True,
    )
    dispensing_back_order_short = fields.Char(
        string="Back Order",
        compute="_compute_dispensing_status",
        store=True,
        compute_sudo=True,
        readonly=True,
    )
    dispensing_back_order_summary = fields.Text(
        string="Back Order Details",
        compute="_compute_dispensing_status",
        store=True,
        compute_sudo=True,
        readonly=True,
    )

    _POST_DISPENSING_STATES = frozenset(
        (
            "awaiting_bagging_qa",
            "awaiting_boxing",
            "awaiting_dispatch",
            "dispatched",
        )
    )
    _DISPENSING_FLOAT_PRECISION_DIGITS = 4

    @api.depends(
        "state",
        "dispense_ids.state",
        "dispense_ids.stock_selection_ids.openmrs_drug_name",
        "dispense_ids.stock_selection_ids.selected_orderable_name",
        "dispense_ids.stock_selection_ids.selected_pack_size",
        "dispense_ids.stock_selection_ids.quantity_dispensed",
        "dispense_ids.stock_selection_ids.picking_fulfilment_line_id",
        "dispense_ids.stock_selection_ids.picking_fulfilment_line_id.picking_line_id",
        "batch_id.patient_picking_line_ids.prescription_id",
        "batch_id.patient_picking_line_ids.product_id",
        "batch_id.patient_picking_line_ids.drug_name",
        "batch_id.patient_picking_line_ids.packs_to_pick",
        "batch_id.patient_picking_line_ids.cdu_bottles_required",
        "batch_id.patient_picking_line_ids.bottles_required",
        "batch_id.patient_picking_line_ids.required_units",
        "batch_id.patient_picking_line_ids.required_quantity",
        "batch_id.patient_picking_line_ids.picked_units",
        "batch_id.patient_picking_line_ids.cdu_days",
        "batch_id.patient_picking_line_ids.effective_repeat_days",
        "batch_id.patient_picking_line_ids.prescription_repeat_days",
        "batch_id.patient_picking_line_ids.repeat_days",
        "batch_id.patient_picking_line_ids.daily_dose",
        "batch_id.patient_picking_line_ids.pack_size",
        "batch_id.patient_picking_line_ids.back_order_days",
    )
    def _compute_dispensing_status(self):
        for prescription in self:
            values = prescription._get_dispensing_status_values()
            prescription.dispensing_status = values["status"]
            prescription.dispensing_back_order_packs = values["back_order_packs"]
            prescription.dispensing_back_order_units = values["back_order_units"]
            prescription.dispensing_back_order_days = values["back_order_days"]
            prescription.dispensing_back_order_short = values["back_order_short"]
            prescription.dispensing_back_order_summary = values["back_order_summary"]

    def _get_dispensing_status_values(self):
        self.ensure_one()
        values = {
            "status": "not_dispensed",
            "back_order_packs": 0.0,
            "back_order_units": 0.0,
            "back_order_days": 0.0,
            "back_order_short": False,
            "back_order_summary": False,
        }
        if self.state == "cancelled":
            values["status"] = "cancelled"
            return values

        dispense = self.dispense_ids[:1]
        if not dispense:
            return values
        if dispense.state == "cancelled":
            values["status"] = "cancelled"
            return values
        if dispense.state != "confirmed":
            return values

        back_order_lines = self._get_dispensing_back_order_lines(dispense)
        if back_order_lines:
            total_packs = sum(line["packs"] for line in back_order_lines)
            total_units = sum(line["units"] for line in back_order_lines)
            longest_days = max(line["days"] for line in back_order_lines)
            values.update(
                {
                    "status": "partially_dispensed",
                    "back_order_packs": total_packs,
                    "back_order_units": total_units,
                    "back_order_days": longest_days,
                    "back_order_short": self._format_dispensing_back_order_short(
                        total_packs,
                        total_units,
                        longest_days,
                    ),
                    "back_order_summary": "\n".join(
                        line["summary"] for line in back_order_lines
                    ),
                }
            )
            return values

        values["status"] = "fully_dispensed"
        return values

    def _get_dispensing_back_order_lines(self, dispense):
        self.ensure_one()
        patient_lines = self.batch_id.patient_picking_line_ids.filtered(
            lambda line: line.prescription_id == self
        )
        groups = {}
        for patient_line in patient_lines:
            groups[("patient", patient_line.id)] = self._new_patient_dispense_group(
                patient_line
            )

        for selection in dispense.stock_selection_ids:
            patient_line = self._find_patient_line_for_dispense_selection(
                selection,
                patient_lines,
            )
            if patient_line:
                key = ("patient", patient_line.id)
                group = groups.setdefault(
                    key,
                    self._new_patient_dispense_group(patient_line),
                )
            else:
                key = ("selection", selection.id)
                group = groups.setdefault(
                    key,
                    self._new_manual_dispense_group(selection),
                )

            pack_size = self._get_dispense_selection_pack_size(
                selection,
                patient_line,
            )
            if pack_size and not group["pack_size"]:
                group["pack_size"] = pack_size
            if group["required_packs"] and pack_size:
                group["required_units"] = max(
                    group["required_units"],
                    group["required_packs"] * pack_size,
                )
            dispensed_packs = selection.quantity_dispensed or 0.0
            group["dispensed_packs"] += dispensed_packs
            group["dispensed_units"] += dispensed_packs * (pack_size or 0.0)

        back_order_lines = []
        for group in groups.values():
            back_order = self._get_group_back_order_values(group)
            if not (
                self._is_dispensing_positive(back_order["packs"])
                or self._is_dispensing_positive(back_order["units"])
                or self._is_dispensing_positive(back_order["days"])
            ):
                continue
            back_order_lines.append(
                {
                    "packs": back_order["packs"],
                    "units": back_order["units"],
                    "days": back_order["days"],
                    "summary": self._format_dispensing_back_order_line(
                        group["medicine"],
                        back_order["packs"],
                        back_order["units"],
                        back_order["days"],
                    ),
                }
            )
        return back_order_lines

    def _new_patient_dispense_group(self, patient_line):
        pack_size = patient_line.pack_size or 0.0
        required_packs = (
            patient_line.packs_to_pick
            or patient_line.cdu_bottles_required
            or patient_line.bottles_required
            or 0.0
        )
        required_units = (
            patient_line.picked_units
            or patient_line.required_units
            or patient_line.required_quantity
            or 0.0
        )
        if required_packs and pack_size:
            required_units = max(required_units, required_packs * pack_size)
        return {
            "medicine": (
                patient_line.product_id.display_name
                or patient_line.drug_name
                or _("Medicine")
            ),
            "required_packs": required_packs,
            "required_units": required_units,
            "dispensed_packs": 0.0,
            "dispensed_units": 0.0,
            "pack_size": pack_size,
            "daily_dose": patient_line.daily_dose or 0.0,
            "expected_days": (
                patient_line.cdu_days
                or patient_line.effective_repeat_days
                or patient_line.prescription_repeat_days
                or patient_line.repeat_days
                or 0.0
            ),
            "fallback_back_order_days": patient_line.back_order_days or 0.0,
        }

    def _new_manual_dispense_group(self, selection):
        pack_size = self._get_dispense_selection_pack_size(selection)
        required_packs = selection.quantity_dispensed or 0.0
        return {
            "medicine": (
                selection.openmrs_drug_name
                or selection.selected_orderable_name
                or _("Medicine")
            ),
            "required_packs": required_packs,
            "required_units": required_packs * (pack_size or 0.0),
            "dispensed_packs": 0.0,
            "dispensed_units": 0.0,
            "pack_size": pack_size,
            "daily_dose": 0.0,
            "expected_days": 0.0,
            "fallback_back_order_days": 0.0,
        }

    def _get_group_back_order_values(self, group):
        required_packs = group["required_packs"] or 0.0
        required_units = group["required_units"] or 0.0
        dispensed_packs = group["dispensed_packs"] or 0.0
        dispensed_units = group["dispensed_units"] or 0.0
        back_order_packs = max(required_packs - dispensed_packs, 0.0)
        back_order_units = max(required_units - dispensed_units, 0.0)
        back_order_days = 0.0
        if group["daily_dose"] and group["expected_days"]:
            supplied_days = dispensed_units / group["daily_dose"]
            back_order_days = max(group["expected_days"] - supplied_days, 0.0)
        elif self._is_dispensing_positive(back_order_packs):
            back_order_days = group["fallback_back_order_days"]
        return {
            "packs": self._zero_small_dispensing_value(back_order_packs),
            "units": self._zero_small_dispensing_value(back_order_units),
            "days": self._zero_small_dispensing_value(back_order_days),
        }

    def _find_patient_line_for_dispense_selection(self, selection, patient_lines):
        if not patient_lines:
            return self.env["cdu.batch.patient.line"].browse()

        fulfilment_line = selection.picking_fulfilment_line_id
        if fulfilment_line and fulfilment_line.picking_line_id:
            candidates = fulfilment_line.picking_line_id._get_matching_patient_picking_lines().filtered(
                lambda line: line.prescription_id == self
            )
            if candidates:
                return self._pick_named_patient_line(selection, candidates) or candidates[:1]

        return self._pick_named_patient_line(selection, patient_lines)

    def _pick_named_patient_line(self, selection, patient_lines):
        candidate_names = {
            self._normalize_dispensing_name(selection.openmrs_drug_name),
            self._normalize_dispensing_name(selection.selected_orderable_name),
        }
        candidate_names.discard("")
        if not candidate_names:
            return self.env["cdu.batch.patient.line"].browse()
        return patient_lines.filtered(
            lambda patient_line: (
                self._normalize_dispensing_name(patient_line.drug_name)
                in candidate_names
                or self._normalize_dispensing_name(patient_line.product_id.display_name)
                in candidate_names
            )
        )[:1]

    def _get_dispense_selection_pack_size(self, selection, patient_line=False):
        fulfilment_line = selection.picking_fulfilment_line_id
        return (
            selection.selected_pack_size
            or (patient_line.pack_size if patient_line else 0)
            or fulfilment_line.selected_pack_size
            or fulfilment_line.pack_size
            or fulfilment_line.picking_line_id.pack_size
            or 0.0
        )

    def _normalize_dispensing_name(self, value):
        return (value or "").strip().lower()

    def _is_dispensing_positive(self, value):
        return (
            float_compare(
                value or 0.0,
                0.0,
                precision_digits=self._DISPENSING_FLOAT_PRECISION_DIGITS,
            )
            > 0
        )

    def _zero_small_dispensing_value(self, value):
        return (
            0.0
            if float_is_zero(
                value or 0.0,
                precision_digits=self._DISPENSING_FLOAT_PRECISION_DIGITS,
            )
            else value
        )

    def _format_dispensing_number(self, value, decimals=2):
        value = self._zero_small_dispensing_value(value or 0.0)
        if float(value).is_integer():
            return str(int(value))
        formatted = "%%.%sf" % decimals
        return (formatted % value).rstrip("0").rstrip(".")

    def _format_dispensing_unit(self, value, singular, plural):
        label = (
            singular
            if float_compare(
                value or 0.0,
                1.0,
                precision_digits=self._DISPENSING_FLOAT_PRECISION_DIGITS,
            )
            == 0
            else plural
        )
        return "%s %s" % (self._format_dispensing_number(value), label)

    def _format_dispensing_back_order_short(self, packs, units, days):
        parts = []
        if self._is_dispensing_positive(packs):
            parts.append(self._format_dispensing_unit(packs, _("pack"), _("packs")))
        if self._is_dispensing_positive(units):
            parts.append(self._format_dispensing_unit(units, _("unit"), _("units")))
        if self._is_dispensing_positive(days):
            parts.append(self._format_dispensing_unit(days, _("day"), _("days")))
        return ", ".join(parts) if parts else False

    def _format_dispensing_back_order_line(self, medicine, packs, units, days):
        return "%s: %s" % (
            medicine,
            self._format_dispensing_back_order_short(packs, units, days)
            or _("No back order"),
        )

    def action_refresh_elmis_product_catalog(self):
        self.ensure_one()
        self._ensure_cdu_groups(
            "cdu_prescription.group_cdu_data_clerk",
            "cdu_prescription.group_cdu_dispensing_officer",
        )
        self._ensure_states(
            (
                "awaiting_verification",
                "awaiting_validation",
                "rejected_to_call_center",
                "rejected_to_facility",
            )
        )
        products = self.env["cdu.elmis.stock.service"].refresh_product_catalog()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Generic medicine catalogue refreshed"),
                "message": _("%s eligible medicines are ready for selection, including medicines without usable stock.")
                % len(products),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def action_open_dispensing(self):
        self.ensure_one()
        self._ensure_cdu_groups(
            "cdu_prescription.group_cdu_dispensing_officer",
            "cdu_prescription.group_cdu_admin",
        )
        self._ensure_states(("awaiting_dispensing",))
        dispense = self.env["cdu.dispense"].search(
            [("prescription_id", "=", self.id)],
            limit=1,
        )
        if dispense and dispense.state == "cancelled":
            dispense._restart_cancelled_dispensing()
        if not dispense:
            dispense = self.env["cdu.dispense"].create({"prescription_id": self.id})
        auth_action = dispense._auto_refresh_production_stock(silent=False)
        if auth_action:
            return auth_action
        return {
            "type": "ir.actions.act_window",
            "name": _("Dispense Prescription"),
            "res_model": "cdu.dispense",
            "res_id": dispense.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
        }

    def action_open_bagging_qa(self):
        self.ensure_one()
        self._ensure_cdu_groups(
            "cdu_prescription.group_cdu_dispensing_officer",
            "cdu_prescription.group_cdu_admin",
        )
        self._ensure_states(("awaiting_bagging_qa",))
        dispense = self.env["cdu.dispense"].search(
            [("prescription_id", "=", self.id)],
            limit=1,
        )
        if not dispense:
            raise UserError(_("This prescription has no dispense record yet."))
        if dispense.state != "confirmed":
            raise UserError(_("Dispensing must be confirmed before Bagging / QA."))
        qa = self.env["cdu.bagging.qa"].search(
            [("prescription_id", "=", self.id)],
            limit=1,
        )
        if not qa:
            qa = self.env["cdu.bagging.qa"].create(
                {
                    "prescription_id": self.id,
                    "dispense_id": dispense.id,
                }
            )
        return qa._action_open()

    def action_reprint_dispensing_labels(self):
        self.ensure_one()
        self._ensure_cdu_groups(
            "cdu_prescription.group_cdu_dispensing_officer",
            "cdu_prescription.group_cdu_admin",
        )
        if self.state not in self._POST_DISPENSING_STATES:
            raise UserError(
                _("Labels can only be reprinted after dispensing has been confirmed.")
            )
        dispense = self.env["cdu.dispense"].search(
            [("prescription_id", "=", self.id)],
            limit=1,
        )
        if not dispense or dispense.state != "confirmed":
            raise UserError(_("This prescription has no confirmed dispensing record."))
        return dispense.action_print_labels()
