from odoo import _, api, fields, models

from .stock_option_schema import ensure_stock_on_hand_units_column


STOCK_STATUS_SELECTION = [
    ("expired", "Expired"),
    ("expires_soon", "Expires Soon"),
    ("low_stock", "Low Stock"),
    ("no_expiry", "No Expiry"),
]

STOCK_STATUS_PRIORITY = {
    "expired": 4,
    "expires_soon": 3,
    "low_stock": 2,
    "no_expiry": 1,
    False: 0,
}


def table_exists(env, table_name):
    env.cr.execute("SELECT to_regclass(%s)", (table_name,))
    return bool(env.cr.fetchone()[0])


def highest_stock_status(options):
    status = False
    for option in options:
        option_status = option.stock_status or False
        if STOCK_STATUS_PRIORITY[option_status] > STOCK_STATUS_PRIORITY[status]:
            status = option_status
    return status


class CduElmisStockSummary(models.Model):
    _name = "cdu.elmis.stock.summary"
    _description = "CDU Store Stock Product Summary"
    _order = "orderable_name"
    _rec_name = "orderable_name"

    batch_id = fields.Many2one(
        "cdu.batch",
        required=True,
        ondelete="cascade",
        index=True,
    )
    orderable_code = fields.Char(index=True)
    orderable_id = fields.Char(string="eLMIS Orderable UUID", index=True)
    orderable_name = fields.Char(string="Product", required=True)
    lot_count = fields.Integer(string="Batches/Lots")
    total_stock_on_hand = fields.Integer(string="Total Available Packs")
    total_stock_on_hand_units = fields.Integer(string="Total Available Units")
    earliest_expiration_date = fields.Date(string="Earliest Expiry Date")
    latest_stock_date = fields.Date(string="Latest Stock Date")
    stock_status = fields.Selection(STOCK_STATUS_SELECTION, string="Status")
    detail_option_ids = fields.Many2many(
        "cdu.elmis.stock.option",
        string="Lot Details",
        compute="_compute_detail_option_ids",
        readonly=True,
    )

    def init(self):
        if not table_exists(self.env, "cdu_elmis_stock_option"):
            return
        ensure_stock_on_hand_units_column(self.env.cr, "cdu_elmis_stock_option")

        self.env.cr.execute("DELETE FROM cdu_elmis_stock_summary")
        self.env.cr.execute(
            """
            INSERT INTO cdu_elmis_stock_summary (
                batch_id,
                orderable_code,
                orderable_id,
                orderable_name,
                lot_count,
                total_stock_on_hand,
                total_stock_on_hand_units,
                earliest_expiration_date,
                latest_stock_date,
                stock_status,
                create_date,
                write_date
            )
            SELECT
                batch_id,
                orderable_code,
                orderable_id,
                orderable_name,
                COUNT(*)::integer AS lot_count,
                COALESCE(SUM(stock_on_hand), 0)::integer AS total_stock_on_hand,
                COALESCE(SUM(stock_on_hand_units), 0)::integer AS total_stock_on_hand_units,
                MIN(expiration_date) FILTER (WHERE expiration_date IS NOT NULL) AS earliest_expiration_date,
                MAX(occurred_date) AS latest_stock_date,
                CASE
                    WHEN BOOL_OR(expiration_date < CURRENT_DATE) THEN 'expired'
                    WHEN BOOL_OR(expiration_date <= CURRENT_DATE + INTERVAL '90 days') THEN 'expires_soon'
                    WHEN BOOL_OR(stock_on_hand <= 5) THEN 'low_stock'
                    WHEN BOOL_OR(expiration_date IS NULL) THEN 'no_expiry'
                    ELSE NULL
                END AS stock_status,
                NOW(),
                NOW()
            FROM cdu_elmis_stock_option
            GROUP BY batch_id, orderable_code, orderable_id, orderable_name
            """
        )
    @api.depends("batch_id", "orderable_code", "orderable_id", "orderable_name")
    def _compute_detail_option_ids(self):
        Option = self.env["cdu.elmis.stock.option"]
        for summary in self:
            summary.detail_option_ids = Option.search(summary._detail_domain())

    def _detail_domain(self):
        self.ensure_one()
        domain = [
            ("batch_id", "=", self.batch_id.id),
            ("orderable_name", "=", self.orderable_name),
        ]
        if self.orderable_code:
            domain.append(("orderable_code", "=", self.orderable_code))
        else:
            domain.append(("orderable_code", "=", False))
        if self.orderable_id:
            domain.append(("orderable_id", "=", self.orderable_id))
        else:
            domain.append(("orderable_id", "=", False))
        return domain

    def action_open_lot_details(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Batches/Lots for %s") % self.orderable_name,
            "res_model": "cdu.elmis.stock.option",
            "view_mode": "tree",
            "target": "new",
            "domain": self._detail_domain(),
        }


