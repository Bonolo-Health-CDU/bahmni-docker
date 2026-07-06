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
    selected_pack_size = fields.Integer(string="Pack Size")
    selected_lot = fields.Char(string="Batch Number")
    selected_lot_id = fields.Char(string="eLMIS Lot UUID")
    selected_lot_expiry = fields.Date(string="Expiry")
    selected_stock_on_hand = fields.Integer(string="Available Packs", readonly=True)
    quantity_dispensed = fields.Float(string="Dispensed Packs")
    dosage_instructions = fields.Text(
        string="Dosing / Instructions",
        related="prescription_id.dosage_instructions",
        readonly=False,
        store=True,
    )

    def init(self):
        if not self._table_exists("cdu_dispense_stock_option"):
            return

        self.env.cr.execute(
            """
            UPDATE cdu_dispense_stock_selection selection
               SET selected_pack_size = COALESCE(NULLIF(option.pack_size, 0), 30)
              FROM cdu_dispense_stock_option option
             WHERE option.id = selection.stock_option_id
               AND selection.selected_pack_size IS NULL
            """
        )
        if not self._table_exists("cdu_picking_fulfilment_line"):
            return

        self.env.cr.execute(
            """
            UPDATE cdu_dispense_stock_selection selection
               SET selected_pack_size = COALESCE(
                   NULLIF(fulfilment.selected_pack_size, 0),
                   30
               )
              FROM cdu_picking_fulfilment_line fulfilment
             WHERE fulfilment.id = selection.picking_fulfilment_line_id
               AND selection.selected_pack_size IS NULL
            """
        )

        self.env.cr.execute(
            """
            UPDATE cdu_dispense_stock_selection selection
               SET dosage_instructions = prescription.dosage_instructions
              FROM cdu_prescription prescription
             WHERE prescription.id = selection.prescription_id
               AND COALESCE(selection.dosage_instructions, '') !=
                   COALESCE(prescription.dosage_instructions, '')
            """
        )

    def _table_exists(self, table_name):
        self.env.cr.execute("SELECT to_regclass(%s)", (table_name,))
        return bool(self.env.cr.fetchone()[0])

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
                line.selected_pack_size = 0
                line.selected_lot = False
                line.selected_lot_id = False
                line.selected_lot_expiry = False
                line.selected_stock_on_hand = 0
                continue
            line.selected_orderable_code = option.orderable_code
            line.selected_orderable_id = option.orderable_id
            line.selected_orderable_name = option.orderable_name
            line.selected_pack_size = option.pack_size
            line.selected_lot = option.lot
            line.selected_lot_id = option.lot_id
            line.selected_lot_expiry = option.expiration_date
            line.selected_stock_on_hand = option.stock_on_hand
