from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError


class CduProductionSkip(models.Model):
    _name = "cdu.production.skip"
    _description = "CDU Production Skip History"
    _order = "skipped_at desc, id desc"

    prescription_id = fields.Many2one(
        "cdu.prescription",
        required=True,
        ondelete="cascade",
        index=True,
    )
    batch_id = fields.Many2one(
        "cdu.batch",
        related="prescription_id.batch_id",
        store=True,
        readonly=True,
    )
    stage = fields.Selection(
        [
            ("dispensing", "Dispensing"),
            ("bagging_qa", "Bagging / QA"),
            ("boxing", "Boxing"),
        ],
        required=True,
        index=True,
    )
    reason = fields.Text(required=True)
    skipped_by = fields.Many2one(
        "res.users",
        required=True,
        readonly=True,
        default=lambda self: self.env.user,
    )
    skipped_at = fields.Datetime(
        required=True,
        readonly=True,
        default=fields.Datetime.now,
    )
    resolved_by = fields.Many2one("res.users", readonly=True)
    resolved_at = fields.Datetime(readonly=True)
    active = fields.Boolean(default=True, readonly=True, index=True)


class CduProductionSkipWizard(models.TransientModel):
    _name = "cdu.production.skip.wizard"
    _description = "Skip CDU Production Item for Now"

    prescription_id = fields.Many2one(
        "cdu.prescription",
        required=True,
        readonly=True,
    )
    stage = fields.Selection(
        [
            ("dispensing", "Dispensing"),
            ("bagging_qa", "Bagging / QA"),
            ("boxing", "Boxing"),
        ],
        required=True,
        readonly=True,
    )
    reason = fields.Text(
        string="Reason",
        required=True,
        help="Explain why this item cannot be completed now.",
    )

    def _ensure_access(self):
        if not (
            self.env.user.has_group("cdu_prescription.group_cdu_dispensing_officer")
            or self.env.user.has_group("cdu_prescription.group_cdu_admin")
        ):
            raise AccessError(_("Only CDU dispensing officers can skip production items."))

    def action_confirm_skip(self):
        self.ensure_one()
        self._ensure_access()
        reason = (self.reason or "").strip()
        if not reason:
            raise UserError(_("A reason is required before this item can be skipped."))

        self.prescription_id._defer_production_stage(self.stage, reason)
        return self.prescription_id._get_action_after_production_skip(self.stage)