class CduDispenseStockSummary(models.Model):
    _name = "cdu.dispense.stock.summary"
    _description = "CDU Production Floor Stock Product Summary"
    _order = "orderable_name"
    _rec_name = "orderable_name"

    dispense_id = fields.Many2one(
        "cdu.dispense",
        required=True,
        ondelete="cascade",
        index=True,
    )
    orderable_code = fields.Char(index=True)
    orderable_id = fields.Char(string="eLMIS Orderable UUID", index=True)
    orderable_name = fields.Char(string="Product", required=True)
    lot_count = fields.Integer(string="Batches/Lots")
    total_stock_on_hand = fields.Integer(string="Total Available Packs")
    total_stock_on_hand_units = fields.Integer(string="Total Available Units")
    earliest_expiration_date = fields.Date(string="Earliest Expiry Date")
    latest_stock_date = fields.Date(string="Latest Stock Date")
    stock_status = fields.Selection(STOCK_STATUS_SELECTION, string="Status")
    detail_option_ids = fields.Many2many(
        "cdu.dispense.stock.option",
        string="Lot Details",
        compute="_compute_detail_option_ids",
        readonly=True,
    )

    def init(self):
        if not table_exists(self.env, "cdu_dispense_stock_option"):
            return
        ensure_stock_on_hand_units_column(self.env.cr, "cdu_dispense_stock_option")

        self.env.cr.execute("DELETE FROM cdu_dispense_stock_summary")
        self.env.cr.execute(
            """
            INSERT INTO cdu_dispense_stock_summary (
                dispense_id,
                orderable_code,
                orderable_id,
                orderable_name,
                lot_count,
                total_stock_on_hand,
                total_stock_on_hand_units,
                earliest_expiration_date,
                latest_stock_date,
                stock_status,
                create_date,
                write_date
            )
            SELECT
                dispense_id,
                orderable_code,
                orderable_id,
                orderable_name,
                COUNT(*)::integer AS lot_count,
                COALESCE(SUM(stock_on_hand), 0)::integer AS total_stock_on_hand,
                COALESCE(SUM(stock_on_hand_units), 0)::integer AS total_stock_on_hand_units,
                MIN(expiration_date) FILTER (WHERE expiration_date IS NOT NULL) AS earliest_expiration_date,
                MAX(occurred_date) AS latest_stock_date,
                CASE
                    WHEN BOOL_OR(expiration_date < CURRENT_DATE) THEN 'expired'
                    WHEN BOOL_OR(expiration_date <= CURRENT_DATE + INTERVAL '90 days') THEN 'expires_soon'
                    WHEN BOOL_OR(stock_on_hand <= 5) THEN 'low_stock'
                    WHEN BOOL_OR(expiration_date IS NULL) THEN 'no_expiry'
                    ELSE NULL
                END AS stock_status,
                NOW(),
                NOW()
            FROM cdu_dispense_stock_option
            GROUP BY dispense_id, orderable_code, orderable_id, orderable_name
            """
        )
    @api.depends("dispense_id", "orderable_code", "orderable_id", "orderable_name")
    def _compute_detail_option_ids(self):
        Option = self.env["cdu.dispense.stock.option"]
        for summary in self:
            summary.detail_option_ids = Option.search(summary._detail_domain())

    def _detail_domain(self):
        self.ensure_one()
        domain = [
            ("dispense_id", "=", self.dispense_id.id),
            ("orderable_name", "=", self.orderable_name),
        ]
        if self.orderable_code:
            domain.append(("orderable_code", "=", self.orderable_code))
        else:
            domain.append(("orderable_code", "=", False))
        if self.orderable_id:
            domain.append(("orderable_id", "=", self.orderable_id))
        else:
            domain.append(("orderable_id", "=", False))
        return domain

    def action_open_lot_details(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Batches/Lots for %s") % self.orderable_name,
            "res_model": "cdu.dispense.stock.option",
            "view_mode": "tree",
            "target": "new",
            "domain": self._detail_domain(),
        }
