import math
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class CduBatchPickingLine(models.Model):
    _name = "cdu.batch.picking.line"
    _description = "CDU Batch Picking Summary"

    batch_id = fields.Many2one(
        "cdu.batch",
        required=True,
        ondelete="cascade",
    )

    product_id = fields.Many2one(
        "product.product",
    )

    unmapped_drug_name = fields.Char(string="Drug (Unmapped)")

    elmis_product_name = fields.Char(string="eLMIS Product")

    pack_size = fields.Integer(string="Pack Size")

    total_tablets = fields.Float()

    total_bottles = fields.Float()

    prescription_count = fields.Integer()


class CduBatchPatientLine(models.Model):
    _name = "cdu.batch.patient.line"
    _description = "CDU Patient Picking Line"

    batch_id = fields.Many2one(
        "cdu.batch",
        required=True,
        ondelete="cascade",
    )

    prescription_id = fields.Many2one(
        "cdu.prescription",
        required=True,
    )

    prescription_medicine_line_id = fields.Many2one(
        "cdu.prescription.medicine.line",
        string="Prescription Medicine",
        ondelete="set null",
        index=True,
    )

    patient_id = fields.Many2one(
        "res.partner",
    )

    product_id = fields.Many2one(
        "product.product",
    )

    drug_name = fields.Char(string="Drug Name")

    cdu_days = fields.Integer()

    repeat_days = fields.Integer(string="Repeats in Days")

    regimen_repeat_days = fields.Integer(string="Regimen Repeat Days")

    prescription_repeat_days = fields.Integer(string="Prescription Repeat Days")

    effective_repeat_days = fields.Integer(string="Effective Repeat Days")

    served_days = fields.Integer(string="Served Days")

    remaining_days = fields.Integer(string="Remaining Days")

    required_quantity = fields.Float(string="Required Quantity")

    required_units = fields.Float(string="Required Units")

    available_quantity = fields.Float(string="Available Quantity")

    picked_quantity = fields.Float(string="Picked Quantity")

    picked_units = fields.Float(string="Picked Units")

    packs_to_pick = fields.Float(string="Packs/Bottles To Pick")

    actual_supplied_days = fields.Float(string="Actual Supplied Days")

    back_order_days = fields.Float(string="Back Order Days")

    recalculated_next_drug_pickup_date = fields.Date(
        string="Recalculated Next Drug Pickup Date",
    )

    calculated_next_pickup_date = fields.Date(string="Calculated Next Pickup Date")

    daily_dose = fields.Float()

    pack_size = fields.Integer(string="Pack Size")

    tablets_required = fields.Float()

    bottles_required = fields.Integer()

    facility_days_supply = fields.Integer()

    total_days_supply = fields.Integer()

    facility_bottles_required = fields.Integer()

    cdu_bottles_required = fields.Integer()

    total_bottles_required = fields.Integer()

    medication_display = fields.Char(
        compute="_compute_picking_display_fields",
        string="Medication",
    )

    regimen_code_display = fields.Char(
        compute="_compute_picking_display_fields",
        string="Regimen Code",
    )

    daily_dose_display = fields.Char(
        compute="_compute_picking_display_fields",
        string="Daily Dose Display",
    )

    days_to_dispense_display = fields.Char(
        compute="_compute_picking_display_fields",
        string="Days to Dispense",
    )

    to_pick_pack_display = fields.Char(
        compute="_compute_picking_display_fields",
        string="Packs to Pick",
    )

    to_pick_units_display = fields.Char(
        compute="_compute_picking_display_fields",
        string="Units to Pick",
    )

    next_pickup_supply_display = fields.Char(
        compute="_compute_picking_display_fields",
        string="Supply Coverage",
    )

    picking_variance_label = fields.Char(
        compute="_compute_picking_display_fields",
        string="Picking Variance",
    )

    picking_variance_class = fields.Char(
        compute="_compute_picking_display_fields",
        string="Picking Variance Style",
    )

    prescribed_days_display = fields.Char(
        compute="_compute_picking_display_fields",
        string="Prescribed",
    )

    facility_cap_display = fields.Char(
        compute="_compute_picking_display_fields",
        string="Facility Cap",
    )

    cdu_cycle_display = fields.Char(
        compute="_compute_picking_display_fields",
        string="CDU Cycle",
    )

    regimen_override_display = fields.Char(
        compute="_compute_picking_display_fields",
        string="Regimen Override",
    )

    required_units_display = fields.Char(
        compute="_compute_picking_display_fields",
        string="Required",
    )

    picked_units_display = fields.Char(
        compute="_compute_picking_display_fields",
        string="Picked",
    )

    back_order_display = fields.Char(
        compute="_compute_picking_display_fields",
        string="Back Order",
    )

    def _format_cdu_number(self, value, decimals=0):
        value = value or 0
        if decimals:
            formatted = f"{value:.{decimals}f}"
            return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted
        if float(value).is_integer():
            return str(int(value))
        return f"{value:.2f}".rstrip("0").rstrip(".")

    def _format_cdu_units(self, value, decimals=0):
        return "%s units" % self._format_cdu_number(value, decimals=decimals)

    def _format_cdu_days(self, value, decimals=0):
        return "%s days" % self._format_cdu_number(value, decimals=decimals)

    @api.depends(
        "patient_id",
        "product_id",
        "drug_name",
        "prescription_id.regimen_prescribed_raw",
        "daily_dose",
        "effective_repeat_days",
        "prescription_repeat_days",
        "regimen_repeat_days",
        "facility_days_supply",
        "cdu_days",
        "required_units",
        "picked_units",
        "packs_to_pick",
        "actual_supplied_days",
        "back_order_days",
    )
    def _compute_picking_display_fields(self):
        for line in self:
            medication = line.product_id.display_name or line.drug_name or ""
            regimen_code = line.prescription_id.regimen_prescribed_raw or line.drug_name or ""
            daily_dose = line.daily_dose or 0
            effective_days = line.effective_repeat_days or line.prescription_repeat_days or 0
            picked_units = line.picked_units or 0
            required_units = line.required_units or line.required_quantity or 0
            extra_units = max(picked_units - required_units, 0)
            short_units = max((line.back_order_days or 0) * daily_dose, 0)

            line.medication_display = medication
            line.regimen_code_display = regimen_code
            line.daily_dose_display = "%s/day" % self._format_cdu_number(daily_dose)
            line.days_to_dispense_display = self._format_cdu_days(effective_days)
            line.to_pick_pack_display = "%s packs" % self._format_cdu_number(line.packs_to_pick)
            line.to_pick_units_display = self._format_cdu_units(picked_units or required_units)
            line.next_pickup_supply_display = "%s supply" % self._format_cdu_days(
                line.actual_supplied_days,
                decimals=0,
            )
            line.prescribed_days_display = self._format_cdu_days(
                line.prescription_repeat_days or effective_days
            )
            line.facility_cap_display = self._format_cdu_days(line.facility_days_supply)
            line.cdu_cycle_display = self._format_cdu_days(line.cdu_days)
            line.regimen_override_display = (
                self._format_cdu_days(line.regimen_repeat_days)
                if line.regimen_repeat_days and line.regimen_repeat_days != line.cdu_days
                else "None"
            )
            line.required_units_display = self._format_cdu_units(required_units, decimals=2)
            line.picked_units_display = self._format_cdu_units(picked_units, decimals=2)
            line.back_order_display = self._format_cdu_days(line.back_order_days, decimals=2)

            if short_units:
                line.picking_variance_label = "Short %s" % self._format_cdu_units(short_units)
                line.picking_variance_class = "is-warning"
            elif extra_units:
                line.picking_variance_label = "+%s" % self._format_cdu_units(extra_units)
                line.picking_variance_class = "is-info"
            else:
                line.picking_variance_label = False
                line.picking_variance_class = False

    @api.constrains("repeat_days", "served_days", "remaining_days")
    def _check_repeat_day_quantities(self):
        for line in self:
            if line.repeat_days < 0:
                raise ValidationError("Repeats in Days cannot be negative.")
            if line.served_days < 0:
                raise ValidationError("Served Days cannot be negative.")
            if line.remaining_days < 0:
                raise ValidationError("Remaining Days cannot be negative.")

    @api.constrains("prescription_repeat_days")
    def _check_prescription_repeat_days(self):
        for line in self:
            if line.prescription_repeat_days < 0:
                raise ValidationError("Prescription Repeat Days cannot be negative.")
            if (
                line.prescription_repeat_days
                and line.prescription_repeat_days > (line.cdu_days or 0)
            ):
                raise ValidationError(
                    "Prescription Repeat Days cannot exceed CDU Days."
                )

    @api.onchange("prescription_repeat_days")
    def _onchange_prescription_repeat_days(self):
        for line in self:
            line._apply_repeat_day_calculation()

    def write(self, vals):
        result = super().write(vals)
        if "prescription_repeat_days" in vals:
            for line in self:
                line.prescription_id.repeat_days = line.prescription_repeat_days
            self._apply_repeat_day_calculation()
            self.mapped("batch_id")._refresh_picking_summary_from_patient_lines()
        return result

    def _apply_repeat_day_calculation(self):
        for line in self:
            effective_days = line.prescription_repeat_days
            daily_dose = line.daily_dose or 0
            pack_size = line.pack_size or 0
            required_units = daily_dose * effective_days
            packs_to_pick = (
                math.ceil(required_units / pack_size)
                if required_units > 0 and pack_size > 0
                else 0
            )
            picked_units = packs_to_pick * pack_size
            actual_supplied_days = picked_units / daily_dose if daily_dose else 0
            back_order_days = max((line.cdu_days or 0) - actual_supplied_days, 0)
            next_pickup_date = False
            if line.prescription_id.next_drug_pickup_date and actual_supplied_days:
                next_pickup_date = line.prescription_id.next_drug_pickup_date + timedelta(
                    days=int(actual_supplied_days)
                )

            line.effective_repeat_days = effective_days
            line.repeat_days = effective_days
            line.required_units = required_units
            line.required_quantity = required_units
            line.packs_to_pick = packs_to_pick
            line.picked_units = picked_units
            line.picked_quantity = picked_units
            line.actual_supplied_days = actual_supplied_days
            line.served_days = int(actual_supplied_days)
            line.back_order_days = back_order_days
            line.remaining_days = int(back_order_days)
            line.cdu_bottles_required = packs_to_pick
            line.bottles_required = packs_to_pick
            line.recalculated_next_drug_pickup_date = next_pickup_date
            line.calculated_next_pickup_date = next_pickup_date
