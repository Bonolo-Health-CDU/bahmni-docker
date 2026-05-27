from odoo import fields, models


class CduResidualReturnLine(models.Model):
    _name = "cdu.residual.return.line"
    _description = "CDU Residual Stock Return Line"
    _order = "batch_id, orderable_name, lot, id"

    batch_id = fields.Many2one(
        "cdu.batch",
        required=True,
        ondelete="cascade",
        index=True,
    )
    orderable_code = fields.Char(string="eLMIS Orderable Code")
    orderable_id = fields.Char(string="eLMIS Orderable UUID", index=True)
    orderable_name = fields.Char(string="eLMIS Product", required=True)
    lot = fields.Char(string="Batch Number", index=True)
    lot_id = fields.Char(string="eLMIS Lot UUID", index=True)
    lot_expiry = fields.Date(string="Expiry")
    picked_qty = fields.Float(string="Picked Qty", readonly=True)
    dispensed_qty = fields.Float(string="Dispensed Qty", readonly=True)
    residual_qty = fields.Float(string="Residual Qty", readonly=True)
