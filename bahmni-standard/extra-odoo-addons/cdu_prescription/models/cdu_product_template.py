from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    cdu_is_drug = fields.Boolean(
        string="CDU Drug"
    )

    cdu_pack_size = fields.Integer(
        string="Pack Size",
        default=30,
        help="Number of tablets/capsules per bottle.",
    )

    cdu_default_daily_dose = fields.Float(
        string="Default Daily Dose",
        default=1.0,
    )

    cdu_drug_code = fields.Char(
        string="CDU Drug Code"
    )