from odoo import api, fields, models


class CduElmisStockCache(models.Model):
    _name = "cdu.elmis.stock.cache"
    _description = "CDU eLMIS Stock Cache"
    _order = "expires_at desc, id desc"

    facility_code = fields.Char(required=True, index=True)
    program_code = fields.Char(required=True, index=True)
    orderable_code = fields.Char(required=True, default="ALL", index=True)
    response_json = fields.Text(required=True)
    fetched_at = fields.Datetime(required=True, default=fields.Datetime.now)
    expires_at = fields.Datetime(required=True, index=True)
    is_valid = fields.Boolean(compute="_compute_is_valid")

    _sql_constraints = [
        (
            "facility_program_orderable_unique",
            "unique(facility_code, program_code, orderable_code)",
            "Only one eLMIS stock cache entry is allowed per facility, program, and orderable.",
        )
    ]

    @api.depends("expires_at")
    def _compute_is_valid(self):
        now = fields.Datetime.now()
        for cache in self:
            cache.is_valid = bool(cache.expires_at and cache.expires_at > now)
