from odoo import api, fields, models


class CduBatch(models.Model):
    _name = "cdu.batch"
    _description = "CDU Workload Batch"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"

    name = fields.Char(
        string="Batch Reference",
        required=True,
        copy=False,
        readonly=True,
        default="/",
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("confirmed", "Confirmed"),
            ("printed", "Picking List Printed"),
            ("done", "Done"),
        ],
        default="draft",
        tracking=True,
    )
    filter_next_drug_pickup_date = fields.Date(string="Next Drug Pickup Date")
    filter_collection_point_id = fields.Many2one(
        "cdu.collection.point",
        string="Collection Point",
    )
    filter_facility_id = fields.Many2one("cdu.facility", string="Source Facility")
    filter_hiv_program_id = fields.Char(string="HIV Program ID")
    prescription_ids = fields.One2many(
        "cdu.prescription",
        "batch_id",
        string="Prescriptions in Batch",
    )
    prescription_count = fields.Integer(
        compute="_compute_prescription_count",
        string="Prescription Count",
    )

    @api.depends("prescription_ids")
    def _compute_prescription_count(self):
        for batch in self:
            batch.prescription_count = len(batch.prescription_ids)

    @api.model
    def create(self, vals):
        if vals.get("name", "/") == "/":
            vals["name"] = self.env["ir.sequence"].next_by_code("cdu.batch") or "/"
        return super().create(vals)

    @api.onchange(
        "filter_next_drug_pickup_date",
        "filter_collection_point_id",
        "filter_facility_id",
        "filter_hiv_program_id",
    )
    def _onchange_filters_fetch_prescriptions(self):
        for batch in self:
            if batch.state != "draft":
                continue
            domain = [
                ("state", "=", "awaiting_batching"),
                ("batch_id", "=", False),
            ]
            if batch.filter_next_drug_pickup_date:
                domain.append(
                    ("next_drug_pickup_date", "=", batch.filter_next_drug_pickup_date)
                )
            if batch.filter_collection_point_id:
                domain.append(
                    ("collection_point_id", "=", batch.filter_collection_point_id.id)
                )
            if batch.filter_facility_id:
                domain.append(("facility_id", "=", batch.filter_facility_id.id))
            if batch.filter_hiv_program_id:
                domain.append(
                    ("hiv_program_id", "=ilike", batch.filter_hiv_program_id.strip())
                )
            prescriptions = self.env["cdu.prescription"].search(
                domain, order="facility_name, collection_point_id, prescription_date, id"
            )
            batch.prescription_ids = [(6, 0, prescriptions.ids)]

    def action_confirm_batch(self):
        self.write({"state": "confirmed"})

    def action_mark_printed(self):
        self.write({"state": "printed"})

    def action_mark_done(self):
        self.write({"state": "done"})
