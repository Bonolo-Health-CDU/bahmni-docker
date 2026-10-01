from odoo import _, fields, models
from odoo.exceptions import UserError


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    cdu_eregister_base_url = fields.Char(
        string="FHIR Base URL (OpenHIM)",
        config_parameter="cdu.eregister.base_url",
        help="OpenHIM router address of the prescription repository, e.g. http://host.docker.internal:5001/fhir",
    )
    cdu_eregister_username = fields.Char(
        string="OpenHIM Client ID",
        config_parameter="cdu.eregister.username",
        groups="cdu_prescription.group_cdu_admin",
    )
    cdu_eregister_password = fields.Char(
        string="OpenHIM Client Password",
        config_parameter="cdu.eregister.password",
        groups="cdu_prescription.group_cdu_admin",
    )
    cdu_eregister_cdu_organization_id = fields.Char(
        string="CDU Organization ID",
        config_parameter="cdu.eregister.cdu_organization_id",
        help="Id of the CDU Organization in the repository. Tasks owned by it are pulled.",
    )
    cdu_eregister_sync_enabled = fields.Boolean(
        string="Automatic Synchronisation",
        config_parameter="cdu.eregister.sync_enabled",
        help="Pull new prescriptions, publish status and check cancellations on a schedule.",
    )
    cdu_eregister_timeout_seconds = fields.Integer(
        string="Request Timeout (seconds)",
        config_parameter="cdu.eregister.timeout_seconds",
        default=30,
    )
    cdu_eregister_page_size = fields.Integer(
        string="Search Page Size",
        config_parameter="cdu.eregister.page_size",
        default=50,
    )
    cdu_eregister_max_publish_attempts = fields.Integer(
        string="Max Automatic Publication Attempts",
        config_parameter="cdu.eregister.max_publish_attempts",
        default=10,
    )

    def _cdu_eregister_notification(self, title, message, ok=True):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": title, "message": message, "type": "success" if ok else "danger", "sticky": not ok},
        }

    def action_test_eregister_connection(self):
        self.set_values()
        client = self.env["cdu.eregister.client"]
        try:
            client.get("metadata", params={"_summary": "true"}, call_type="TEST_CONNECTION", log_success=True)
            queue = client.get(
                "Task",
                params={
                    "owner": "Organization/%s" % client._cdu_organization_id(),
                    "status": "requested",
                    "_summary": "count",
                },
                call_type="TEST_CONNECTION",
                log_success=True,
            )
        except UserError as exc:
            return self._cdu_eregister_notification(_("eRegister connection failed"), str(exc), ok=False)
        return self._cdu_eregister_notification(
            _("eRegister connection OK"),
            _("Connected through OpenHIM. %s prescription(s) waiting for the CDU.") % queue.get("total", 0),
        )

    def action_pull_eregister_now(self):
        self.set_values()
        try:
            summary = self.env["cdu.eregister.sync"].pull_new_prescriptions(commit=True)
        except UserError as exc:
            return self._cdu_eregister_notification(_("eRegister pull failed"), str(exc), ok=False)
        return self._cdu_eregister_notification(
            _("eRegister pull finished"),
            _("New: %(created)s, re-sent: %(updated)s, failed: %(failed)s.") % summary,
            ok=not summary["failed"],
        )

    def action_check_eregister_cancellations_now(self):
        self.set_values()
        try:
            count = self.env["cdu.eregister.sync"].check_cancellations(commit=True)
        except UserError as exc:
            return self._cdu_eregister_notification(_("Cancellation check failed"), str(exc), ok=False)
        return self._cdu_eregister_notification(
            _("Cancellation check finished"),
            _("%s prescription(s) newly cancelled in eRegister.") % count,
        )
