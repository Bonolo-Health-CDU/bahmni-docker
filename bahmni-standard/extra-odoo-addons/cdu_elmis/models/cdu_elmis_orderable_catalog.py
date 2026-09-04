from odoo import fields, models


class CduElmisOrderableCatalog(models.Model):
    _name = "cdu.elmis.orderable.catalog"
    _description = "CDU eLMIS Orderable Catalogue Mapping"
    _order = "generic_label, full_name, orderable_code"
    _rec_name = "full_name"

    medicine_tmpl_id = fields.Many2one(
        "product.template",
        string="Generic Medicine",
        required=True,
        ondelete="restrict",
        index=True,
        domain=[("cdu_is_drug", "=", True)],
    )
    orderable_id = fields.Char(
        string="eLMIS Orderable UUID", required=True, index=True
    )
    orderable_code = fields.Char(string="eLMIS Product Code", index=True)
    full_name = fields.Char(string="eLMIS Full Product Name", required=True)
    generic_label = fields.Char(string="Generic Label", required=True, index=True)
    pack_size = fields.Integer(string="Pack Size")
    dosage_form = fields.Char(string="Dosage Form")
    facility_code = fields.Char(required=True, index=True)
    program_code = fields.Char(required=True, index=True)
    active = fields.Boolean(default=True, index=True)
    last_seen_at = fields.Datetime(readonly=True)

    _sql_constraints = [
        (
            "cdu_elmis_orderable_scope_unique",
            "unique(orderable_id, facility_code, program_code)",
            "This eLMIS orderable already exists for the facility and program.",
        )
    ]
