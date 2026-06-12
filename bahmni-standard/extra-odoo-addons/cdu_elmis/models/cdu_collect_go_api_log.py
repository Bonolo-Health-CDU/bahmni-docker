from odoo import fields, models


class CduCollectGoApiLog(models.Model):
    _name = "cdu.collect.go.api.log"
    _description = "CDU Collect-and-Go API Log"
    _order = "timestamp desc, id desc"

    call_type = fields.Selection(
        [
            ("TEST_CONNECTION", "Test Connection"),
            ("CREATE_PARCEL", "Create Parcel"),
            ("UPDATE_PARCEL", "Update Parcel"),
            ("GET_MESSAGE", "Get Message"),
            ("GET_PARCEL_STATUS", "Get Parcel Status"),
            ("RETURN_PARCEL", "Return Parcel"),
        ],
        required=True,
        index=True,
    )
    reference_guid = fields.Char(index=True)
    endpoint = fields.Char(required=True)
    http_method = fields.Char(required=True)
    request_payload = fields.Text()
    response_body = fields.Text()
    http_status_code = fields.Integer(string="HTTP Status Code")
    bridge_status_code = fields.Integer(
        string="Bridge Status Code",
        help="Status code returned inside the Collect-and-Go JSON response body.",
    )
    success = fields.Boolean(default=False, index=True)
    error_message = fields.Char()
    box_id = fields.Many2one(
        "cdu.box",
        string="Box",
        ondelete="set null",
        index=True,
    )
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
    retry_count = fields.Integer(default=0)
    retry_of = fields.Many2one(
        "cdu.collect.go.api.log",
        string="Retry Of",
        ondelete="set null",
        index=True,
    )
    retry_ids = fields.One2many(
        "cdu.collect.go.api.log",
        "retry_of",
        string="Retries",
    )
