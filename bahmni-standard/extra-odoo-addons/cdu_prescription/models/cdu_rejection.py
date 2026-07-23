from odoo import api, fields, models


REJECTION_REASON_SELECTION = [
    ("missing_patient_demographics", "Missing patient Demographic Information"),
    ("missing_medicine_information", "Missing Medicine information"),
    ("missing_patient_clinical", "Missing patient Clinical information"),
    ("missing_collection_information", "Missing Medicine Collection Information"),
    ("duplicate_prescription", "Duplicate Prescription"),
    ("out_of_stock", "Out of Stock"),
]

REJECTION_DESTINATION_SELECTION = [
    ("call_center", "Call Center"),
    ("facility", "Facility"),
]

REJECTION_STAGE_SELECTION = [
    ("awaiting_verification", "Verification"),
    ("awaiting_validation", "Validation"),
    ("awaiting_dispensing", "Dispensing"),
]


class CduPrescriptionRejection(models.Model):
    _name = "cdu.prescription.rejection"
    _description = "CDU Prescription Rejection History"
    _order = "rejected_at desc, id desc"

    prescription_id = fields.Many2one(
        "cdu.prescription",
        required=True,
        ondelete="cascade",
        index=True,
    )
    stage = fields.Selection(
        REJECTION_STAGE_SELECTION,
        string="Rejected From",
        required=True,
        readonly=True,
    )
    destination = fields.Selection(
        REJECTION_DESTINATION_SELECTION,
        required=True,
        readonly=True,
    )
    reason = fields.Selection(
        REJECTION_REASON_SELECTION,
        string="Legacy Rejection Reason",
        readonly=True,
    )
    reason_ids = fields.Many2many(
        "cdu.rejection.reason",
        "cdu_prescription_rejection_reason_rel",
        "rejection_id",
        "reason_id",
        string="Rejection Reasons",
        readonly=True,
    )
    reason_display = fields.Char(
        string="Rejection Reasons",
        compute="_compute_reason_display",
        store=True,
    )
    rejected_by = fields.Many2one(
        "res.users",
        required=True,
        readonly=True,
        default=lambda self: self.env.user,
    )
    rejected_at = fields.Datetime(
        required=True,
        readonly=True,
        default=fields.Datetime.now,
    )
    returned_by = fields.Many2one("res.users", readonly=True)
    returned_at = fields.Datetime(readonly=True)
    is_active = fields.Boolean(string="Current Rejection", default=True, readonly=True)

    @api.depends("reason_ids.name", "reason")
    def _compute_reason_display(self):
        legacy_labels = dict(REJECTION_REASON_SELECTION)
        for rejection in self:
            rejection.reason_display = ", ".join(rejection.reason_ids.mapped("name")) or (
                legacy_labels.get(rejection.reason) or False
            )

    @api.model
    def _migrate_legacy_rejection_reasons(self):
        legacy_rejections = self.search(
            [("reason", "!=", False), ("reason_ids", "=", False)]
        )
        reasons_by_code = {
            reason.code: reason
            for reason in self.env["cdu.rejection.reason"].search([])
        }
        for rejection in legacy_rejections:
            reason = reasons_by_code.get(rejection.reason)
            if reason:
                rejection.reason_ids = [(4, reason.id)]


class CduRejectionReason(models.Model):
    _name = "cdu.rejection.reason"
    _description = "CDU Rejection Reason"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, index=True)
    dispensing_only = fields.Boolean(default=False)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "unique_rejection_reason_code",
            "unique(code)",
            "The rejection reason code must be unique.",
        ),
    ]
