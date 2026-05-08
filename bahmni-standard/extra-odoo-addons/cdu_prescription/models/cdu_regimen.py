from odoo import fields, api, models


class CduRegimen(models.Model):
    _name = "cdu.regimen"
    _description = "CDU Regimen"
    _order = "code"

    name = fields.Char(required=True)

    code = fields.Char(
        required=True,
        index=True,
    )

    active = fields.Boolean(default=True)

    line_ids = fields.One2many(
        "cdu.regimen.line",
        "regimen_id",
        string="Drug Components",
    )

    _sql_constraints = [
        (
            "unique_regimen_code",
            "unique(code)",
            "Regimen code already exists.",
        )
    ]

    @api.model
    def create(self, vals):

        if vals.get("code"):
            vals["code"] = vals["code"].strip().lower()

        return super().create(vals)


    def write(self, vals):

        if vals.get("code"):
            vals["code"] = vals["code"].strip().lower()

        return super().write(vals)

class CduRegimenLine(models.Model):
    _name = "cdu.regimen.line"
    _description = "CDU Regimen Drug"

    regimen_id = fields.Many2one(
            "cdu.regimen",
            required=True,
            ondelete="cascade",
        )

    product_id = fields.Many2one(
            "product.product",
            required=True,
            domain=[("product_tmpl_id.cdu_is_drug", "=", True)],
        )

    daily_dose = fields.Float(
            required=True,
            default=1.0,
            help="Units/tablets per day.",
        )