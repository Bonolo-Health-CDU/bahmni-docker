from odoo import _, fields, models
from odoo.exceptions import UserError


class CduBagScanWizard(models.TransientModel):
    _name = "cdu.bag.scan.wizard"
    _description = "CDU Bag Scan Wizard"

    picking_id = fields.Many2one(
        "stock.picking",
        string="Picking",
        domain="[('picking_type_id.name', 'ilike', 'dispensing')]",
        required=True,
    )
    barcode = fields.Char(string="Bag Barcode", required=True)

    def action_scan(self):
        self.ensure_one()
        if not self.picking_id:
            raise UserError(_("Please select a picking."))

        self.picking_id._cdu_scan_bag(self.barcode)
        return {
            "type": "ir.actions.act_window",
            "res_model": "stock.picking",
            "res_id": self.picking_id.id,
            "view_mode": "form",
            "target": "current",
        }
