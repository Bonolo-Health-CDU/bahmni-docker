from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    cdu_eregister_id = fields.Char(string="eRegister ID", index=True, copy=False)
    cdu_hiv_program_id = fields.Char(string="HIV Program ID", copy=False)
    cdu_national_id = fields.Char(string="National ID", copy=False)
    cdu_gender = fields.Selection(
        [("male", "Male"), ("female", "Female"), ("other", "Other")],
        string="Gender",
    )
    cdu_date_of_birth = fields.Date(string="Date of Birth")
    cdu_secondary_contact = fields.Char(string="Secondary Contact")
    cdu_source_facility_id = fields.Many2one("cdu.facility", string="Source Facility")
    cdu_last_report_run_id = fields.Many2one("cdu.report.run", string="Last Report Run")
    cdu_last_report_file_name = fields.Char(
        string="Last Report File",
        related="cdu_last_report_run_id.source_file_name",
        readonly=True,
    )
    cdu_last_report_generated_at = fields.Datetime(
        string="Last Report Generated At",
        related="cdu_last_report_run_id.report_generated_at",
        readonly=True,
    )
    cdu_last_ingested_at = fields.Datetime(
        string="Last Ingested At",
        related="cdu_last_report_run_id.ingested_at",
        readonly=True,
    )
    cdu_prescription_ids = fields.One2many(
        "cdu.prescription",
        "patient_id",
        string="Prescription History",
    )
    cdu_prescription_count = fields.Integer(
        string="Prescription Count",
        compute="_compute_cdu_prescription_count",
    )

    _sql_constraints = [
        ("unique_cdu_eregister_id", "unique(cdu_eregister_id)", "eRegister ID must be unique."),
    ]

    @api.depends("cdu_prescription_ids")
    def _compute_cdu_prescription_count(self):
        for patient in self:
            patient.cdu_prescription_count = len(patient.cdu_prescription_ids)

    def action_view_cdu_prescriptions(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "cdu_prescription.action_cdu_prescription"
        )
        action["domain"] = [("patient_id", "=", self.id)]
        action["context"] = {"default_patient_id": self.id}
        return action
