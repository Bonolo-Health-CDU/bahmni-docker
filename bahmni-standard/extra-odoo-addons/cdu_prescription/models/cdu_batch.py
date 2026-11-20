import math
from datetime import timedelta
from typing import Any

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from .constants import E_LOCKER_DISTRICT_SELECTION


class CduBatch(models.Model):
    _name = "cdu.batch"
    _description = "CDU Workload Batch"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"

    name = fields.Char(
        string="Batch Reference",
        required=True,
        copy=False,
        readonly=True,
        default="/",
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("confirmed", "Confirmed"),
            ("picking_generated", "Picking Generated"),
            ("printed", "Picking List Printed"),
            ("done", "Done"),
        ],
        default="draft",
        tracking=True,
    )
    filter_next_drug_pickup_date_from = fields.Date(string="Pickup Date From")
    filter_next_drug_pickup_date_to = fields.Date(string="Pickup Date To")
    filter_e_locker_district = fields.Selection(
        E_LOCKER_DISTRICT_SELECTION,
        string="E-locker District",
    )
    filter_collection_point_id = fields.Many2one(
        "cdu.collection.point",
        string="Collection Location",
    )
    prescription_ids = fields.One2many(
        "cdu.prescription",
        "batch_id",
        string="Prescriptions in Batch",
    )
    prescription_count = fields.Integer(
        compute="_compute_prescription_count",
        string="Prescription Count",
    )

    picking_line_ids = fields.One2many(
        "cdu.batch.picking.line",
        "batch_id",
        string="Picking Summary",
    )

    patient_picking_line_ids = fields.One2many(
        "cdu.batch.patient.line",
        "batch_id",
        string="Patient Picking Lines",
    )

    repeat_apply_regimen_domain_ids = fields.Many2many(
        "cdu.prescription",
        compute="_compute_repeat_apply_regimen_domain_ids",
        string="Regimens In Batch",
    )
    repeat_apply_line_ids = fields.One2many(
        "cdu.batch.regimen.repeat.line",
        "batch_id",
        string="Regimen Repeat Days",
    )

    @api.depends("prescription_ids")
    def _compute_prescription_count(self):
        for batch in self:
            batch.prescription_count = len(batch.prescription_ids)

    def _normalize_repeat_regimen_text(self, regimen):
        return (regimen or "").strip()

    @api.depends("prescription_ids.regimen_prescribed_raw")
    def _compute_repeat_apply_regimen_domain_ids(self):
        for batch in self:
            regimen_option_ids = []
            seen_regimens = set()
            for prescription in batch.prescription_ids:
                regimen = batch._normalize_repeat_regimen_text(
                    prescription.regimen_prescribed_raw
                )
                key = regimen.casefold()
                if regimen and key not in seen_regimens:
                    regimen_option_ids.append(prescription.id)
                    seen_regimens.add(key)
            batch.repeat_apply_regimen_domain_ids = [(6, 0, regimen_option_ids)]

    @api.onchange("prescription_ids")
    def _onchange_prescriptions_repeat_apply_lines(self):
        for batch in self:
            batch._set_single_default_repeat_apply_line()

    def _get_available_repeat_regimen_prescriptions(self):
        self.ensure_one()
        used_keys = {
            self._normalize_repeat_regimen_text(
                line.regimen_prescription_id.regimen_prescribed_raw
            ).casefold()
            for line in self.repeat_apply_line_ids
            if line.regimen_prescription_id
        }
        return self.repeat_apply_regimen_domain_ids.filtered(
            lambda prescription: self._normalize_repeat_regimen_text(
                prescription.regimen_prescribed_raw
            ).casefold() not in used_keys
        )

    def _prepare_repeat_apply_line_vals(self, sequence=10):
        self.ensure_one()
        return {
            "regimen_prescription_id": False,
            "days_to_serve_input": False,
            "sequence": sequence,
        }

    def _sync_selected_prescription_repeat_days_defaults(self):
        for batch in self:
            batch.prescription_ids._sync_repeat_days_from_cdu_days()

    def _ensure_default_repeat_apply_line(self):
        for batch in self:
            if batch.repeat_apply_line_ids:
                continue
            batch.repeat_apply_line_ids = [
                (
                    0,
                    0,
                    batch._prepare_repeat_apply_line_vals(),
                )
            ]

    def _set_single_default_repeat_apply_line(self):
        for batch in self:
            commands: list[tuple[int, int, Any]] = [(5, 0, 0)]
            commands.append(
                (
                    0,
                    0,
                    batch._prepare_repeat_apply_line_vals(),
                )
            )
            batch.repeat_apply_line_ids = commands

    @api.onchange("repeat_apply_line_ids")
    def _onchange_repeat_apply_line_ids_keep_blank_row(self):
        for batch in self:
            if not batch.repeat_apply_line_ids:
                batch.repeat_apply_line_ids = [
                    (
                        0,
                        0,
                        batch._prepare_repeat_apply_line_vals(),
                    )
                ]

    def _get_prescriptions_for_repeat_regimen(self, regimen):
        self.ensure_one()
        regimen_key = self._normalize_repeat_regimen_text(regimen).casefold()
        return self.prescription_ids.filtered(
            lambda prescription: self._normalize_repeat_regimen_text(
                prescription.regimen_prescribed_raw
            ).casefold() == regimen_key
        )

    def _get_regimen_repeat_days_by_key(self):
        self.ensure_one()
        return {
            self._normalize_repeat_regimen_text(
                line.regimen_prescription_id.regimen_prescribed_raw
            ).casefold(): line._get_days_to_serve_value()
            for line in self.repeat_apply_line_ids
            if line.regimen_prescription_id and line._get_days_to_serve_value()
        }

    @api.model
    def create(self, vals):
        if vals.get("name", "/") == "/":
            vals["name"] = self.env["ir.sequence"].next_by_code("cdu.batch") or "/"
        batch = super().create(vals)
        if "prescription_ids" in vals and "repeat_apply_line_ids" not in vals:
            batch._set_single_default_repeat_apply_line()
        return batch

    def write(self, vals):
        result = super().write(vals)
        if "prescription_ids" in vals and "repeat_apply_line_ids" not in vals:
            self._set_single_default_repeat_apply_line()
        return result

    @api.onchange(
        "filter_next_drug_pickup_date_from",
        "filter_next_drug_pickup_date_to",
        "filter_e_locker_district",
        "filter_collection_point_id",
    )
    def _onchange_filters_fetch_prescriptions(self):
        for batch in self:
            if batch.state != "draft":
                continue
            domain = batch._get_prescription_domain()
            if batch.filter_collection_point_id:
                domain.append(
                    ("collection_point_id", "=", batch.filter_collection_point_id.id)
                )
            if batch.filter_e_locker_district:
                domain.append(
                    ("e_locker_district", "=ilike", batch.filter_e_locker_district)
                )
            prescriptions = self.env["cdu.prescription"].search(
                domain, order="facility_name, collection_point_id, prescription_date, id"
            )
            prescriptions._sync_repeat_days_from_cdu_days()
            batch.prescription_ids = [(6, 0, prescriptions.ids)]
            batch._set_single_default_repeat_apply_line()

    @api.constrains(
        "filter_next_drug_pickup_date_from",
        "filter_next_drug_pickup_date_to",
    )
    def _check_pickup_date_filters(self):
        for batch in self:
            if not batch.filter_next_drug_pickup_date_from:
                raise ValidationError(_("Pickup Date From is required."))
            if (
                batch.filter_next_drug_pickup_date_from
                and batch.filter_next_drug_pickup_date_to
                and batch.filter_next_drug_pickup_date_from > batch.filter_next_drug_pickup_date_to
            ):
                raise ValidationError(_("Pickup Date From cannot be after Pickup Date To."))

    def _get_prescription_domain(self):
        self.ensure_one()
        current_batch_id = self._origin.id

        domain: list[Any] = [
            ("state", "=", "awaiting_batching"),
        ]
        if current_batch_id:
            domain.extend([
                "|",
                ("batch_id", "=", False),
                ("batch_id", "=", current_batch_id),
            ])
        else:
            domain.append(("batch_id", "=", False))
        if self.filter_next_drug_pickup_date_from:
            domain.append(("next_drug_pickup_date", ">=", self.filter_next_drug_pickup_date_from))
        if self.filter_next_drug_pickup_date_to:
            domain.append(("next_drug_pickup_date", "<=", self.filter_next_drug_pickup_date_to))
        return domain

    def _ensure_batch_workflow_access(self):
        if not (
            self.env.user.has_group("cdu_prescription.group_cdu_dispensing_officer")
            or self.env.user.has_group("cdu_prescription.group_cdu_admin")
        ):
            raise AccessError(_("Only CDU dispensing officers can manage workload batches."))

    def action_confirm_batch(self):
        self._ensure_batch_workflow_access()
        for batch in self:
            batch._check_pickup_date_filters()
            if not batch.prescription_ids:
                raise ValidationError(_("Add at least one prescription before confirming the batch."))
            batch._sync_selected_prescription_repeat_days_defaults()
            batch._validate_selected_prescription_repeat_days()
            batch.prescription_ids.write({"state": "awaiting_picking"})
            batch.picking_line_ids.unlink()
            batch.patient_picking_line_ids.unlink()
        self.write({"state": "confirmed"})
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Workload Batch"),
            "res_model": "cdu.batch",
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_mark_printed(self):
        self._ensure_batch_workflow_access()
        self.write({"state": "printed"})

    def action_mark_done(self):
        self._ensure_batch_workflow_access()
        self.write({"state": "done"})

    # def _generate_picking_lines(self):

    #     self.ensure_one()

    #     self.picking_line_ids.unlink()
    #     self.patient_picking_line_ids.unlink()

    #     summary = {}

    #     for prescription in self.prescription_ids:

    #         regimen = prescription.regimen_id

    #         if not regimen:
    #             continue

    #         cdu_days = prescription.cdu_days_supply

    #         for line in regimen.line_ids:

    #             product = line.product_id

    #             daily_dose = line.daily_dose

    #             pack_size = (
    #                 product.product_tmpl_id.cdu_pack_size
    #                 or 30
    #             )

    #             tablets_required = (
    #                 daily_dose * cdu_days
    #             )

    #             bottles_required = math.ceil(
    #                 tablets_required / pack_size
    #             )

    #             self.env[
    #                 "cdu.batch.patient.line"
    #             ].create({
    #                 "batch_id": self.id,
    #                 "prescription_id": prescription.id,
    #                 "patient_id": prescription.patient_id.id,
    #                 "product_id": product.id,
    #                 "cdu_days": cdu_days,
    #                 "daily_dose": daily_dose,
    #                 "tablets_required": tablets_required,
    #                 "bottles_required": bottles_required,
    #             })

    #             key = product.id

    #             if key not in summary:

    #                 summary[key] = {
    #                     "product_id": product.id,
    #                     "total_tablets": 0,
    #                     "total_bottles": 0,
    #                     "prescription_count": 0,
    #                 }

    #             summary[key]["total_tablets"] += tablets_required

    #             summary[key]["total_bottles"] += bottles_required

    #             summary[key]["prescription_count"] += 1

    #     for vals in summary.values():

    #         vals["batch_id"] = self.id

    #         self.env[
    #             "cdu.batch.picking.line"
    #         ].create(vals)

    def _validate_selected_prescription_repeat_days(self):
        for batch in self:
            invalid = batch.prescription_ids.filtered(lambda prescription: prescription.repeat_days < 0)
            if invalid:
                names = ", ".join(invalid.mapped("name")[:5])
                if len(invalid) > 5:
                    names += ", ..."
                raise ValidationError(
                    _("Repeats in Days cannot be negative for selected prescriptions: %s")
                    % names
                )
            excessive = batch.prescription_ids.filtered(
                lambda prescription: prescription.repeat_days
                and prescription.repeat_days > (prescription.cdu_days_supply or 0)
            )
            if excessive:
                names = ", ".join(excessive.mapped("name")[:5])
                if len(excessive) > 5:
                    names += ", ..."
                raise ValidationError(
                    _("Repeat Days cannot exceed CDU Days for selected prescriptions: %s")
                    % names
                )

    def action_apply_regimen_repeat_days(self):
        self.ensure_one()
        self._ensure_batch_workflow_access()
        if not self.repeat_apply_line_ids:
            raise ValidationError(_("No regimens found in selected prescriptions."))

        seen_regimen_keys = set()
        applied_prescription_count = 0
        for line in self.repeat_apply_line_ids:
            has_regimen = bool(line.regimen_prescription_id)
            has_days = bool((line.days_to_serve_input or "").strip())
            if not has_regimen and not has_days:
                continue
            if not has_regimen:
                raise ValidationError(_("Regimen is required."))
            days_to_serve = line._get_days_to_serve_value(required=True)
            line._validate_days_to_serve(days_to_serve)

            selected_regimen = self._normalize_repeat_regimen_text(
                line.regimen_prescription_id.regimen_prescribed_raw
            )
            selected_regimen_key = selected_regimen.casefold()
            if selected_regimen_key in seen_regimen_keys:
                raise ValidationError(
                    _("The same regimen cannot appear more than once.")
                )
            seen_regimen_keys.add(selected_regimen_key)

            matching_prescriptions = self._get_prescriptions_for_repeat_regimen(
                selected_regimen
            )
            if not matching_prescriptions:
                continue
            excessive_prescriptions = matching_prescriptions.filtered(
                lambda prescription: days_to_serve
                > (prescription.cdu_days_supply or 0)
            )
            if excessive_prescriptions:
                names = ", ".join(excessive_prescriptions.mapped("name")[:5])
                if len(excessive_prescriptions) > 5:
                    names += ", ..."
                raise ValidationError(
                    _(
                        "%(regimen)s: Repeat Days To Serve cannot exceed CDU Days "
                        "for matching prescriptions: %(names)s"
                    )
                    % {"regimen": selected_regimen, "names": names}
                )

            line.write({
                "days_to_serve": days_to_serve,
                "days_to_serve_input": str(days_to_serve),
            })
            matching_prescriptions.write({"repeat_days": days_to_serve})
            applied_prescription_count += len(matching_prescriptions)

        if not applied_prescription_count:
            raise ValidationError(_("No selected prescriptions match the configured regimens."))

        if self.patient_picking_line_ids or self.picking_line_ids:
            self._generate_picking_lines()

        return {"type": "ir.actions.client", "tag": "reload"}

    # Locate the _generate_picking_lines method in cdu_batch.py and update the loop logic:

    # def _generate_picking_lines(self):
    #     self.ensure_one()

    #     self.picking_line_ids.unlink()
    #     self.patient_picking_line_ids.unlink()

    #     summary = {}

    #     for prescription in self.prescription_ids:
    #         regimen = prescription.regimen_id
    #         if not regimen:
    #             continue

    #         # Uses the newly computed CDU specific window days
    #         cdu_days = prescription.cdu_days_supply
    #         if cdu_days <= 0:
    #             continue

    #         for line in regimen.line_ids:
    #             product = line.product_id
    #             daily_dose = line.daily_dose
                
    #             # Safe fallback to 30 if pack size is 0 or False
    #             pack_size = product.product_tmpl_id.cdu_pack_size or 30

    #             # Formula: Dosage X Quantity (in drug days)
    #             tablets_required = daily_dose * cdu_days

    #             # Formula: Bottles to be dispensed (Rounded up to full pack size)
    #             bottles_required = math.ceil(tablets_required / pack_size)

    #             self.env["cdu.batch.patient.line"].create({
    #                 "batch_id": self.id,
    #                 "prescription_id": prescription.id,
    #                 "patient_id": prescription.patient_id.id,
    #                 "product_id": product.id,
    #                 "cdu_days": cdu_days,
    #                 "daily_dose": daily_dose,
    #                 "tablets_required": tablets_required,
    #                 "bottles_required": bottles_required,
    #             })

    #             key = product.id
    #             if key not in summary:
    #                 summary[key] = {
    #                     "product_id": product.id,
    #                     "total_tablets": 0,
    #                     "total_bottles": 0,
    #                     "prescription_count": 0,
    #                 }

    #             summary[key]["total_tablets"] += tablets_required
    #             summary[key]["total_bottles"] += bottles_required
    #             summary[key]["prescription_count"] += 1

    #     for vals in summary.values():
    #         vals["batch_id"] = self.id
    #         self.env["cdu.batch.picking.line"].create(vals)

    def _prepare_picking_line_values(self):
        self.ensure_one()

        summary = {}
        patient_line_vals = []
        self._sync_selected_prescription_repeat_days_defaults()
        self._validate_selected_prescription_repeat_days()

        for prescription in self.prescription_ids:

            # -------------------------------------------------
            # DAYS SUPPLY
            # -------------------------------------------------

            facility_days = (
                prescription.facility_days_supply or 0
            )

            cdu_days = (
                prescription.cdu_days_supply or 0
            )

            total_days = (
                prescription.total_days_supply or 0
            )

            prescription_repeat_days = prescription.repeat_days or 0
            regimen_repeat_days = prescription_repeat_days
            effective_repeat_days = prescription_repeat_days
            operational_days = effective_repeat_days
            if operational_days <= 0:
                continue

            # The prescription line selected during verification is authoritative.
            # Older records without product lines retain the former regimen/raw fallback.
            if prescription.product_line_ids:
                medicines = [
                    (
                        line.product_id,
                        (
                            line.product_id.product_tmpl_id.cdu_default_daily_dose
                            if line.product_id
                            else 1.0
                        ),
                        (
                            line.product_id.display_name
                            if line.product_id
                            else line.imported_product_name
                        ),
                    )
                    for line in prescription.product_line_ids.sorted(
                        key=lambda product_line: (product_line.sequence, product_line.id)
                    )
                ]
            elif prescription.regimen_id:
                medicines = [
                    (line.product_id, line.daily_dose or 0, line.product_id.display_name)
                    for line in prescription.regimen_id.line_ids
                ]
            else:
                medicines = [
                    (
                        self.env["product.product"],
                        1.0,
                        (prescription.regimen_prescribed_raw or "Unknown Drug").strip(),
                    )
                ]

            for product, daily_dose, drug_name in medicines:
                pack_size = (
                    product.product_tmpl_id.cdu_pack_size
                    if product
                    and product.product_tmpl_id.cdu_catalog_source != "elmis"
                    else 0
                )

                if not drug_name:
                    continue

                # -------------------------------------------------
                # FACILITY SUPPLY
                # -------------------------------------------------

                facility_units_required = (
                    daily_dose * facility_days
                )

                facility_bottles_required = (
                    math.ceil(facility_units_required / pack_size)
                    if facility_units_required > 0 and pack_size > 0
                    else 0
                )

                # -------------------------------------------------
                # CDU SUPPLY
                # -------------------------------------------------

                cdu_units_required = (
                    daily_dose * operational_days
                )

                cdu_bottles_required = (
                    math.ceil(cdu_units_required / pack_size)
                    if cdu_units_required > 0 and pack_size > 0
                    else 0
                )
                picked_units = cdu_bottles_required * pack_size
                actual_supplied_days = picked_units / daily_dose if daily_dose else 0
                back_order_days = max(cdu_days - actual_supplied_days, 0)
                calculated_next_pickup_date = False
                if prescription.next_drug_pickup_date and actual_supplied_days:
                    calculated_next_pickup_date = (
                        prescription.next_drug_pickup_date
                        + timedelta(days=int(actual_supplied_days))
                    )

                # -------------------------------------------------
                # TOTAL SUPPLY
                # -------------------------------------------------

                total_units_required = (
                    daily_dose * total_days
                )

                total_bottles_required = (
                    math.ceil(total_units_required / pack_size)
                    if total_units_required > 0 and pack_size > 0
                    else 0
                )

                # -------------------------------------------------
                # CREATE PATIENT PICKING LINE
                # -------------------------------------------------

                patient_line_vals.append({
                    "batch_id": self.id,

                    "prescription_id": prescription.id,

                    "patient_id": prescription.patient_id.id,

                    "product_id": product.id if product else False,
                    "drug_name": drug_name,

                    # DAYS
                    "facility_days_supply": facility_days,
                    "cdu_days": cdu_days,
                    "regimen_repeat_days": regimen_repeat_days,
                    "prescription_repeat_days": prescription_repeat_days,
                    "effective_repeat_days": effective_repeat_days,
                    "repeat_days": effective_repeat_days,
                    "served_days": int(actual_supplied_days),
                    "remaining_days": int(back_order_days),
                    "total_days_supply": total_days,

                    # DOSING
                    "daily_dose": daily_dose,
                    "pack_size": pack_size,

                    # REPEAT-BASED QUANTITY
                    "required_quantity": cdu_units_required,
                    "required_units": cdu_units_required,
                    "available_quantity": 0,
                    "picked_quantity": picked_units,
                    "picked_units": picked_units,
                    "packs_to_pick": cdu_bottles_required,
                    "actual_supplied_days": actual_supplied_days,
                    "back_order_days": back_order_days,
                    "recalculated_next_drug_pickup_date": calculated_next_pickup_date,
                    "calculated_next_pickup_date": calculated_next_pickup_date,

                    # TOTAL TABLETS
                    "tablets_required": total_units_required,

                    # BOTTLES
                    "facility_bottles_required": facility_bottles_required,
                    "cdu_bottles_required": cdu_bottles_required,
                    "total_bottles_required": total_bottles_required,

                    # IMPORTANT:
                    # Operational warehouse/eLMIS picking
                    # uses CDU quantities ONLY
                    "bottles_required": cdu_bottles_required,
                })

                # -------------------------------------------------
                # BATCH SUMMARY
                # -------------------------------------------------

                key = product.id if product else drug_name

                if key not in summary:
                    summary[key] = {
                        "product_id": product.id if product else False,
                        "unmapped_drug_name": drug_name,
                        "pack_size": pack_size,
                        "total_tablets": 0,
                        "total_bottles": 0,
                        "prescription_count": 0,
                    }

                # Summary reflects CDU operational stock only
                summary[key]["total_tablets"] += cdu_units_required

                summary[key]["total_bottles"] += cdu_bottles_required

                summary[key]["prescription_count"] += 1

        return patient_line_vals, list(summary.values())

    def _generate_picking_lines(self):

        self.ensure_one()

        patient_line_vals, summary_vals = self._prepare_picking_line_values()

        # -------------------------------------------------------------------------
        # UPDATE ONE2MANY RELATIONS (Native ORM approach for UI consistency)
        # -------------------------------------------------------------------------
        self.write({
            "patient_picking_line_ids": [(5, 0, 0)] + [(0, 0, v) for v in patient_line_vals],
            "picking_line_ids": [(5, 0, 0)] + [(0, 0, v) for v in summary_vals]
        })

        # Ensure the changes are flushed to the database so eLMIS logic can read them
        self.flush_recordset(['patient_picking_line_ids', 'picking_line_ids'])

    def _refresh_picking_summary_from_patient_lines(self):
        for batch in self:
            summary = {}
            for line in batch.patient_picking_line_ids:
                key = line.product_id.id if line.product_id else line.drug_name
                if not key:
                    continue
                summary.setdefault(
                    key,
                    {
                        "product_id": line.product_id.id if line.product_id else False,
                        "unmapped_drug_name": line.drug_name,
                        "pack_size": line.pack_size,
                        "total_tablets": 0,
                        "total_bottles": 0,
                        "prescription_count": 0,
                    },
                )
                summary[key]["total_tablets"] += line.required_units or line.required_quantity
                summary[key]["total_bottles"] += line.packs_to_pick or line.bottles_required
                summary[key]["prescription_count"] += 1

            existing_by_key = {
                line.product_id.id if line.product_id else line.unmapped_drug_name: line
                for line in batch.picking_line_ids
            }
            active_keys = set(summary)
            for key, vals in summary.items():
                existing = existing_by_key.get(key)
                if existing:
                    existing.write(vals)
                else:
                    vals["batch_id"] = batch.id
                    self.env["cdu.batch.picking.line"].create(vals)
            batch.picking_line_ids.filtered(
                lambda line: (line.product_id.id if line.product_id else line.unmapped_drug_name)
                not in active_keys
            ).unlink()

            for elmis_line in batch.elmis_picking_line_ids:
                if elmis_line.summary_line_id:
                    elmis_line.quantity_to_pick = elmis_line.summary_line_id.total_bottles or 1.0

    def action_generate_picking_list(self):
        self._ensure_batch_workflow_access()

        for batch in self:

            if not batch.prescription_ids:
                continue

            batch._generate_picking_lines()

        self.write({
            "state": "picking_generated"
        })
        return {"type": "ir.actions.client", "tag": "reload"}


