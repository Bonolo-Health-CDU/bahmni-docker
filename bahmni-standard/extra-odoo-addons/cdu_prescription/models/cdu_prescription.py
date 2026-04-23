from odoo import api, fields, models
from odoo.exceptions import ValidationError


class CduPrescription(models.Model):
    _name = "cdu.prescription"
    _description = "CDU Prescription"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "refill_date, create_date desc"

    name = fields.Char(
        default="/",
        copy=False,
        readonly=True,
        tracking=True,
    )
    source_document_ref = fields.Char(string="Source Document Reference")
    barcode = fields.Char(string="Prescription Barcode", copy=False, tracking=True)
    facility_name = fields.Char(required=True, tracking=True)
    prescription_date = fields.Date(required=True, tracking=True)
    patient_id = fields.Many2one(
        "res.partner",
        string="Patient",
        domain=[("customer_rank", ">", 0)],
        tracking=True,
    )
    patient_identifier = fields.Char(string="Patient ID", tracking=True)
    patient_surname = fields.Char()
    patient_first_name = fields.Char()
    patient_date_of_birth = fields.Date()
    patient_gender = fields.Selection(
        [("male", "Male"), ("female", "Female"), ("other", "Other")]
    )
    patient_phone = fields.Char()
    patient_address = fields.Text()
    diagnosis_asthma_copd = fields.Boolean(string="Asthma/COPD")
    diagnosis_diabetes_type_2 = fields.Boolean(string="Diabetes Mellitus - Type 2")
    diagnosis_family_planning = fields.Boolean(string="Family Planning")
    diagnosis_hypertension = fields.Boolean(string="Hypertension")
    diagnosis_hiv = fields.Boolean(string="HIV")
    diagnosis_arthritis = fields.Boolean(string="Arthritis")
    viral_load_date_taken = fields.Date(string="Viral Load Date Taken")
    cd4_count_date_taken = fields.Date(string="CD4 Count Date Taken")
    program_id = fields.Many2one("cdu.program", tracking=True)
    repeat_count = fields.Integer(string="Repeat Count", tracking=True)
    refill_date = fields.Date(string="Refill / Next Collection Date", tracking=True)
    collection_point_id = fields.Many2one(
        "cdu.collection.point",
        required=True,
        tracking=True,
    )
    nominated_person_name = fields.Char()
    nominated_person_id_number = fields.Char()
    nominated_person_relationship = fields.Char()
    nominated_person_phone = fields.Char()
    prescriber_name = fields.Char()
    prescriber_contact = fields.Char()
    prescriber_registration_number = fields.Char()
    clinical_review_only = fields.Boolean(tracking=True)
    new_patient = fields.Boolean(tracking=True)
    repeat_patient = fields.Boolean(tracking=True)
    next_clinical_visit_date = fields.Date()
    line_ids = fields.One2many(
        "cdu.prescription.line",
        "prescription_id",
        string="Medicines",
    )
    state = fields.Selection(
        [
            ("draft_capture", "Draft Capture"),
            ("captured", "Captured"),
            ("validation_failed", "Validation Failed"),
            ("validated", "Validated"),
            ("on_hold", "On Hold"),
            ("cancelled", "Cancelled"),
        ],
        default="draft_capture",
        required=True,
        tracking=True,
    )
    validation_notes = fields.Text(tracking=True)

    _sql_constraints = [
        ("unique_barcode", "unique(barcode)", "Prescription barcode must be unique."),
    ]

    @api.model
    def create(self, vals):
        if vals.get("name", "/") == "/":
            vals["name"] = self.env["ir.sequence"].next_by_code("cdu.prescription") or "/"
        return super().create(vals)

    def action_mark_captured(self):
        self.write({"state": "captured"})

    def action_validate_prescription(self):
        for prescription in self:
            validation_errors = prescription._get_validation_errors()
            if validation_errors:
                prescription.write({
                    "state": "validation_failed",
                    "validation_notes": "Missing or invalid: %s" % ", ".join(validation_errors),
                })
                continue
            prescription.state = "validated"
            prescription.validation_notes = False

    def action_put_on_hold(self):
        self.write({"state": "on_hold"})

    def action_cancel(self):
        self.write({"state": "cancelled"})

    def _get_validation_errors(self):
        self.ensure_one()
        missing = []
        if not self.patient_id and not self.patient_identifier:
            missing.append("patient")
        if not self.collection_point_id:
            missing.append("collection point")
        if not self.clinical_review_only and not self.line_ids:
            missing.append("at least one medicine line")
        if self.repeat_count < 0:
            missing.append("valid repeat count")
        return missing


class CduPrescriptionLine(models.Model):
    _name = "cdu.prescription.line"
    _description = "CDU Prescription Line"
    _order = "sequence, id"

    prescription_id = fields.Many2one(
        "cdu.prescription",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one("product.product", string="Medicine")
    medicine_text = fields.Char(
        string="Medicine as Written",
        help="Raw medicine text captured from the prescription form.",
    )
    directions = fields.Char(string="Directions / Abbreviation")
    one_time_use_only = fields.Boolean()
    form = fields.Char()
    strength = fields.Char()
    quantity = fields.Float(required=True, default=1.0)
    product_uom_id = fields.Many2one("uom.uom", string="Unit of Measure")
    initial_issue = fields.Boolean()
    refill_number = fields.Integer()
    lot_id = fields.Many2one("stock.lot", string="Batch / Lot")
    expiry_date = fields.Datetime(related="lot_id.expiration_date", store=True)

    @api.constrains("quantity", "refill_number")
    def _check_positive_values(self):
        for line in self:
            if line.quantity <= 0:
                raise ValidationError("Medicine quantity must be greater than zero.")
            if line.refill_number < 0:
                raise ValidationError("Refill number cannot be negative.")
