from odoo import api, fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    cdu_is_drug = fields.Boolean(
        string="CDU Drug"
    )

    cdu_catalog_source = fields.Selection(
        [("manual", "Manual"), ("elmis", "eLMIS")],
        string="Catalogue Source",
        default="manual",
        index=True,
    )

    cdu_catalog_key = fields.Char(
        string="Generic Catalogue Key",
        copy=False,
        index=True,
        help="Normalized identity used to merge eLMIS pack variants into one medicine.",
    )

    cdu_generic_name = fields.Char(string="Generic Medicine Name", index=True)

    cdu_strength = fields.Char(string="Strength")

    cdu_dosage_form = fields.Char(
        string="Dosage Form",
        help="Retained for mapping and safety checks; not shown in verification selectors.",
    )

    cdu_verification_label = fields.Char(
        string="Verification Label",
        compute="_compute_cdu_verification_label",
        store=True,
        index=True,
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

    _sql_constraints = [
        (
            "cdu_catalog_key_unique",
            "unique(cdu_catalog_key)",
            "This generic medicine is already present in the CDU catalogue.",
        )
    ]

    @api.depends("cdu_generic_name", "cdu_strength", "name")
    def _compute_cdu_verification_label(self):
        for template in self:
            label = " ".join(
                part.strip()
                for part in (template.cdu_generic_name, template.cdu_strength)
                if (part or "").strip()
            )
            template.cdu_verification_label = label or template.name


class ProductProduct(models.Model):
    _inherit = "product.product"

    def name_get(self):
        if not self.env.context.get("cdu_generic_catalog_label"):
            return super().name_get()
        return [
            (
                product.id,
                product.product_tmpl_id.cdu_verification_label
                or product.product_tmpl_id.name,
            )
            for product in self
        ]
