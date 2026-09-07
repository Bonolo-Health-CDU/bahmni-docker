import math

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare, float_is_zero


class CduPrescription(models.Model):
    _inherit = "cdu.prescription"

    is_backorder = fields.Boolean(
        string="Backorder",
        default=False,
        copy=False,
        index=True,
        tracking=True,
    )
    source_prescription_id = fields.Many2one(
        "cdu.prescription",
        string="Source Prescription",
        readonly=True,
        copy=False,
        ondelete="restrict",
        index=True,
    )
    root_prescription_id = fields.Many2one(
        "cdu.prescription",
        string="Original Prescription",
        readonly=True,
        copy=False,
        ondelete="restrict",
        index=True,
    )
    backorder_child_ids = fields.One2many(
        "cdu.prescription",
        "source_prescription_id",
        string="Subsequent Backorders",
        readonly=True,
    )
    origin_dispense_id = fields.Many2one(
        "cdu.dispense",
        string="Origin Dispense",
        readonly=True,
        copy=False,
        ondelete="restrict",
        index=True,
    )
    backorder_sequence = fields.Integer(readonly=True, copy=False)
    backorder_reason = fields.Selection(
        [
            ("stock_unavailable", "Stock unavailable"),
            ("insufficient_stock", "Insufficient stock"),
            ("damaged_expired", "Stock damaged or expired"),
            ("other", "Other"),
        ],
        readonly=True,
        copy=False,
        tracking=True,
    )
    backorder_notes = fields.Text(readonly=True, copy=False)
    backorder_due_date = fields.Date(readonly=True, copy=False, index=True)
    backorder_required_units = fields.Float(
        compute="_compute_backorder_required_quantity",
        store=True,
        readonly=True,
    )
    backorder_required_packs = fields.Float(
        compute="_compute_backorder_required_quantity",
        store=True,
        readonly=True,
    )
    backorder_stock_status = fields.Selection(
        [
            ("unknown", "Not checked"),
            ("unavailable", "Unavailable"),
            ("partial", "Partially available"),
            ("available", "Available"),
        ],
        string="Stock Availability",
        default="unknown",
        readonly=True,
        copy=False,
        index=True,
        tracking=True,
    )
    backorder_available_units = fields.Float(
        string="Available Stock Units",
        readonly=True,
        copy=False,
    )
    backorder_stock_checked_at = fields.Datetime(
        string="Stock Checked At",
        readonly=True,
        copy=False,
    )
    backorder_stock_summary = fields.Text(
        string="Stock Availability Details",
        readonly=True,
        copy=False,
    )
    backorder_urgent = fields.Boolean(
        string="Urgent",
        default=False,
        copy=False,
        tracking=True,
    )
    backorder_cancellation_reason = fields.Text(
        string="Cancellation Reason",
        copy=False,
        tracking=True,
    )

    _sql_constraints = [
        (
            "unique_backorder_origin_dispense",
            "unique(origin_dispense_id)",
            "A backorder has already been created for this dispensing record.",
        ),
    ]

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
        "is_backorder",
        "product_line_ids.backorder_required_units",
        "product_line_ids.backorder_required_packs",
    )
    def _compute_backorder_required_quantity(self):
        for prescription in self:
            prescription.backorder_required_units = sum(
                prescription.product_line_ids.mapped("backorder_required_units")
            ) if prescription.is_backorder else 0.0
            prescription.backorder_required_packs = sum(
                prescription.product_line_ids.mapped("backorder_required_packs")
            ) if prescription.is_backorder else 0.0

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
                    "pack_size": group["pack_size"],
                    "product_id": group["product_id"],
                    "imported_product_name": group["medicine"],
                    "dosage_instructions": group["dosage_instructions"],
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
        product_line = patient_line.prescription_product_line_id
        if not product_line:
            product_line = self.product_line_ids.filtered(
                lambda line: (
                    patient_line.product_id
                    and line.product_id == patient_line.product_id
                ) or (
                    not patient_line.product_id
                    and self._normalize_dispensing_name(line.imported_product_name)
                    == self._normalize_dispensing_name(patient_line.drug_name)
                )
            )[:1]
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
            "product_id": patient_line.product_id.id or False,
            "dosage_instructions": (
                product_line.dosage_instructions
                or self.dosage_instructions
                or False
            ),
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
            "product_id": False,
            "dosage_instructions": selection.dosage_instructions or False,
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

    def _create_backorder_from_dispense(self, dispense, back_order_lines):
        """Create the outstanding supply as a normal prescription workflow item."""
        self.ensure_one()
        existing = self.search([("origin_dispense_id", "=", dispense.id)], limit=1)
        if existing:
            return existing

        root = self.root_prescription_id or self
        sequence = self.search_count(
            [
                ("is_backorder", "=", True),
                "|",
                ("root_prescription_id", "=", root.id),
                ("source_prescription_id", "=", root.id),
            ]
        ) + 1
        line_commands = []
        for index, line in enumerate(back_order_lines, start=1):
            line_commands.append(
                (
                    0,
                    0,
                    {
                        "sequence": index * 10,
                        "product_id": line["product_id"],
                        "imported_product_name": line["imported_product_name"],
                        "dosage_instructions": line["dosage_instructions"],
                        "source": "backorder",
                        "backorder_required_packs": line["packs"],
                        "backorder_required_units": line["units"],
                        "backorder_required_days": line["days"],
                        "backorder_pack_size": line["pack_size"],
                    },
                )
            )

        today = fields.Date.context_today(self)
        maximum_days = max([line["days"] for line in back_order_lines] or [0])
        values = {
            "name": "%s/BO-%02d" % (root.name, sequence),
            "is_backorder": True,
            "source_prescription_id": self.id,
            "root_prescription_id": root.id,
            "origin_dispense_id": dispense.id,
            "backorder_sequence": sequence,
            "backorder_reason": dispense.backorder_reason,
            "backorder_notes": dispense.backorder_notes,
            "backorder_due_date": today,
            "state": "awaiting_verification",
            "prescription_date": today,
            "next_drug_pickup_date": today,
            "repeat_days": max(1, int(math.ceil(maximum_days))),
            "facility_id": self.facility_id.id,
            "facility_name": self.facility_name,
            "facility_code": self.facility_code,
            "patient_id": self.patient_id.id,
            "patient_identifier": self.patient_identifier,
            "hiv_program_id": self.hiv_program_id,
            "national_id": self.national_id,
            "patient_first_name": self.patient_first_name,
            "patient_date_of_birth": self.patient_date_of_birth,
            "patient_gender": self.patient_gender,
            "patient_phone": self.patient_phone,
            "patient_address": self.patient_address,
            "allergies": self.allergies,
            "has_allergies": self.has_allergies,
            "hiv_diagnosis_date": self.hiv_diagnosis_date,
            "latest_vl_collection_date": self.latest_vl_collection_date,
            "latest_vl_result": self.latest_vl_result,
            "regimen_prescribed_raw": ", ".join(
                line["imported_product_name"] for line in back_order_lines
            ),
            "dosage_instructions": self.dosage_instructions,
            "new_or_revisit": self.new_or_revisit,
            "drug_pickup_point_raw": self.drug_pickup_point_raw,
            "collection_point_id": self.collection_point_id.id,
            "e_locker_district": self.e_locker_district,
            "secondary_contact": self.secondary_contact,
            "prescriber_name": self.prescriber_name,
            "next_clinical_visit_date": self.next_clinical_visit_date,
            "product_line_ids": line_commands,
        }
        backorder = self.with_context(cdu_allow_product_line_sync=True).create(values)
        return backorder

    def action_refresh_backorder_stock(self):
        backorders = self.filtered("is_backorder")
        if not backorders:
            raise UserError(_("Select at least one backorder."))
        self._ensure_cdu_groups(
            "cdu_prescription.group_cdu_data_clerk",
            "cdu_prescription.group_cdu_dispensing_officer",
        )
        params = self.env["ir.config_parameter"].sudo()
        facility_code = params.get_param("cdu.elmis.cdu_store_facility_code")
        program_code = params.get_param("cdu.elmis.default_program_code")
        if not facility_code or not program_code:
            raise UserError(_("Configure the CDU Store Facility and default program first."))

        service = self.env["cdu.elmis.stock.service"]
        service.invalidate_stock_cache(facility_code, program_code)
        payload = service.get_stock_card_summaries(
            facility_code=facility_code,
            program_code=program_code,
            use_cache=False,
            use_user_token=False,
        )
        options = self.env["cdu.batch"].new({})._stock_options_from_payload(
            payload,
            facility_code=facility_code,
            program_code=program_code,
        )
        checked_at = fields.Datetime.now()
        for backorder in backorders:
            summaries = []
            total_available = 0.0
            known_line_count = 0
            fully_available = True
            for line in backorder.product_line_ids:
                mappings = line.product_id.product_tmpl_id.cdu_elmis_orderable_catalog_ids.filtered(
                    lambda mapping: mapping.active
                    and mapping.facility_code == facility_code
                    and mapping.program_code == program_code
                ) if line.product_id else self.env["cdu.elmis.orderable.catalog"]
                identifiers = set(mappings.mapped("orderable_id")) | set(
                    mappings.mapped("orderable_code")
                )
                matching = [
                    option for option in options
                    if (option.get("orderable_id") in identifiers)
                    or (option.get("orderable_code") in identifiers)
                ]
                available_units = sum(
                    option.get("stock_on_hand_units") or 0.0 for option in matching
                )
                required_units = line.backorder_required_units or 0.0
                known = bool(identifiers)
                if known:
                    known_line_count += 1
                total_available += available_units
                fully_available = fully_available and known and available_units >= required_units
                line.with_context(cdu_allow_product_line_sync=True).write(
                    {
                        "backorder_available_units": available_units,
                        "backorder_stock_checked_at": checked_at,
                    }
                )
                summaries.append(
                    _("%s: %s of %s units available")
                    % (
                        line.product_id.display_name or line.imported_product_name,
                        self._format_dispensing_number(available_units),
                        self._format_dispensing_number(required_units),
                    )
                )
            if fully_available and backorder.product_line_ids:
                status = "available"
            elif total_available > 0:
                status = "partial"
            elif known_line_count == len(backorder.product_line_ids):
                status = "unavailable"
            else:
                status = "unknown"
            backorder.write(
                {
                    "backorder_stock_status": status,
                    "backorder_available_units": total_available,
                    "backorder_stock_checked_at": checked_at,
                    "backorder_stock_summary": "\n".join(summaries),
                }
            )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Backorder stock refreshed"),
                "message": _("CDU Store availability was refreshed from eLMIS."),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def action_open_backorder_workflow(self):
        self.ensure_one()
        if not self.is_backorder:
            raise UserError(_("This action is only available for backorders."))
        if self.state == "awaiting_dispensing":
            return self.action_open_dispensing()
        if self.state == "awaiting_bagging_qa":
            return self.action_open_bagging_qa()
        if self.state == "awaiting_picking" and self.batch_id:
            return {
                "type": "ir.actions.act_window",
                "name": _("Backorder Picking"),
                "res_model": "cdu.batch",
                "res_id": self.batch_id.id,
                "view_mode": "form",
                "views": [(False, "form")],
                "target": "main",
            }
        return {
            "type": "ir.actions.act_window",
            "name": self.name,
            "res_model": "cdu.prescription",
            "res_id": self.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "main",
        }

    def action_open_backorder_source(self):
        self.ensure_one()
        source = self.source_prescription_id
        if not source:
            raise UserError(_("This backorder has no source prescription."))
        return {
            "type": "ir.actions.act_window",
            "name": source.name,
            "res_model": "cdu.prescription",
            "res_id": source.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "main",
        }

    def action_open_backorder_chain(self):
        self.ensure_one()
        root = self.root_prescription_id or self
        action = self.env.ref("cdu_elmis.action_cdu_backorder_queue").read()[0]
        action["domain"] = [
            "|",
            ("id", "=", root.id),
            ("root_prescription_id", "=", root.id),
        ]
        action["context"] = {}
        return self._action_as_main_target(action)

    def action_toggle_backorder_urgent(self):
        for backorder in self.filtered("is_backorder"):
            backorder.backorder_urgent = not backorder.backorder_urgent
        return True

    def action_cancel(self):
        backorders_without_reason = self.filtered(
            lambda prescription: prescription.is_backorder
            and not (prescription.backorder_cancellation_reason or "").strip()
        )
        if backorders_without_reason:
            raise UserError(_("Enter a cancellation reason before cancelling a backorder."))
        return super().action_cancel()

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
            "target": "main",
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


class CduPrescriptionProductLine(models.Model):
    _inherit = "cdu.prescription.product.line"

    backorder_available_units = fields.Float(
        string="Available Stock Units",
        readonly=True,
        copy=False,
    )
    backorder_stock_checked_at = fields.Datetime(
        string="Stock Checked At",
        readonly=True,
        copy=False,
    )
