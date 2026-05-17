from odoo import api, fields, models


class CduElmisStockOption(models.Model):
    _name = "cdu.elmis.stock.option"
    _description = "CDU eLMIS Stock Option"
    _order = "expiration_date, orderable_code, lot"
    _rec_name = "name"

    batch_id = fields.Many2one(
        "cdu.batch",
        required=True,
        ondelete="cascade",
        index=True,
    )
    facility_code = fields.Char(required=True, index=True)
    program_code = fields.Char(required=True, index=True)
    orderable_code = fields.Char(required=True, index=True)
    orderable_id = fields.Char(string="eLMIS Orderable UUID", index=True)
    orderable_name = fields.Char(required=True)
    lot = fields.Char(index=True)
    lot_id = fields.Char(string="eLMIS Lot UUID", index=True)
    stock_on_hand = fields.Integer(required=True)
    expiration_date = fields.Date(index=True)
    occurred_date = fields.Date()
    name = fields.Char(compute="_compute_name", store=True)

    @api.depends("orderable_name", "orderable_code", "lot", "stock_on_hand", "expiration_date")
    def _compute_name(self):
        for option in self:
            bits = [
                option.orderable_name or option.orderable_code,
                "Lot: %s" % (option.lot or "N/A"),
                "SOH: %s" % option.stock_on_hand,
            ]
            if option.expiration_date:
                bits.append("Exp: %s" % option.expiration_date)
            option.name = " | ".join(bits)
