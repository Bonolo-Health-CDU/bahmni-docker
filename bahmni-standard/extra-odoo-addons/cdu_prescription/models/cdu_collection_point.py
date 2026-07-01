from odoo import fields, models


class CduCollectionPoint(models.Model):
    _name = "cdu.collection.point"
    _description = "CDU Collection Point"
    _order = "name"

    name = fields.Char(required=True)
    code = fields.Char(required=True)
    point_type = fields.Selection(
        [
            ("e_locker", "E-locker"),
            ("retail_pharmacy", "Partner Retail Pharmacy"),
        ],
        string="CDU Point Type",
        help="Optional CDU classification. Not auto-derived from the Collect-and-Go location feed.",
    )
    partner_id = fields.Many2one("res.partner", string="Related Partner")
    remote_sync_key = fields.Char(
        string="Remote Sync Key",
        copy=False,
        readonly=True,
        index=True,
        help="Composite key used to match the remote location feed safely.",
    )
    remote_location_id = fields.Char(string="Remote Location ID", readonly=True)
    remote_location_type = fields.Integer(string="Remote Location Type", readonly=True)
    address_id = fields.Integer(readonly=True)
    city = fields.Char(readonly=True)
    address_line_1 = fields.Char(readonly=True)
    address_line_2 = fields.Char(readonly=True)
    address_line_3 = fields.Char(readonly=True)
    postal_code = fields.Char(readonly=True)
    country_code_id = fields.Char(readonly=True)
    country_name = fields.Char(readonly=True)
    region_id = fields.Integer(readonly=True)
    region_name = fields.Char(readonly=True)
    gps_coordinates = fields.Char(readonly=True)
    shop_name = fields.Char(readonly=True)
    shop_number = fields.Char(readonly=True)
    telephone_id = fields.Integer(readonly=True)
    telephone_number = fields.Char(readonly=True)
    telephone_type = fields.Char(readonly=True)
    effective_date = fields.Datetime(readonly=True)
    expiry_date = fields.Datetime(readonly=True)
    remote_timestamp = fields.Datetime(readonly=True)
    owner_id = fields.Char(readonly=True)
    external_reference = fields.Char(
        string="Collect-and-Go Reference",
        readonly=True,
        help="External identifier used when dispatch jobs are submitted.",
    )
    last_synced_at = fields.Datetime(readonly=True)
    sync_source = fields.Char(readonly=True)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("unique_code", "unique(code)", "Collection point code must be unique."),
        ("unique_remote_sync_key", "unique(remote_sync_key)", "Remote sync key must be unique."),
    ]
