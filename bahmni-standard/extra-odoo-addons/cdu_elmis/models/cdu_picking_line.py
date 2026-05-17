from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class CduPickingLine(models.Model):
    _name = "cdu.picking.line"
    _description = "CDU eLMIS Picking Line"
    _order = "batch_id, prescription_id, id"

    batch_id = fields.Many2one(
        "cdu.batch",
        required=True,
        ondelete="cascade",
        index=True,
    )
    prescription_id = fields.Many2one(
        "cdu.prescription",
        ondelete="set null",
        index=True,
    )
    prescription_item_id = fields.Many2one(
        "cdu.batch.patient.line",
        string="Prescription Item",
        ondelete="set null",
        index=True,
    )
    summary_line_id = fields.Many2one(
        "cdu.batch.picking.line",
        string="Picking Summary Line",
        ondelete="cascade",
        index=True,
    )
    prescription_count = fields.Integer(readonly=True)
    openmrs_drug_name = fields.Char(string="eRegister Drug / Regimen", required=True)
    openmrs_drug_uuid = fields.Char(
        help="Retained for Phase 2 product mapping from OpenMRS drugs to OpenLMIS orderables.",
    )
    selected_orderable_code = fields.Char(string="eLMIS Orderable Code")
    selected_orderable_id = fields.Char(string="eLMIS Orderable UUID")
    selected_orderable_name = fields.Char(string="eLMIS Orderable")
    selected_lot = fields.Char(string="Lot")
    selected_lot_id = fields.Char(string="eLMIS Lot UUID")
    selected_lot_expiry = fields.Date(string="Lot Expiry")
    selected_stock_option_id = fields.Many2one(
        "cdu.elmis.stock.option",
        string="Fulfil With",
        domain="[('batch_id', '=', batch_id)]",
    )
    selected_stock_on_hand = fields.Integer(string="Available SOH", readonly=True)
    quantity_to_pick = fields.Float(required=True)
    quantity_picked = fields.Float()

    _sql_constraints = [
        (
            "unique_prescription_item",
            "unique(prescription_item_id)",
            "Only one eLMIS picking line is allowed per prescription item.",
        ),
        (
            "unique_summary_line",
            "unique(summary_line_id)",
            "Only one eLMIS picking line is allowed per picking summary line.",
        )
    ]

    @api.constrains("quantity_to_pick", "quantity_picked")
    def _check_quantities(self):
        for line in self:
            if line.quantity_to_pick <= 0:
                raise ValidationError(_("Quantity to pick must be greater than zero."))
            if line.quantity_picked < 0:
                raise ValidationError(_("Quantity picked cannot be negative."))

    @api.onchange("selected_stock_option_id")
    def _onchange_selected_stock_option_id(self):
        for line in self:
            line._sync_selected_stock_option()

    def write(self, vals):
        result = super().write(vals)
        if "selected_stock_option_id" in vals:
            self._sync_selected_stock_option()
        return result

    def _sync_selected_stock_option(self):
        for line in self:
            option = line.selected_stock_option_id
            if not option:
                continue
            line.selected_orderable_code = option.orderable_code
            line.selected_orderable_id = option.orderable_id
            line.selected_orderable_name = option.orderable_name
            line.selected_lot = option.lot
            line.selected_lot_id = option.lot_id
            line.selected_lot_expiry = option.expiration_date
            line.selected_stock_on_hand = option.stock_on_hand
            if not line.quantity_picked:
                line.quantity_picked = line.quantity_to_pick
