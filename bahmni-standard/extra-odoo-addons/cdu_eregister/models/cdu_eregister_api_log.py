from odoo import fields, models


class CduEregisterApiLog(models.Model):
    _name = "cdu.eregister.api.log"
    _description = "CDU eRegister FHIR API Log"
    _order = "timestamp desc, id desc"

    call_type = fields.Selection(
        [
            ("TEST_CONNECTION", "Test Connection"),
            ("PULL_TASKS", "Pull New Prescriptions"),
            ("READ_RESOURCE", "Read Resource"),
            ("INGEST", "Ingest Prescription"),
            ("PUSH_STATUS", "Publish Status"),
            ("CHECK_CANCELLATIONS", "Check Cancellations"),
        ],
        required=True,
        index=True,
    )
    endpoint = fields.Char(required=True)
    http_method = fields.Char(required=True)
    request_payload = fields.Text()
    response_body = fields.Text()
    http_status_code = fields.Integer(string="HTTP Status Code")
    success = fields.Boolean(default=False, index=True)
    error_message = fields.Text()
    prescription_id = fields.Many2one(
        "cdu.prescription",
        string="Prescription",
        ondelete="set null",
        index=True,
    )
    fhir_reference = fields.Char(string="FHIR Reference", index=True)
    user_id = fields.Many2one(
        "res.users",
        required=True,
        default=lambda self: self.env.user,
        ondelete="restrict",
        index=True,
    )
    timestamp = fields.Datetime(
        required=True,
        default=fields.Datetime.now,
        index=True,
    )
