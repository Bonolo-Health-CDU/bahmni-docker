from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class CduDispenseStockSelection(models.Model):
    _name = "cdu.dispense.stock.selection"
    _description = "CDU Dispense Stock Selection"
    _order = "dispense_id, openmrs_drug_name, selected_orderable_name, selected_lot, id"

    dispense_id = fields.Many2one(
        "cdu.dispense",
        required=True,
        ondelete="cascade",
        index=True,
    )
    prescription_id = fields.Many2one(
        "cdu.prescription",
        related="dispense_id.prescription_id",
        store=True,
        readonly=True,
    )
    batch_id = fields.Many2one(
        "cdu.batch",
        related="dispense_id.batch_id",
        store=True,
        readonly=True,
    )
    picking_fulfilment_line_id = fields.Many2one(
        "cdu.picking.fulfilment.line",
        string="Picked Stock Reference",
        ondelete="set null",
        index=True,
    )
    openmrs_drug_name = fields.Char(string="eRegister Drug / Regimen", required=True)
    openmrs_drug_uuid = fields.Char()
    stock_option_id = fields.Many2one(
        "cdu.dispense.stock.option",
        string="Dispense From",
        domain="[('dispense_id', '=', dispense_id), ('stock_on_hand', '>', 0)]",
    )
    selected_orderable_code = fields.Char(string="eLMIS Orderable Code")
    selected_orderable_id = fields.Char(string="eLMIS Orderable UUID")
    selected_orderable_name = fields.Char(string="eLMIS Product")
    selected_lot = fields.Char(string="Batch Number")
    selected_lot_id = fields.Char(string="eLMIS Lot UUID")
    selected_lot_expiry = fields.Date(string="Expiry")
    selected_stock_on_hand = fields.Integer(string="Available SOH", readonly=True)
    quantity_dispensed = fields.Float(string="Dispensed Qty")
    dosage_instructions = fields.Text(string="Dosing / Instructions")

    @api.constrains("quantity_dispensed")
    def _check_quantity_dispensed(self):
        for line in self:
            if line.quantity_dispensed < 0:
                raise ValidationError(_("Dispensed quantity cannot be negative."))

    @api.onchange("stock_option_id")
    def _onchange_stock_option_id(self):
        for line in self:
            line._sync_stock_option()

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if any(values.get("stock_option_id") for values in vals_list):
            records._sync_stock_option()
        return records

    def write(self, vals):
        result = super().write(vals)
        if "stock_option_id" in vals:
            self._sync_stock_option()
        return result

    def _sync_stock_option(self):
        for line in self:
            option = line.stock_option_id
            if not option:
                line.selected_orderable_code = False
                line.selected_orderable_id = False
                line.selected_orderable_name = False
                line.selected_lot = False
                line.selected_lot_id = False
                line.selected_lot_expiry = False
                line.selected_stock_on_hand = 0
                continue
            line.selected_orderable_code = option.orderable_code
            line.selected_orderable_id = option.orderable_id
            line.selected_orderable_name = option.orderable_name
            line.selected_lot = option.lot
            line.selected_lot_id = option.lot_id
            line.selected_lot_expiry = option.expiration_date
            line.selected_stock_on_hand = option.stock_on_hand
