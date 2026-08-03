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
    prescription_medicine_line_id = fields.Many2one(
        "cdu.prescription.medicine.line",
        string="Prescription Medicine",
        ondelete="set null",
        readonly=True,
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
    dispensed_units = fields.Float(
        string="Dispensed Units",
        compute="_compute_dispensed_units",
        store=True,
        readonly=True,
        help="Pack size multiplied by the number of dispensed packs.",
    )
    dosage_instructions = fields.Text(
        string="Dosing / Instructions",
        help=(
            "Product-specific dosing instructions. Initially copied from the "
            "prescription and then maintained independently for this line."
        ),
    )

    @api.depends("selected_pack_size", "quantity_dispensed")
    def _compute_dispensed_units(self):
        for line in self:
            line.dispensed_units = (
                (line.selected_pack_size or 0)
                * (line.quantity_dispensed or 0)
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
        prepared_values = []
        for values in vals_list:
            values = dict(values)
            if values.get("dispense_id"):
                dispense = self.env["cdu.dispense"].browse(values["dispense_id"])
                if dispense.state == "confirmed":
                    raise ValidationError(
                        _("Dispensing selections are locked after confirmation.")
                    )
            if (
                "dosage_instructions" not in values
                and values.get("dispense_id")
            ):
                medicine_line = self.env["cdu.prescription.medicine.line"].browse(
                    values.get("prescription_medicine_line_id")
                ).exists()
                values["dosage_instructions"] = (
                    medicine_line.dosage_instructions
                    if medicine_line
                    else dispense.prescription_id.dosage_instructions
                )
            prepared_values.append(values)

        records = super().create(prepared_values)
        if any(values.get("stock_option_id") for values in prepared_values):
            records._sync_stock_option()
        return records

    def write(self, vals):
        if any(line.dispense_id.state == "confirmed" for line in self):
            raise ValidationError(
                _("Dispensing selections are locked after confirmation.")
            )
        result = super().write(vals)
        if "stock_option_id" in vals:
            self._sync_stock_option()
        return result

    def unlink(self):
        if any(line.dispense_id.state == "confirmed" for line in self):
            raise ValidationError(
                _("Dispensing selections are locked after confirmation.")
            )
        return super().unlink()

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
