import math

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError


E_LOCKER_DISTRICT_SELECTION = [
    ("Butha-Buthe", "Butha-Buthe"),
    ("Leribe", "Leribe"),
    ("Berea", "Berea"),
    ("Maseru", "Maseru"),
    ("Mafeteng", "Mafeteng"),
    ("Mohaleshoek", "Mohaleshoek"),
    ("Quthing", "Quthing"),
    ("Qacha's Nek", "Qacha's Nek"),
    ("Thaba-Tseka", "Thaba-Tseka"),
    ("Mokhotlong", "Mokhotlong"),
]


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
    filter_e_locker_district = fields.Selection(
        E_LOCKER_DISTRICT_SELECTION,
        string="E-locker District",
    )
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
                    ("e_locker_district", "=ilike", batch.filter_e_locker_district)
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
        current_batch_id = self._origin.id

        domain = [
            ("state", "=", "awaiting_batching"),
        ]
        if current_batch_id:
            domain.extend([
                "|",
                ("batch_id", "=", False),
                ("batch_id", "=", current_batch_id),
            ])
        else:
            domain.append(("batch_id", "=", False))
        if self.filter_next_drug_pickup_date_from:
            domain.append(("next_drug_pickup_date", ">=", self.filter_next_drug_pickup_date_from))
        if self.filter_next_drug_pickup_date_to:
            domain.append(("next_drug_pickup_date", "<=", self.filter_next_drug_pickup_date_to))
        return domain

    def _ensure_batch_workflow_access(self):
        if not (
            self.env.user.has_group("cdu_prescription.group_cdu_dispensing_officer")
            or self.env.user.has_group("cdu_prescription.group_cdu_admin")
        ):
            raise AccessError(_("Only CDU dispensing officers can manage workload batches."))

    def action_confirm_batch(self):
        self._ensure_batch_workflow_access()
        for batch in self:
            if not batch.prescription_ids:
                raise ValidationError(_("Add at least one prescription before confirming the batch."))
            batch.prescription_ids.write({"state": "awaiting_picking"})
            batch.picking_line_ids.unlink()
            batch.patient_picking_line_ids.unlink()
        self.write({"state": "confirmed"})
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_mark_printed(self):
        self._ensure_batch_workflow_access()
        self.write({"state": "printed"})

    def action_mark_done(self):
        self._ensure_batch_workflow_access()
        self.write({"state": "done"})

    # def _generate_picking_lines(self):

    #     self.ensure_one()

    #     self.picking_line_ids.unlink()
    #     self.patient_picking_line_ids.unlink()

    #     summary = {}

    #     for prescription in self.prescription_ids:

    #         regimen = prescription.regimen_id

    #         if not regimen:
    #             continue

    #         cdu_days = prescription.cdu_days_supply

    #         for line in regimen.line_ids:

    #             product = line.product_id

    #             daily_dose = line.daily_dose

    #             pack_size = (
    #                 product.product_tmpl_id.cdu_pack_size
    #                 or 30
    #             )

    #             tablets_required = (
    #                 daily_dose * cdu_days
    #             )

    #             bottles_required = math.ceil(
    #                 tablets_required / pack_size
    #             )

    #             self.env[
    #                 "cdu.batch.patient.line"
    #             ].create({
    #                 "batch_id": self.id,
    #                 "prescription_id": prescription.id,
    #                 "patient_id": prescription.patient_id.id,
    #                 "product_id": product.id,
    #                 "cdu_days": cdu_days,
    #                 "daily_dose": daily_dose,
    #                 "tablets_required": tablets_required,
    #                 "bottles_required": bottles_required,
    #             })

    #             key = product.id

    #             if key not in summary:

    #                 summary[key] = {
    #                     "product_id": product.id,
    #                     "total_tablets": 0,
    #                     "total_bottles": 0,
    #                     "prescription_count": 0,
    #                 }

    #             summary[key]["total_tablets"] += tablets_required

    #             summary[key]["total_bottles"] += bottles_required

    #             summary[key]["prescription_count"] += 1

    #     for vals in summary.values():

    #         vals["batch_id"] = self.id

    #         self.env[
    #             "cdu.batch.picking.line"
    #         ].create(vals)

    # Locate the _generate_picking_lines method in cdu_batch.py and update the loop logic:

    # def _generate_picking_lines(self):
    #     self.ensure_one()

    #     self.picking_line_ids.unlink()
    #     self.patient_picking_line_ids.unlink()

    #     summary = {}

    #     for prescription in self.prescription_ids:
    #         regimen = prescription.regimen_id
    #         if not regimen:
    #             continue

    #         # Uses the newly computed CDU specific window days
    #         cdu_days = prescription.cdu_days_supply
    #         if cdu_days <= 0:
    #             continue

    #         for line in regimen.line_ids:
    #             product = line.product_id
    #             daily_dose = line.daily_dose
                
    #             # Safe fallback to 30 if pack size is 0 or False
    #             pack_size = product.product_tmpl_id.cdu_pack_size or 30

    #             # Formula: Dosage X Quantity (in drug days)
    #             tablets_required = daily_dose * cdu_days

    #             # Formula: Bottles to be dispensed (Rounded up to full pack size)
    #             bottles_required = math.ceil(tablets_required / pack_size)

    #             self.env["cdu.batch.patient.line"].create({
    #                 "batch_id": self.id,
    #                 "prescription_id": prescription.id,
    #                 "patient_id": prescription.patient_id.id,
    #                 "product_id": product.id,
    #                 "cdu_days": cdu_days,
    #                 "daily_dose": daily_dose,
    #                 "tablets_required": tablets_required,
    #                 "bottles_required": bottles_required,
    #             })

    #             key = product.id
    #             if key not in summary:
    #                 summary[key] = {
    #                     "product_id": product.id,
    #                     "total_tablets": 0,
    #                     "total_bottles": 0,
    #                     "prescription_count": 0,
    #                 }

    #             summary[key]["total_tablets"] += tablets_required
    #             summary[key]["total_bottles"] += bottles_required
    #             summary[key]["prescription_count"] += 1

    #     for vals in summary.values():
    #         vals["batch_id"] = self.id
    #         self.env["cdu.batch.picking.line"].create(vals)

    def _prepare_picking_line_values(self):
        self.ensure_one()

        summary = {}
        patient_line_vals = []

        for prescription in self.prescription_ids:

            # -------------------------------------------------
            # DAYS SUPPLY
            # -------------------------------------------------

            facility_days = (
                prescription.facility_days_supply or 0
            )

            cdu_days = (
                prescription.cdu_days_supply or 0
            )

            total_days = (
                prescription.total_days_supply or 0
            )

            # Keep operational picking computable even when CDU days are not yet present.
            # Fallback to total days so picking lines are still generated for the batch.
            operational_days = cdu_days if cdu_days > 0 else total_days
            if operational_days <= 0:
                continue

            # If there is no regimen, we create a virtual line for the raw drug name
            # to ensure it appears in the picking list.
            regimen = prescription.regimen_id
            lines = regimen.line_ids if regimen else [False]

            for line in lines:

                if line:
                    product = line.product_id
                    daily_dose = line.daily_dose or 0
                    pack_size = product.product_tmpl_id.cdu_pack_size or 30
                    drug_name = product.display_name
                else:
                    # Fallback for unmapped prescriptions
                    product = self.env['product.product'] # Empty
                    daily_dose = 1.0 # Assume 1 unit/day
                    pack_size = 30
                    drug_name = (prescription.regimen_prescribed_raw or "Unknown Drug").strip()

                if not drug_name:
                    continue

                # -------------------------------------------------
                # FACILITY SUPPLY
                # -------------------------------------------------

                facility_units_required = (
                    daily_dose * facility_days
                )

                facility_bottles_required = math.ceil(
                    facility_units_required / pack_size
                )

                # -------------------------------------------------
                # CDU SUPPLY
                # -------------------------------------------------

                cdu_units_required = (
                    daily_dose * operational_days
                )

                cdu_bottles_required = math.ceil(
                    cdu_units_required / pack_size
                )

                # -------------------------------------------------
                # TOTAL SUPPLY
                # -------------------------------------------------

                total_units_required = (
                    daily_dose * total_days
                )

                total_bottles_required = math.ceil(
                    total_units_required / pack_size
                )

                # -------------------------------------------------
                # CREATE PATIENT PICKING LINE
                # -------------------------------------------------

                patient_line_vals.append({
                    "batch_id": self.id,

                    "prescription_id": prescription.id,

                    "patient_id": prescription.patient_id.id,

                    "product_id": product.id if product else False,
                    "drug_name": drug_name,

                    # DAYS
                    "facility_days_supply": facility_days,
                    "cdu_days": cdu_days,
                    "total_days_supply": total_days,

                    # DOSING
                    "daily_dose": daily_dose,

                    # TOTAL TABLETS
                    "tablets_required": total_units_required,

                    # BOTTLES
                    "facility_bottles_required": facility_bottles_required,
                    "cdu_bottles_required": cdu_bottles_required,
                    "total_bottles_required": total_bottles_required,

                    # IMPORTANT:
                    # Operational warehouse/eLMIS picking
                    # uses CDU quantities ONLY
                    "bottles_required": cdu_bottles_required,
                })

                # -------------------------------------------------
                # BATCH SUMMARY
                # -------------------------------------------------

                key = product.id if product else drug_name

                if key not in summary:
                    summary[key] = {
                        "product_id": product.id if product else False,
                        "unmapped_drug_name": drug_name,
                        "pack_size": pack_size,
                        "total_tablets": 0,
                        "total_bottles": 0,
                        "prescription_count": 0,
                    }

                # Summary reflects CDU operational stock only
                summary[key]["total_tablets"] += cdu_units_required

                summary[key]["total_bottles"] += cdu_bottles_required

                summary[key]["prescription_count"] += 1

        return patient_line_vals, list(summary.values())

    def _generate_picking_lines(self):

        self.ensure_one()

        patient_line_vals, summary_vals = self._prepare_picking_line_values()

        # -------------------------------------------------------------------------
        # UPDATE ONE2MANY RELATIONS (Native ORM approach for UI consistency)
        # -------------------------------------------------------------------------
        self.write({
            "patient_picking_line_ids": [(5, 0, 0)] + [(0, 0, v) for v in patient_line_vals],
            "picking_line_ids": [(5, 0, 0)] + [(0, 0, v) for v in summary_vals]
        })

        # Ensure the changes are flushed to the database so eLMIS logic can read them
        self.flush_recordset(['patient_picking_line_ids', 'picking_line_ids'])

    def action_generate_picking_list(self):
        self._ensure_batch_workflow_access()

        for batch in self:

            if not batch.prescription_ids:
                continue

            batch._generate_picking_lines()

        self.write({
            "state": "picking_generated"
        })
        return {"type": "ir.actions.client", "tag": "reload"}
