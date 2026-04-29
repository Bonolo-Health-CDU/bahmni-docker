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
        required=True,
    )
    partner_id = fields.Many2one("res.partner", string="Related Partner")
    external_reference = fields.Char(
        string="Collect-and-Go Reference",
        help="External identifier used when dispatch jobs are submitted.",
    )
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("unique_code", "unique(code)", "Collection point code must be unique."),
    ]
