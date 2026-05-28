import math

from odoo import fields, models


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

    facility_days_supply = fields.Integer()

    total_days_supply = fields.Integer()

    facility_bottles_required = fields.Integer()

    cdu_bottles_required = fields.Integer()

    total_bottles_required = fields.Integer()

