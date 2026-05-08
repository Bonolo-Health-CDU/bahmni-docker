from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
import math


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
            ("picking_generated", "Picking Generated"),
            ("printed", "Picking List Printed"),
            ("done", "Done"),
        ],
        default="draft",
        tracking=True,
    )
    filter_next_drug_pickup_date_from = fields.Date(string="Pickup Date From")
    filter_next_drug_pickup_date_to = fields.Date(string="Pickup Date To")
    filter_e_locker_district = fields.Char(string="E-locker District")
    filter_collection_point_id = fields.Many2one(
        "cdu.collection.point",
        string="Collection Location",
    )
    prescription_ids = fields.One2many(
        "cdu.prescription",
        "batch_id",
        string="Prescriptions in Batch",
    )
    prescription_count = fields.Integer(
        compute="_compute_prescription_count",
        string="Prescription Count",
    )

    picking_line_ids = fields.One2many(
    "cdu.batch.picking.line",
    "batch_id",
    string="Picking Summary",
    )

    patient_picking_line_ids = fields.One2many(
        "cdu.batch.patient.line",
        "batch_id",
        string="Patient Picking Lines",
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
        "filter_next_drug_pickup_date_from",
        "filter_next_drug_pickup_date_to",
        "filter_e_locker_district",
        "filter_collection_point_id",
    )
    def _onchange_filters_fetch_prescriptions(self):
        for batch in self:
            if batch.state != "draft":
                continue
            domain = batch._get_prescription_domain()
            if batch.filter_collection_point_id:
                domain.append(
                    ("collection_point_id", "=", batch.filter_collection_point_id.id)
                )
            if batch.filter_e_locker_district:
                domain.append(
                    ("e_locker_district", "=ilike", batch.filter_e_locker_district.strip())
                )
            prescriptions = self.env["cdu.prescription"].search(
                domain, order="facility_name, collection_point_id, prescription_date, id"
            )
            batch.prescription_ids = [(6, 0, prescriptions.ids)]

    @api.constrains(
        "filter_next_drug_pickup_date_from",
        "filter_next_drug_pickup_date_to",
    )
    def _check_pickup_date_filters(self):
        for batch in self:
            if (
                batch.filter_next_drug_pickup_date_from
                and batch.filter_next_drug_pickup_date_to
                and batch.filter_next_drug_pickup_date_from > batch.filter_next_drug_pickup_date_to
            ):
                raise ValidationError(_("Pickup Date From cannot be after Pickup Date To."))

    def _get_prescription_domain(self):
        self.ensure_one()
        domain = [
            ("state", "=", "awaiting_batching"),
            ("batch_id", "=", False),
        ]
        if self.filter_next_drug_pickup_date_from:
            domain.append(("next_drug_pickup_date", ">=", self.filter_next_drug_pickup_date_from))
        if self.filter_next_drug_pickup_date_to:
            domain.append(("next_drug_pickup_date", "<=", self.filter_next_drug_pickup_date_to))
        return domain

    def action_confirm_batch(self):
        for batch in self:
            if not batch.prescription_ids:
                raise ValidationError(_("Add at least one prescription before confirming the batch."))
            batch.prescription_ids.write({"state": "awaiting_picking"})
        self.write({"state": "confirmed"})

    def action_mark_printed(self):
        self.write({"state": "printed"})

    def action_mark_done(self):
        self.write({"state": "done"})

    def _generate_picking_lines(self):

        self.ensure_one()

        self.picking_line_ids.unlink()
        self.patient_picking_line_ids.unlink()

        summary = {}

        for prescription in self.prescription_ids:

            regimen = prescription.regimen_id

            if not regimen:
                continue

            cdu_days = prescription.cdu_days_supply

            for line in regimen.line_ids:

                product = line.product_id

                daily_dose = line.daily_dose

                pack_size = (
                    product.product_tmpl_id.cdu_pack_size
                    or 30
                )

                tablets_required = (
                    daily_dose * cdu_days
                )

                bottles_required = math.ceil(
                    tablets_required / pack_size
                )

                self.env[
                    "cdu.batch.patient.line"
                ].create({
                    "batch_id": self.id,
                    "prescription_id": prescription.id,
                    "patient_id": prescription.patient_id.id,
                    "product_id": product.id,
                    "cdu_days": cdu_days,
                    "daily_dose": daily_dose,
                    "tablets_required": tablets_required,
                    "bottles_required": bottles_required,
                })

                key = product.id

                if key not in summary:

                    summary[key] = {
                        "product_id": product.id,
                        "total_tablets": 0,
                        "total_bottles": 0,
                        "prescription_count": 0,
                    }

                summary[key]["total_tablets"] += tablets_required

                summary[key]["total_bottles"] += bottles_required

                summary[key]["prescription_count"] += 1

        for vals in summary.values():

            vals["batch_id"] = self.id

            self.env[
                "cdu.batch.picking.line"
            ].create(vals)

    def action_generate_picking_list(self):

        for batch in self:

            if not batch.prescription_ids:
                continue

            batch._generate_picking_lines()

        self.write({
            "state": "picking_generated"
        })