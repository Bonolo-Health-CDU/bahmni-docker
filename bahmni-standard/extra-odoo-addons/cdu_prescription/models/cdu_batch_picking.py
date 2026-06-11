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
            effective_days = (
                line.prescription_repeat_days
                or line.regimen_repeat_days
                or line.cdu_days
                or 0
            )
            daily_dose = line.daily_dose or 0
            pack_size = line.pack_size or 30
            required_units = daily_dose * effective_days
            packs_to_pick = math.ceil(required_units / pack_size) if required_units > 0 else 0
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
