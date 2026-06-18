from odoo import api, fields, models


class CduDispenseStockOption(models.Model):
    _name = "cdu.dispense.stock.option"
    _description = "CDU Dispense eLMIS Stock Option"
    _order = "dispense_id, orderable_name, expiration_date, lot"
    _rec_name = "name"

    dispense_id = fields.Many2one(
        "cdu.dispense",
        required=True,
        ondelete="cascade",
        index=True,
    )
    facility_code = fields.Char(required=True, index=True)
    program_code = fields.Char(required=True, index=True)
    orderable_code = fields.Char(required=True, index=True)
    orderable_id = fields.Char(string="eLMIS Orderable UUID", index=True)
    orderable_name = fields.Char(required=True)
    pack_size = fields.Integer(string="Pack Size")
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
                "Batch: %s" % (option.lot or "N/A"),
            ]
            if option.expiration_date:
                bits.append("Expiry: %s" % option.expiration_date)
            bits.append("Available: %s" % option.stock_on_hand)
            option.name = " | ".join(bits)

    @api.model
    def name_search(self, name="", args=None, operator="ilike", limit=100):
        args = args or []
        if not name:
            return super().name_search(name=name, args=args, operator=operator, limit=limit)

        domain = [
            "|",
            "|",
            ("orderable_name", operator, name),
            ("orderable_code", operator, name),
            ("lot", operator, name),
        ]
        return self.search(domain + args, limit=limit).name_get()
