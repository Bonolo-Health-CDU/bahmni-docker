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

    served_days = fields.Integer(string="Served Days")

    remaining_days = fields.Integer(string="Remaining Days")

    required_quantity = fields.Float(string="Required Quantity")

    available_quantity = fields.Float(string="Available Quantity")

    picked_quantity = fields.Float(string="Picked Quantity")

    recalculated_next_drug_pickup_date = fields.Date(
        string="Recalculated Next Drug Pickup Date",
    )

    daily_dose = fields.Float()

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
