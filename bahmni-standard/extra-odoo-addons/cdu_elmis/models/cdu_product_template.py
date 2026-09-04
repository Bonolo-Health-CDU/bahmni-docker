import re
import unicodedata

from odoo import api, fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    cdu_openmrs_drug_uuid = fields.Char(
        string="OpenMRS Drug UUID",
        help="OpenMRS concept drug UUID retained for future eLMIS orderable mapping.",
    )
    cdu_elmis_orderable_id = fields.Char(
        string="Legacy eLMIS Orderable UUID",
        copy=False,
        index=True,
        help="Deprecated one-to-one mapping retained for historical records.",
    )
    cdu_elmis_orderable_catalog_ids = fields.One2many(
        "cdu.elmis.orderable.catalog",
        "medicine_tmpl_id",
        string="eLMIS Orderable Variants",
    )

    _sql_constraints = [
        (
            "cdu_elmis_orderable_id_unique",
            "unique(cdu_elmis_orderable_id)",
            "This legacy eLMIS orderable is already linked to another product.",
        )
    ]

    @api.model
    def _cdu_normalize_catalog_key(self, value):
        normalized = unicodedata.normalize("NFKD", value or "")
        normalized = "".join(
            char for char in normalized if not unicodedata.combining(char)
        )
        return re.sub(r"[^a-z0-9]+", "-", normalized.casefold()).strip("-")

    @api.model
    def _cdu_parse_orderable_label(self, full_name, dosage_form=None):
        """Return stable, pack-free clinical fields from an eLMIS full name."""
        cleaned = re.sub(r"\s+", " ", (full_name or "").strip())
        cleaned = re.sub(r"\s*\(all\)\s*$", "", cleaned, flags=re.IGNORECASE)
        strength_pattern = re.compile(
            r"\d+(?:\.\d+)?(?:\s*/\s*\d+(?:\.\d+)?)*\s*"
            r"(?:micrograms?|mcg|mg|g|ml|iu|units?|%)"
            r"(?:\s*/\s*\d+(?:\.\d+)?\s*"
            r"(?:micrograms?|mcg|mg|g|ml|iu|units?|%))*",
            flags=re.IGNORECASE,
        )
        match = strength_pattern.search(cleaned)
        if match:
            generic_name = cleaned[: match.start()].strip(" -/,;")
            strength = re.sub(r"\s+", "", match.group(0))
            remainder = cleaned[match.end() :].strip()
        else:
            generic_name = re.sub(r"\s+\d+\s*$", "", cleaned).strip()
            strength = ""
            remainder = ""

        inferred_form = (dosage_form or "").strip()
        if not inferred_form and remainder:
            inferred_form = re.sub(r"\s+\d+\s*$", "", remainder).strip(" -/,;")
        label = " ".join(part for part in (generic_name, strength) if part).strip()
        return {
            "generic_name": generic_name or cleaned,
            "strength": strength,
            "dosage_form": inferred_form,
            "generic_label": label or cleaned,
        }

    @api.model
    def _cdu_catalog_pack_size(self, value):
        try:
            pack_size = int(float(value or 0))
        except (TypeError, ValueError):
            return 0
        return max(pack_size, 0)

    @api.model
    def _sync_cdu_elmis_product_catalog(
        self,
        orderables,
        facility_code=None,
        program_code=None,
        complete=False,
    ):
        """Upsert variants and merge their pack presentations into medicines."""
        ProductTemplate = self.sudo().with_context(active_test=False)
        Mapping = self.env["cdu.elmis.orderable.catalog"].sudo().with_context(
            active_test=False
        )
        facility_code = (facility_code or "UNSCOPED").strip()
        program_code = (program_code or "UNSCOPED").strip()
        templates = self.browse()
        seen_orderable_ids = set()

        for orderable in orderables:
            orderable_id = str(orderable.get("orderable_id") or "").strip()
            full_name = str(orderable.get("orderable_name") or "").strip()
            if not orderable_id or not full_name or orderable_id in seen_orderable_ids:
                continue
            seen_orderable_ids.add(orderable_id)
            parsed = self._cdu_parse_orderable_label(
                full_name, orderable.get("dosage_form")
            )
            catalog_key = self._cdu_normalize_catalog_key(parsed["generic_label"])
            if not catalog_key:
                continue

            template = ProductTemplate.search(
                [("cdu_catalog_key", "=", catalog_key)], limit=1
            )
            if not template:
                template = ProductTemplate.search(
                    [
                        ("cdu_is_drug", "=", True),
                        ("name", "=ilike", parsed["generic_label"]),
                    ],
                    limit=1,
                )
            template_values = {
                "name": parsed["generic_label"],
                "cdu_is_drug": True,
                "cdu_catalog_source": "elmis",
                "cdu_catalog_key": catalog_key,
                "cdu_generic_name": parsed["generic_name"],
                "cdu_strength": parsed["strength"] or False,
                "cdu_dosage_form": parsed["dosage_form"] or False,
                "cdu_pack_size": 0,
                "cdu_drug_code": catalog_key,
                "active": True,
            }
            if template:
                template.write(template_values)
            else:
                template_values["type"] = "product"
                template = ProductTemplate.create(template_values)
            templates |= template

            mapping = Mapping.search(
                [
                    ("orderable_id", "=", orderable_id),
                    ("facility_code", "=", facility_code),
                    ("program_code", "=", program_code),
                ],
                limit=1,
            )
            mapping_values = {
                "medicine_tmpl_id": template.id,
                "orderable_code": str(orderable.get("orderable_code") or "").strip()
                or False,
                "full_name": full_name,
                "generic_label": parsed["generic_label"],
                "pack_size": self._cdu_catalog_pack_size(
                    orderable.get("pack_size")
                ),
                "dosage_form": parsed["dosage_form"] or False,
                "active": True,
                "last_seen_at": fields.Datetime.now(),
            }
            if mapping:
                mapping.write(mapping_values)
            else:
                mapping_values.update(
                    {
                        "orderable_id": orderable_id,
                        "facility_code": facility_code,
                        "program_code": program_code,
                    }
                )
                Mapping.create(mapping_values)

        if complete:
            stale = Mapping.search(
                [
                    ("facility_code", "=", facility_code),
                    ("program_code", "=", program_code),
                    ("orderable_id", "not in", list(seen_orderable_ids) or [False]),
                    ("active", "=", True),
                ]
            )
            stale.write({"active": False})
            orphaned_templates = stale.mapped("medicine_tmpl_id").filtered(
                lambda medicine: not medicine.cdu_elmis_orderable_catalog_ids.filtered(
                    "active"
                )
            )
            for orphaned_template in orphaned_templates:
                orphaned_template.write({"active": False})

        return templates
