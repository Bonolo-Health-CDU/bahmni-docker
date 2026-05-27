from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    cdu_openmrs_drug_uuid = fields.Char(
        string="OpenMRS Drug UUID",
        help="OpenMRS concept drug UUID retained for future eLMIS orderable mapping.",
    )
