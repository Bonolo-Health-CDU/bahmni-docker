from odoo import _, api, fields, models


class CduLabelPrintWizard(models.TransientModel):
    _name = "cdu.label.print.wizard"
    _description = "Preview and Print CDU Labels"

    dispense_id = fields.Many2one(
        "cdu.dispense",
        required=True,
        readonly=True,
        ondelete="cascade",
    )
    label_layout = fields.Selection(
        [
            ("all", "Medicine and bag labels"),
            ("medicine", "Medicine labels only"),
            ("bag", "Bag label only"),
        ],
        string="Print Layout",
        required=True,
        default="all",
    )
    patient_name = fields.Char(related="dispense_id.patient_name", readonly=True)
    patient_identifier = fields.Char(related="dispense_id.patient_identifier", readonly=True)
    patient_phone = fields.Char(related="dispense_id.patient_phone", readonly=True)
    prescription_id = fields.Many2one(
        "cdu.prescription",
        related="dispense_id.prescription_id",
        readonly=True,
    )
    collection_point_id = fields.Many2one(
        "cdu.collection.point",
        related="dispense_id.collection_point_id",
        readonly=True,
    )
    next_drug_pickup_date = fields.Date(
        related="dispense_id.next_drug_pickup_date",
        readonly=True,
    )
    stock_selection_ids = fields.One2many(
        "cdu.dispense.stock.selection",
        related="dispense_id.stock_selection_ids",
        readonly=True,
    )
    medicine_label_count = fields.Integer(compute="_compute_label_counts")
    bag_label_count = fields.Integer(compute="_compute_label_counts")
    total_label_count = fields.Integer(compute="_compute_label_counts")

    @api.depends("dispense_id.stock_selection_ids", "label_layout")
    def _compute_label_counts(self):
        for wizard in self:
            medicine_count = len(wizard.dispense_id.stock_selection_ids)
            wizard.medicine_label_count = (
                medicine_count if wizard.label_layout in ("all", "medicine") else 0
            )
            wizard.bag_label_count = 1 if wizard.label_layout in ("all", "bag") else 0
            wizard.total_label_count = wizard.medicine_label_count + wizard.bag_label_count

    def action_print_labels(self):
        self.ensure_one()
        return self.dispense_id.with_context(
            cdu_skip_label_print_wizard=True,
            cdu_label_layout=self.label_layout,
        ).action_print_labels()
