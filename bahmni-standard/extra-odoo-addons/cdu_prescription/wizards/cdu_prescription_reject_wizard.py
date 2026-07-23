from odoo import _, fields, models
from odoo.exceptions import ValidationError

from ..models.cdu_rejection import REJECTION_DESTINATION_SELECTION, REJECTION_STAGE_SELECTION


class CduPrescriptionRejectWizard(models.TransientModel):
    _name = "cdu.prescription.reject.wizard"
    _description = "Reject CDU Prescription"

    prescription_id = fields.Many2one(
        "cdu.prescription",
        required=True,
        readonly=True,
    )
    stage = fields.Selection(
        REJECTION_STAGE_SELECTION,
        required=True,
        readonly=True,
    )
    destination = fields.Selection(
        REJECTION_DESTINATION_SELECTION,
        required=True,
        readonly=True,
    )
    standard_reason_ids = fields.Many2many(
        "cdu.rejection.reason",
        "cdu_reject_wizard_standard_reason_rel",
        "wizard_id",
        "reason_id",
        string="Rejection Reasons",
    )
    dispensing_reason_ids = fields.Many2many(
        "cdu.rejection.reason",
        "cdu_reject_wizard_dispensing_reason_rel",
        "wizard_id",
        "reason_id",
        string="Rejection Reasons",
    )

    def action_confirm_rejection(self):
        self.ensure_one()
        reasons = (
            self.dispensing_reason_ids
            if self.stage == "awaiting_dispensing"
            else self.standard_reason_ids
        )
        if not reasons:
            raise ValidationError(_("Select at least one rejection reason before continuing."))
        if self.stage != "awaiting_dispensing" and any(reasons.mapped("dispensing_only")):
            raise ValidationError(_("Out of Stock is only available during Dispensing."))
        if self.prescription_id.state != self.stage:
            raise ValidationError(
                _("The prescription stage changed before the rejection was confirmed.")
            )
        return self.prescription_id._perform_rejection(
            self.destination,
            reasons.mapped("code"),
        )
