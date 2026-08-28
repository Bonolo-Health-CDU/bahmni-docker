from odoo import api, fields, models


class CduElmisStockOption(models.Model):
    _name = "cdu.elmis.stock.option"
    _description = "CDU eLMIS Stock Option"
    _order = "orderable_name, expiration_date, lot"
    _rec_name = "name"

    batch_id = fields.Many2one(
        "cdu.batch",
        required=True,
        ondelete="cascade",
        index=True,
    )
    facility_code = fields.Char(required=True, index=True)
    program_code = fields.Char(required=True, index=True)
    orderable_code = fields.Char(required=True, index=True)
    orderable_id = fields.Char(string="eLMIS Orderable UUID", index=True)
    orderable_name = fields.Char(required=True)
    pack_size = fields.Integer(string="Pack Size")
    lot = fields.Char(index=True)
    lot_id = fields.Char(string="eLMIS Lot UUID", index=True)
    stock_on_hand = fields.Integer(string="Available Packs", required=True)
    stock_on_hand_units = fields.Integer(string="Available Units")
    expiration_date = fields.Date(index=True)
    occurred_date = fields.Date()
    stock_status = fields.Selection(
        [
            ("expired", "Expired"),
            ("expires_soon", "Expires Soon"),
            ("low_stock", "Low Stock"),
            ("no_expiry", "No Expiry"),
        ],
        string="Status",
        compute="_compute_stock_status",
    )
    expires_in_days = fields.Integer(
        string="Expires In",
        compute="_compute_stock_status",
    )
    name = fields.Char(compute="_compute_name", store=True)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        self.env["product.template"]._sync_cdu_elmis_product_catalog(vals_list)
        return records

    def init(self):
        self.env.cr.execute(
            """
            UPDATE cdu_elmis_stock_option
               SET stock_on_hand_units = stock_on_hand,
                   stock_on_hand = FLOOR(
                       stock_on_hand::numeric / COALESCE(NULLIF(pack_size, 0), 30)
                   )::integer
             WHERE stock_on_hand_units IS NULL
               AND stock_on_hand IS NOT NULL
            """
        )

    @api.depends(
        "orderable_name",
        "orderable_code",
        "lot",
        "stock_on_hand",
        "stock_on_hand_units",
        "expiration_date",
    )
    def _compute_name(self):
        for option in self:
            bits = [
                option.orderable_name or option.orderable_code,
                "Batch: %s" % (option.lot or "N/A"),
            ]
            if option.expiration_date:
                bits.append("Expiry Date: %s" % option.expiration_date)
            bits.append("Available Packs: %s" % option.stock_on_hand)
            if option.stock_on_hand_units:
                bits.append("Units: %s" % option.stock_on_hand_units)
            option.name = " | ".join(bits)

    @api.depends("expiration_date", "stock_on_hand")
    def _compute_stock_status(self):
        today = fields.Date.context_today(self)
        for option in self:
            option.expires_in_days = 0
            option.stock_status = False
            if option.expiration_date:
                days_to_expiry = (option.expiration_date - today).days
                option.expires_in_days = days_to_expiry
                if days_to_expiry < 0:
                    option.stock_status = "expired"
                elif days_to_expiry <= 90:
                    option.stock_status = "expires_soon"
                elif option.stock_on_hand <= 5:
                    option.stock_status = "low_stock"
            elif option.stock_on_hand <= 5:
                option.stock_status = "low_stock"
            else:
                option.stock_status = "no_expiry"

    @api.model
    def name_search(self, name="", args=None, operator="ilike", limit=100):
        args = args or []
        if not name:
            return super().name_search(name=name, args=args, operator=operator, limit=limit)

        domain = [
            "|",
            "|",
            ("orderable_name", operator, name),
            ("orderable_code", operator, name),
            ("lot", operator, name),
        ]
        return self.search(domain + args, limit=limit).name_get()
