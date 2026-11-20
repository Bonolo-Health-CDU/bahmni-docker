from odoo import _, api, fields, models
from odoo.exceptions import UserError


class BahmniPrintJob(models.Model):
    _name = "bahmni.print.job"
    _description = "Bahmni Print Job"
    _order = "create_date desc, id desc"

    name = fields.Char(default="/", copy=False, readonly=True, index=True)
    label_type = fields.Char(required=True, index=True)
    printer_key = fields.Char(required=True, index=True)
    command_language = fields.Selection(
        [
            ("tspl", "TSPL2"),
            ("zpl", "ZPL II"),
            ("pdf", "PDF"),
        ],
        required=True,
        index=True,
    )
    payload = fields.Text(required=True)
    payload_encoding = fields.Selection(
        [
            ("text", "Text"),
            ("base64", "Base64"),
        ],
        required=True,
        default="text",
    )
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("done", "Done"),
            ("failed", "Failed"),
        ],
        required=True,
        default="pending",
        index=True,
    )
    error_message = fields.Text()
    res_model = fields.Char(index=True)
    res_id = fields.Integer(index=True)
    dispense_id = fields.Many2one(
        "cdu.dispense",
        string="Dispense",
        ondelete="set null",
        index=True,
    )
    agent_name = fields.Char()
    last_attempt_at = fields.Datetime(readonly=True)
    completed_at = fields.Datetime(readonly=True)
    failed_at = fields.Datetime(readonly=True)

    @api.model
    def create(self, vals):
        vals = dict(vals)
        if vals.get("name", "/") == "/":
            vals["name"] = self.env["ir.sequence"].next_by_code(
                "bahmni.print.job"
            ) or "/"
        return super().create(vals)

    def write(self, vals):
        vals = dict(vals)
        if vals.get("state") == "done":
            vals.setdefault("completed_at", fields.Datetime.now())
            vals.setdefault("last_attempt_at", fields.Datetime.now())
            vals.setdefault("failed_at", False)
            vals.setdefault("error_message", False)
        elif vals.get("state") == "failed":
            vals.setdefault("failed_at", fields.Datetime.now())
            vals.setdefault("last_attempt_at", fields.Datetime.now())
            vals.setdefault("completed_at", False)
        elif vals.get("state") == "pending":
            vals.setdefault("failed_at", False)
            vals.setdefault("completed_at", False)
            vals.setdefault("error_message", False)
        return super().write(vals)

    @api.model
    def api_get_pending_jobs(self, limit=20):
        limit = min(max(int(limit or 20), 1), 100)
        jobs = self.search(
            [("state", "=", "pending")],
            order="create_date asc, id asc",
            limit=limit,
        )
        return jobs.read(
            [
                "id",
                "name",
                "label_type",
                "printer_key",
                "command_language",
                "payload",
                "payload_encoding",
                "state",
                "res_model",
                "res_id",
            ]
        )

    @api.model
    def api_mark_done(self, job_id, agent_name=False):
        job = self.browse(int(job_id)).exists()
        if not job:
            raise UserError(_("Print job %s was not found.") % job_id)
        job.write(
            {
                "state": "done",
                "agent_name": agent_name or self.env.user.name,
            }
        )
        return True

    @api.model
    def api_mark_failed(self, job_id, error_message, agent_name=False):
        job = self.browse(int(job_id)).exists()
        if not job:
            raise UserError(_("Print job %s was not found.") % job_id)
        job.write(
            {
                "state": "failed",
                "error_message": error_message or _("Unknown print failure."),
                "agent_name": agent_name or self.env.user.name,
            }
        )
        return True

    def action_retry(self):
        self.write({"state": "pending"})

    def action_mark_done(self):
        self.write(
            {
                "state": "done",
                "agent_name": self.env.user.name,
            }
        )
