from odoo import _, api, fields, models
from odoo.exceptions import AccessError


PRESCRIPTION_STATES = [
    ("awaiting_verification", "Awaiting verification"),
    ("awaiting_validation", "Awaiting validation"),
    ("rejected_to_call_center", "Rejected to call center"),
    ("rejected_to_facility", "Rejected to facility"),
    ("awaiting_batching", "Awaiting batching"),
    ("awaiting_picking", "Awaiting picking"),
    ("awaiting_dispensing", "Awaiting dispensing"),
    ("awaiting_bagging_qa", "Awaiting bagging / QA"),
    ("awaiting_boxing", "Awaiting boxing"),
    ("awaiting_dispatch", "Awaiting dispatch"),
    ("dispatched", "Dispatched"),
    ("cancelled", "Cancelled"),
]


class CduPrescriptionStatusHistory(models.Model):
    _name = "cdu.prescription.status.history"
    _description = "CDU Prescription Status History"
    _order = "changed_at desc, id desc"
    _rec_name = "prescription_id"

    prescription_id = fields.Many2one(
        "cdu.prescription",
        required=True,
        ondelete="cascade",
        index=True,
    )
    previous_status = fields.Selection(PRESCRIPTION_STATES, readonly=True, index=True)
    new_status = fields.Selection(PRESCRIPTION_STATES, required=True, readonly=True, index=True)
    changed_at = fields.Datetime(required=True, readonly=True, index=True)
    changed_by = fields.Many2one("res.users", required=True, readonly=True, index=True)
    reason = fields.Char(readonly=True)
    remarks = fields.Text(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get("cdu_reporting_history_internal"):
            raise AccessError(_("Prescription lifecycle history is system-managed and immutable."))
        return super().create(vals_list)

    def write(self, vals):
        raise AccessError(_("Prescription lifecycle history cannot be modified."))

    def unlink(self):
        if self.env.context.get("module_uninstall"):
            return super().unlink()
        raise AccessError(_("Prescription lifecycle history cannot be deleted."))


class CduPrescription(models.Model):
    _inherit = "cdu.prescription"

    status_history_ids = fields.One2many(
        "cdu.prescription.status.history",
        "prescription_id",
        string="Lifecycle History",
        readonly=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        prescriptions = super().create(vals_list)
        if not self.env.context.get("cdu_reporting_skip_history"):
            now = fields.Datetime.now()
            history_values = [
                {
                    "prescription_id": prescription.id,
                    "new_status": prescription.state,
                    "changed_at": prescription.create_date or now,
                    "changed_by": prescription.create_uid.id or self.env.user.id,
                    "reason": _("Prescription created"),
                }
                for prescription in prescriptions
            ]
            self.env["cdu.prescription.status.history"].with_context(
                cdu_reporting_history_internal=True
            ).create(history_values)
        return prescriptions

    def write(self, vals):
        target_state = vals.get("state")
        previous_states = {record.id: record.state for record in self} if target_state else {}
        result = super().write(vals)
        if target_state and not self.env.context.get("cdu_reporting_skip_history"):
            history_values = []
            for prescription in self:
                previous = previous_states.get(prescription.id)
                if previous == prescription.state:
                    continue
                rejection = prescription.active_rejection_id
                timestamp = fields.Datetime.now()
                if prescription.state == "awaiting_validation" and vals.get("verified_at"):
                    timestamp = vals["verified_at"]
                elif prescription.state == "awaiting_batching" and vals.get("validated_at"):
                    timestamp = vals["validated_at"]
                history_values.append(
                    {
                        "prescription_id": prescription.id,
                        "previous_status": previous,
                        "new_status": prescription.state,
                        "changed_at": timestamp,
                        "changed_by": self.env.user.id,
                        "reason": rejection.reason_display if rejection else False,
                        "remarks": prescription.validation_notes or False,
                    }
                )
            if history_values:
                self.env["cdu.prescription.status.history"].with_context(
                    cdu_reporting_history_internal=True
                ).create(history_values)
        return result

