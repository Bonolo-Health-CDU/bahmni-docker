from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

# Settings field -> the scheduled job whose interval it controls.
SYNC_SCHEDULES = {
    "cdu_eregister_pull_interval": "cdu_eregister.ir_cron_cdu_eregister_pull",
    "cdu_eregister_push_interval": "cdu_eregister.ir_cron_cdu_eregister_push_status",
    "cdu_eregister_cancellation_interval": "cdu_eregister.ir_cron_cdu_eregister_cancellations",
}
MAX_INTERVAL_MINUTES = 24 * 60
MINUTES_PER_UNIT = {"minutes": 1, "hours": 60, "days": 1440, "weeks": 10080, "months": 43200}


def cron_interval_minutes(cron):
    """A scheduled job's interval in minutes, whatever unit it is stored in."""
    return cron.interval_number * MINUTES_PER_UNIT.get(cron.interval_type, 1)


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
    # Stored on the scheduled jobs themselves (ir.cron), not as parameters, so
    # there is one source of truth; the jobs are noupdate, so module upgrades
    # keep what is set here.
    cdu_eregister_pull_interval = fields.Integer(
        string="Pull New Prescriptions Every (minutes)",
        help="How often the CDU checks the repository for new prescriptions from eRegister.",
    )
    cdu_eregister_push_interval = fields.Integer(
        string="Publish Status Every (minutes)",
        help="How often pending CDU status changes are published to eRegister.",
    )
    cdu_eregister_cancellation_interval = fields.Integer(
        string="Check Cancellations Every (minutes)",
        help="How often the CDU looks for prescriptions cancelled in eRegister.",
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

    @api.model
    def get_values(self):
        values = super().get_values()
        for field_name, xml_id in SYNC_SCHEDULES.items():
            cron = self.env.ref(xml_id, raise_if_not_found=False)
            values[field_name] = cron_interval_minutes(cron.sudo()) if cron else 0
        return values

    def set_values(self):
        super().set_values()
        now = fields.Datetime.now()
        for field_name, xml_id in SYNC_SCHEDULES.items():
            cron = self.env.ref(xml_id, raise_if_not_found=False)
            if not cron:
                continue
            cron = cron.sudo()
            minutes = self[field_name]
            if not 1 <= minutes <= MAX_INTERVAL_MINUTES:
                raise ValidationError(
                    _("%(field)s must be between 1 and %(max)s minutes.")
                    % {"field": self._fields[field_name].string, "max": MAX_INTERVAL_MINUTES}
                )
            if minutes == cron_interval_minutes(cron):
                continue
            values = {"interval_number": minutes, "interval_type": "minutes"}
            # A shorter interval applies now, not after the old, longer wait.
            sooner = now + timedelta(minutes=minutes)
            if cron.nextcall and cron.nextcall > sooner:
                values["nextcall"] = sooner
            cron.write(values)

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
        summary = self.env["cdu.eregister.sync"].run_pull("manual")
        if summary.get("busy"):
            return self._cdu_eregister_notification(_("eRegister pull"), _("A pull is already running."), ok=False)
        if not summary["ok"]:
            return self._cdu_eregister_notification(_("eRegister pull failed"), summary["error"], ok=False)
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
