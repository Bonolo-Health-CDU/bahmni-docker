from odoo import fields, models


class CduElmisApiLog(models.Model):
    _name = "cdu.elmis.api.log"
    _description = "CDU eLMIS API Log"
    _order = "timestamp desc, id desc"

    call_type = fields.Selection(
        [
            ("STOCK_QUERY", "Stock Query"),
            ("PICKING_STORE_DEBIT", "Picking Store Debit"),
            ("PICKING_PRODUCTION_CREDIT", "Picking Production Credit"),
            ("CONSUMPTION", "Consumption"),
            ("RESIDUAL_PRODUCTION_DEBIT", "Residual Production Debit"),
            ("RESIDUAL_STORE_CREDIT", "Residual Store Credit"),
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
    error_message = fields.Char()
    auth_mode = fields.Selection(
        [
            ("system_api_key", "System API Key"),
            ("user_token", "User eLMIS Token"),
        ],
        string="Authentication Mode",
        index=True,
    )
    elmis_username = fields.Char(string="eLMIS Username", index=True)
    batch_id = fields.Many2one(
        "cdu.batch",
        string="Workload Batch",
        ondelete="set null",
        index=True,
    )
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
        "cdu.elmis.api.log",
        string="Retry Of",
        ondelete="set null",
        index=True,
    )
    retry_ids = fields.One2many(
        "cdu.elmis.api.log",
        "retry_of",
        string="Retries",
    )
