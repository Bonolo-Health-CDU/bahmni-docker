from odoo import fields, models


class CduFacility(models.Model):
    _name = "cdu.facility"
    _description = "CDU Facility"
    _order = "name"

    name = fields.Char(string="Facility Name", required=True)
    code = fields.Char(required=True)
    enabled = fields.Boolean(default=True)
    partner_id = fields.Many2one("res.partner", string="Related Partner")
    notes = fields.Text()
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("unique_facility_code", "unique(code)", "Facility code must be unique."),
    ]
