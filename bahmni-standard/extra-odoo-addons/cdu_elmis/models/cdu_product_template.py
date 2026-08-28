from odoo import api, fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    cdu_openmrs_drug_uuid = fields.Char(
        string="OpenMRS Drug UUID",
        help="OpenMRS concept drug UUID retained for future eLMIS orderable mapping.",
    )
    cdu_elmis_orderable_id = fields.Char(
        string="eLMIS Orderable UUID",
        copy=False,
        index=True,
        help="Stable eLMIS orderable identifier used throughout CDU fulfilment.",
    )

    _sql_constraints = [
        (
            "cdu_elmis_orderable_id_unique",
            "unique(cdu_elmis_orderable_id)",
            "This eLMIS orderable is already linked to another product.",
        )
    ]

    @api.model
    def _sync_cdu_elmis_product_catalog(self, orderables):
        """Upsert eLMIS orderables into the medication selector catalog."""
        templates = self.browse()
        ProductTemplate = self.sudo().with_context(active_test=False)
        seen = set()
        for orderable in orderables:
            orderable_id = (orderable.get("orderable_id") or "").strip()
            orderable_code = (orderable.get("orderable_code") or "").strip()
            orderable_name = (orderable.get("orderable_name") or "").strip()
            key = orderable_id or orderable_code or orderable_name.casefold()
            if not key or not orderable_name or key in seen:
                continue
            seen.add(key)

            domain = []
            if orderable_id:
                domain = [("cdu_elmis_orderable_id", "=", orderable_id)]
            if not domain and orderable_code:
                domain = [("cdu_drug_code", "=", orderable_code)]
            template = ProductTemplate.search(domain, limit=1) if domain else self.browse()
            if not template and orderable_code:
                template = ProductTemplate.search(
                    [("cdu_drug_code", "=", orderable_code)], limit=1
                )

            values = {
                "name": orderable_name,
                "cdu_is_drug": True,
                "cdu_elmis_orderable_id": orderable_id or False,
                "cdu_drug_code": orderable_code or orderable_id or False,
            }
            pack_size = int(orderable.get("pack_size") or 0)
            if pack_size > 0:
                values["cdu_pack_size"] = pack_size
            if template:
                template.write(values)
            else:
                values["type"] = "product"
                template = ProductTemplate.create(values)
            templates |= template
        return templates
