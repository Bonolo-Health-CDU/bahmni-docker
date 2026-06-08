from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class CduPickingFulfilmentLine(models.Model):
    _name = "cdu.picking.fulfilment.line"
    _description = "CDU eLMIS Picking Fulfilment Line"
    _order = "batch_id, picking_line_id, selected_orderable_name, selected_lot, id"

    batch_id = fields.Many2one(
        "cdu.batch",
        required=True,
        ondelete="cascade",
        index=True,
    )
    picking_line_id = fields.Many2one(
        "cdu.picking.line",
        string="Regimen",
        required=True,
        ondelete="cascade",
        index=True,
        domain="[('batch_id', '=', batch_id)]",
    )
    openmrs_drug_name = fields.Char(
        string="Regimen",
        related="picking_line_id.openmrs_drug_name",
        store=True,
        readonly=True,
    )
    required_quantity = fields.Float(
        string="Required Qty",
        related="picking_line_id.quantity_to_pick",
        readonly=True,
    )
    prescription_count = fields.Integer(
        string="Rx Count",
        related="picking_line_id.prescription_count",
        readonly=True,
    )
    estimated_available_repeats = fields.Char(
        string="Estimated Available Repeats",
        related="picking_line_id.estimated_available_repeats",
        readonly=True,
    )
    repeats_to_dispense = fields.Char(
        string="Repeats To Dispense",
        related="picking_line_id.repeats_to_dispense",
        readonly=True,
    )
    coverage_days = fields.Char(
        string="Coverage Days",
        related="picking_line_id.coverage_days",
        readonly=True,
    )
    required_units = fields.Float(
        string="Required Units/Tablets",
        related="picking_line_id.required_units",
        readonly=True,
    )
    pack_size = fields.Integer(
        string="Pack Size",
        related="picking_line_id.pack_size",
        readonly=True,
    )
    packs_to_pick = fields.Float(
        string="Packs/Bottles To Pick",
        related="picking_line_id.packs_to_pick",
        readonly=True,
    )
    selected_stock_option_id = fields.Many2one(
        "cdu.elmis.stock.option",
        string="Fulfil With",
        domain="[('batch_id', '=', batch_id), ('stock_on_hand', '>', 0)]",
    )
    selected_orderable_code = fields.Char(string="eLMIS Orderable Code")
    selected_orderable_id = fields.Char(string="eLMIS Orderable UUID")
    selected_orderable_name = fields.Char(string="Fulfil With eLMIS Product")
    selected_lot = fields.Char(string="Batch Number")
    selected_lot_id = fields.Char(string="eLMIS Lot UUID")
    selected_lot_expiry = fields.Date(string="Expiry Date")
    selected_stock_on_hand = fields.Integer(string="Available SOH", readonly=True)
    quantity_picked = fields.Float(string="Picked Qty")

    @api.constrains("quantity_picked")
    def _check_quantity_picked(self):
        for line in self:
            if line.quantity_picked < 0:
                raise ValidationError(_("Picked quantity cannot be negative."))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if any(values.get("selected_stock_option_id") for values in vals_list):
            records._sync_selected_stock_option()
        return records

    @api.onchange("picking_line_id")
    def _onchange_picking_line_id(self):
        for line in self:
            if line.picking_line_id and not line.batch_id:
                line.batch_id = line.picking_line_id.batch_id

    @api.onchange("selected_stock_option_id")
    def _onchange_selected_stock_option_id(self):
        for line in self:
            line._sync_selected_stock_option()

    def write(self, vals):
        result = super().write(vals)
        if "selected_stock_option_id" in vals:
            self._sync_selected_stock_option()
        return result

            # def _sync_selected_stock_option(self):
            #     for line in self:
            #         option = line.selected_stock_option_id
            #         if not option:
            #             line.selected_orderable_code = False
            #             line.selected_orderable_id = False
            #             line.selected_orderable_name = False
            #             line.selected_lot = False
            #             line.selected_lot_id = False
            #             line.selected_lot_expiry = False
            #             line.selected_stock_on_hand = 0
            #             continue
            #         line.selected_orderable_code = option.orderable_code
            #         line.selected_orderable_id = option.orderable_id
            #         line.selected_orderable_name = option.orderable_name
            #         line.selected_lot = option.lot
            #         line.selected_lot_id = option.lot_id
            #         line.selected_lot_expiry = option.expiration_date
            #         line.selected_stock_on_hand = option.stock_on_hand
            #         if not line.quantity_picked:
            #             line.quantity_picked = line.required_quantity or 1
    
    # Locate the _sync_selected_stock_option method in cdu_picking_fulfilment_line.py and verify the default assignment:

    def _sync_selected_stock_option(self):
        for line in self:
            option = line.selected_stock_option_id
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
            
            default_quantity = min(line.required_quantity or 0, option.stock_on_hand or 0)
            if not line.quantity_picked or line.quantity_picked > option.stock_on_hand:
                line.quantity_picked = default_quantity