class CduBatchRegimenRepeatLine(models.Model):
    _name = "cdu.batch.regimen.repeat.line"
    _description = "CDU Batch Regimen Repeat Days"
    _order = "sequence, id"

    sequence = fields.Integer(
        default=10,
    )

    batch_id = fields.Many2one(
        "cdu.batch",
        required=True,
        ondelete="cascade",
    )
    regimen_prescription_id = fields.Many2one(
        "cdu.prescription",
        string="Regimen",
    )
    days_to_serve = fields.Integer(
        string="Repeat Days To Serve",
        default=False,
    )
    days_to_serve_input = fields.Char(
        string="Repeat Days To Serve",
        default=False,
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if "days_to_serve_input" not in vals and vals.get("days_to_serve"):
                vals["days_to_serve_input"] = str(vals["days_to_serve"])
            elif "days_to_serve_input" in vals:
                days_to_serve = self._parse_days_to_serve_text(
                    vals.get("days_to_serve_input"),
                    required=False,
                )
                vals["days_to_serve"] = days_to_serve or 0
        return super().create(vals_list)

    def write(self, vals):
        if "days_to_serve_input" in vals:
            days_to_serve = self._parse_days_to_serve_text(
                vals.get("days_to_serve_input"),
                required=False,
            )
            vals = dict(vals, days_to_serve=days_to_serve or 0)
        return super().write(vals)

    def _get_regimen_key(self):
        self.ensure_one()
        return self.batch_id._normalize_repeat_regimen_text(
            self.regimen_prescription_id.regimen_prescribed_raw
        ).casefold()

    @api.model
    def _parse_days_to_serve_text(self, value, required=False):
        text_value = str(value or "").strip()
        if not text_value:
            if required:
                raise ValidationError(_("Repeat Days To Serve is required."))
            return False
        if not text_value.isdigit():
            raise ValidationError(_("Repeat Days To Serve must be a whole number greater than 0."))
        days_to_serve = int(text_value)
        if days_to_serve <= 0:
            raise ValidationError(_("Repeat Days To Serve must be greater than 0."))
        return days_to_serve

    def _get_days_to_serve_value(self, required=False):
        self.ensure_one()
        return self._parse_days_to_serve_text(
            self.days_to_serve_input,
            required=required,
        )

    def _validate_days_to_serve(self, days_to_serve=False):
        for line in self:
            days_to_serve = days_to_serve or line._get_days_to_serve_value()
            if not days_to_serve:
                continue
            if not line.batch_id or not line.regimen_prescription_id:
                continue
            selected_regimen = line.batch_id._normalize_repeat_regimen_text(
                line.regimen_prescription_id.regimen_prescribed_raw
            )
            matching_prescriptions = line.batch_id._get_prescriptions_for_repeat_regimen(
                selected_regimen
            )
            excessive_prescriptions = matching_prescriptions.filtered(
                lambda prescription: days_to_serve
                > (prescription.cdu_days_supply or 0)
            )
            if excessive_prescriptions:
                names = ", ".join(excessive_prescriptions.mapped("name")[:5])
                if len(excessive_prescriptions) > 5:
                    names += ", ..."
                raise ValidationError(
                    _(
                        "%(regimen)s: Repeat Days To Serve cannot exceed CDU Days "
                        "for matching prescriptions: %(names)s"
                    )
                    % {"regimen": selected_regimen, "names": names}
                )

    @api.constrains("days_to_serve_input", "regimen_prescription_id", "batch_id")
    def _check_days_to_serve(self):
        self._validate_days_to_serve()

    @api.onchange("days_to_serve_input")
    def _onchange_days_to_serve(self):
        for line in self:
            text_value = (line.days_to_serve_input or "").strip()
            line.days_to_serve = int(text_value) if text_value.isdigit() else 0

    @api.constrains("batch_id", "regimen_prescription_id")
    def _check_unique_regimen_per_batch(self):
        for line in self:
            if not line.batch_id or not line.regimen_prescription_id:
                continue
            if line.regimen_prescription_id not in line.batch_id.repeat_apply_regimen_domain_ids:
                raise ValidationError(
                    _("Regimen must be selected from prescriptions in the current batch.")
                )
            regimen_key = line._get_regimen_key()
            duplicate = line.batch_id.repeat_apply_line_ids.filtered(
                lambda other: (
                    other != line
                    and other.regimen_prescription_id
                    and other._get_regimen_key() == regimen_key
                )
            )
            if duplicate:
                raise ValidationError(
                    _("The same regimen cannot appear more than once.")
                )
