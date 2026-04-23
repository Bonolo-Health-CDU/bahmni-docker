from odoo import fields, models


class CduProgram(models.Model):
    _name = "cdu.program"
    _description = "CDU Clinical Program"
    _order = "sequence, name"

    name = fields.Char(required=True)
    code = fields.Char()
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
