from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class CduPickingLine(models.Model):
    _name = "cdu.picking.line"
    _description = "CDU eLMIS Picking Line"
    _order = "batch_id, openmrs_drug_name, id"
    _rec_name = "openmrs_drug_name"

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
    openmrs_drug_name = fields.Char(string="Regimen", required=True)
    openmrs_drug_uuid = fields.Char(
        help="Retained for Phase 2 product mapping from OpenMRS drugs to OpenLMIS orderables.",
    )
    selected_orderable_code = fields.Char(string="eLMIS Orderable Code")
    selected_orderable_id = fields.Char(string="eLMIS Orderable UUID")
    selected_orderable_name = fields.Char(string="Fulfil With eLMIS Product")
    selected_lot = fields.Char(string="Batch Number")
    selected_lot_id = fields.Char(string="eLMIS Lot UUID")
    selected_lot_expiry = fields.Date(string="Expiry")
    selected_stock_option_id = fields.Many2one(
        "cdu.elmis.stock.option",
        string="Fulfil With",
        domain="[('batch_id', '=', batch_id), ('stock_on_hand', '>', 0)]",
    )
    selected_stock_on_hand = fields.Integer(string="Available Packs", readonly=True)
    available_quantity = fields.Float(
        string="Available Quantity",
        compute="_compute_stock_quantities",
    )
    picked_quantity = fields.Float(
        string="Picked Quantity",
        compute="_compute_stock_quantities",
    )
    remaining_quantity = fields.Float(
        string="Remaining Quantity",
        compute="_compute_stock_quantities",
    )
    remaining_packs_to_pick = fields.Float(
        string="Remaining Packs",
        compute="_compute_stock_quantities",
    )
    pack_size = fields.Integer(
        string="Pack Size",
        compute="_compute_report_quantity_fields",
    )
    required_units = fields.Float(
        string="Required Units/Tablets",
        compute="_compute_report_quantity_fields",
    )
    packs_to_pick = fields.Float(
        string="Packs/Bottles To Pick",
        compute="_compute_report_quantity_fields",
    )
    estimated_available_repeats = fields.Char(
        string="Estimated Available Repeats",
        compute="_compute_repeat_report_fields",
    )
    repeats_to_dispense = fields.Char(
        string="Repeats To Dispense",
        compute="_compute_repeat_report_fields",
    )
    coverage_days = fields.Char(
        string="Coverage Days",
        compute="_compute_repeat_report_fields",
    )
    quantity_to_pick = fields.Float(
        string="Required Packs",
        required=True
    )
    quantity_picked = fields.Float(string="Picked Packs")
    fulfilment_line_ids = fields.One2many(
        "cdu.picking.fulfilment.line",
        "picking_line_id",
        string="eLMIS Fulfilment Lines",
    )
    total_quantity_picked = fields.Float(
        string="Total Picked Packs",
        compute="_compute_total_quantity_picked",
    )

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

    @api.depends("fulfilment_line_ids.quantity_picked")
    def _compute_total_quantity_picked(self):
        for line in self:
            line.total_quantity_picked = sum(line.fulfilment_line_ids.mapped("quantity_picked"))

    @api.depends(
        "fulfilment_line_ids.quantity_picked",
        "fulfilment_line_ids.selected_stock_on_hand",
        "summary_line_id.pack_size",
        "required_units",
        "pack_size",
    )
    def _compute_stock_quantities(self):
        for line in self:
            pack_size = line.summary_line_id.pack_size or line.pack_size or 30
            available_packs = sum(line.fulfilment_line_ids.mapped("selected_stock_on_hand"))
            picked_packs = sum(line.fulfilment_line_ids.mapped("quantity_picked"))
            line.available_quantity = available_packs * pack_size
            line.picked_quantity = picked_packs * pack_size
            line.remaining_quantity = max((line.required_units or 0.0) - line.picked_quantity, 0.0)
            line.remaining_packs_to_pick = max((line.quantity_to_pick or 0.0) - picked_packs, 0.0)

    @api.depends(
        "quantity_to_pick",
        "summary_line_id.pack_size",
        "selected_stock_option_id.pack_size",
        "fulfilment_line_ids.selected_stock_option_id.pack_size",
        "summary_line_id.total_tablets",
        "batch_id.patient_picking_line_ids.effective_repeat_days",
        "batch_id.patient_picking_line_ids.required_units",
        "batch_id.patient_picking_line_ids.daily_dose",
        "batch_id.patient_picking_line_ids.drug_name",
        "batch_id.patient_picking_line_ids.product_id",
    )
    def _compute_report_quantity_fields(self):
        for line in self:
            matching_patient_lines = line._get_matching_patient_picking_lines()
            pack_size = (
                line.selected_stock_option_id.pack_size
                or next(
                    (
                        option.pack_size
                        for option in line.fulfilment_line_ids.mapped(
                            "selected_stock_option_id"
                        )
                        if option.pack_size
                    ),
                    0,
                )
                or line.summary_line_id.pack_size
                or 30
            )
            required_units = sum(matching_patient_lines.mapped("required_units"))

            line.pack_size = pack_size
            line.required_units = (
                required_units or line.summary_line_id.total_tablets or 0.0
            )
            line.packs_to_pick = line.quantity_to_pick

    @api.depends(
        "batch_id.patient_picking_line_ids.effective_repeat_days",
        "batch_id.patient_picking_line_ids.drug_name",
        "batch_id.patient_picking_line_ids.product_id",
        "summary_line_id.product_id",
        "openmrs_drug_name",
    )
    def _compute_repeat_report_fields(self):
        for line in self:
            matching_patient_lines = line._get_matching_patient_picking_lines()
            total_coverage_days = sum(matching_patient_lines.mapped("effective_repeat_days"))

            if total_coverage_days:
                estimated_repeats = total_coverage_days / 30.0
                repeat_value = (
                    str(int(estimated_repeats))
                    if estimated_repeats.is_integer()
                    else ("%.2f" % estimated_repeats).rstrip("0").rstrip(".")
                )
                line.estimated_available_repeats = repeat_value
                line.coverage_days = str(int(total_coverage_days))
            else:
                line.estimated_available_repeats = False
                line.coverage_days = False

            # No persisted CDU-selected repeat field exists yet. Keep this blank
            # rather than inventing a selected value in the printed report.
            line.repeats_to_dispense = False

    def _get_matching_patient_picking_lines(self):
        self.ensure_one()
        patient_lines = self.batch_id.patient_picking_line_ids
        if not patient_lines:
            return patient_lines

        product = self.summary_line_id.product_id
        if product:
            product_lines = patient_lines.filtered(
                lambda patient_line: patient_line.product_id == product
            )
            if product_lines:
                return product_lines

        regimen_name = (self.openmrs_drug_name or "").strip().lower()
        if not regimen_name:
            return self.env["cdu.batch.patient.line"].browse()
        return patient_lines.filtered(
            lambda patient_line: (patient_line.drug_name or "").strip().lower() == regimen_name
        )

    def _ensure_default_fulfilment_line(self):
        for line in self:
            if line.fulfilment_line_ids:
                continue
            values = {
                "batch_id": line.batch_id.id,
                "picking_line_id": line.id,
                "quantity_picked": line.quantity_picked or line.quantity_to_pick or 1,
            }
            if line.selected_stock_option_id:
                values["selected_stock_option_id"] = line.selected_stock_option_id.id
            self.env["cdu.picking.fulfilment.line"].create(values)

    @api.onchange("selected_stock_option_id")
    def _onchange_selected_stock_option_id(self):
        for line in self:
            line._sync_selected_stock_option()

    def write(self, vals):
        result = super().write(vals)
        if "selected_stock_option_id" in vals:
            self._sync_selected_stock_option()
            self.mapped("batch_id")._sync_picking_quantities_from_elmis_pack_sizes()
        return result

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
 
