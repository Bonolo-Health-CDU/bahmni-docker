from odoo import _, api, fields, models

from .fhir_contract import REJECTION_REASON_CODES, STATE_TO_TASK_STATUS


class CduPrescription(models.Model):
    _inherit = "cdu.prescription"

    intake_source = fields.Selection(
        [
            ("report", "ART-094 report"),
            ("eregister_fhir", "eRegister (FHIR)"),
        ],
        string="Intake Source",
        default="report",
        required=True,
        readonly=True,
        index=True,
    )
    eregister_order_uuid = fields.Char(
        string="eRegister Order UUID",
        readonly=True,
        copy=False,
        index=True,
        help="Identifier of the prescription in eRegister; also the fulfilment Task identifier.",
    )
    fhir_task_id = fields.Char(string="FHIR Task ID", readonly=True, copy=False)
    fhir_medication_request_id = fields.Char(string="FHIR MedicationRequest ID", readonly=True, copy=False)
    fhir_patient_id = fields.Char(string="FHIR Patient ID", readonly=True, copy=False)
    fhir_task_status = fields.Char(string="Published Task Status", readonly=True, copy=False)
    fhir_business_status = fields.Char(string="Published Fulfilment Status", readonly=True, copy=False)
    fhir_published_signature = fields.Char(readonly=True, copy=False)
    fhir_sync_state = fields.Selection(
        [
            ("not_applicable", "Not applicable"),
            ("pending", "Pending"),
            ("synced", "Published"),
            ("failed", "Failed"),
        ],
        string="Status Publication",
        default="not_applicable",
        required=True,
        readonly=True,
        copy=False,
        index=True,
    )
    fhir_sync_attempts = fields.Integer(string="Publication Attempts", readonly=True, copy=False)
    fhir_sync_error = fields.Text(string="Publication Error", readonly=True, copy=False)
    fhir_synced_at = fields.Datetime(string="Last Published At", readonly=True, copy=False)
    eregister_cancelled = fields.Boolean(
        string="Cancelled in eRegister",
        readonly=True,
        copy=False,
        tracking=True,
    )
    eregister_cancelled_at = fields.Datetime(readonly=True, copy=False)
    eregister_api_log_ids = fields.One2many(
        "cdu.eregister.api.log",
        "prescription_id",
        string="eRegister API Calls",
        readonly=True,
    )

    _sql_constraints = [
        (
            "unique_eregister_order_uuid",
            "unique(eregister_order_uuid)",
            "This eRegister order has already been received.",
        ),
    ]

    def write(self, vals):
        result = super().write(vals)
        if {"state", "active_rejection_id"}.intersection(vals):
            self._queue_fhir_status_publication()
        return result

    # ------------------------------------------------------------------
    # Status publication
    # ------------------------------------------------------------------
    def _fhir_desired_status(self):
        """What the fulfilment Task should say for the current CDU state.

        Returns (task_status, business_status_code, reason_codes, reason_text)
        or None when nothing should be published.
        """
        self.ensure_one()
        mapped = STATE_TO_TASK_STATUS.get(self.state)
        if not mapped:
            return None
        task_status, business_status = mapped
        reason_codes = []
        reason_text = False
        if self.state in ("rejected_to_call_center", "rejected_to_facility") and self.active_rejection_id:
            reasons = self.active_rejection_id.reason_ids
            reason_codes = [
                REJECTION_REASON_CODES[reason.code]
                for reason in reasons
                if reason.code in REJECTION_REASON_CODES
            ]
            reason_text = self.active_rejection_id.reason_display or False
        return task_status, business_status, reason_codes, reason_text

    @api.model
    def _fhir_signature(self, desired):
        task_status, business_status, reason_codes, _reason_text = desired
        return "|".join([task_status, business_status, ",".join(reason_codes)])

    def _fhir_cdu_published_cancellation(self):
        """True when the Task's 'cancelled' status was published by the CDU.

        eRegister cancels with the same Task.status and businessStatus, so the
        only way to tell the two apart is what this record last published.
        """
        self.ensure_one()
        return (self.fhir_published_signature or "").startswith("cancelled|")

    def _queue_fhir_status_publication(self):
        for prescription in self.filtered(
            lambda record: record.intake_source == "eregister_fhir"
            and record.fhir_task_id
            and not record.eregister_cancelled
        ):
            desired = prescription._fhir_desired_status()
            if not desired:
                continue
            if prescription._fhir_signature(desired) == prescription.fhir_published_signature:
                continue
            prescription.write(
                {
                    "fhir_sync_state": "pending",
                    "fhir_sync_attempts": 0,
                    "fhir_sync_error": False,
                }
            )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_push_eregister_status(self):
        self.ensure_one()
        self._ensure_cdu_groups("cdu_prescription.group_cdu_admin")
        # Publish even when the last attempt already succeeded or gave up.
        self.sudo().write({"fhir_sync_attempts": 0})
        ok, message = self.env["cdu.eregister.sync"].push_prescription_status(self)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("eRegister status"),
                "message": message,
                "type": "success" if ok else "warning",
                "sticky": not ok,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def action_view_eregister_api_logs(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("cdu_eregister.action_cdu_eregister_api_log")
        action["domain"] = [("prescription_id", "=", self.id)]
        action["context"] = {}
        action["name"] = _("eRegister API Calls")
        return action
