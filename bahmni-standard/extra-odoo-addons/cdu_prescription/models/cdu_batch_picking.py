import math

from odoo import fields, models, api


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
        required=True,
    )

    total_tablets = fields.Float()

    total_bottles = fields.Integer()

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
        required=True,
    )

    cdu_days = fields.Integer()

    daily_dose = fields.Float()

    tablets_required = fields.Float()

    bottles_required = fields.Integer()
    #required_bottles = fields.Integer()

    allocated_bottles = fields.Integer(
    default=0
    )

    shortfall_bottles = fields.Integer(
        compute="_compute_shortfall",
        store=True,
    )

    required_days_supply = fields.Integer()

    allocated_days_supply = fields.Integer()

    allocated_next_pickup_date = fields.Date()

    allocation_status = fields.Selection(
        [
            ("full", "Fully Allocated"),
            ("partial", "Partially Allocated"),
            ("none", "Not Allocated"),
        ],
        default="none",
    )
    @api.depends(
    "bottles_required",
    "allocated_bottles",
    )
    def _compute_shortfall(self):

        for line in self:

            line.shortfall_bottles = (
                line.bottles_required
                - line.allocated_bottles
            )