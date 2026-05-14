from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class CduBag(models.Model):
    _name = "cdu.bag"
    _description = "CDU Bag"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "scan_date desc, id desc"

    name = fields.Char(
        string="Bag Barcode",
        required=True,
        tracking=True,
        index=True,
    )
    picking_id = fields.Many2one(
        "stock.picking",
        string="Picking",
        ondelete="set null",
        tracking=True,
        index=True,
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Patient/Contact",
        related="picking_id.partner_id",
        store=True,
        readonly=True,
    )
    destination_location_id = fields.Many2one(
        "stock.location",
        string="Destination Location",
        related="picking_id.location_dest_id",
        store=True,
        readonly=True,
    )
    box_id = fields.Many2one(
        "cdu.box",
        string="Box",
        ondelete="set null",
        tracking=True,
        index=True,
    )
    status = fields.Selection(
        [
            ("draft", "Draft"),
            ("scanned", "Scanned"),
            ("packed", "Packed"),
            ("dispatched", "Dispatched"),
            ("delivered", "Delivered"),
            ("cancelled", "Cancelled"),
        ],
        string="Status",
        default="draft",
        required=True,
        tracking=True,
    )
    scan_date = fields.Datetime(string="Last Scan Date", readonly=True, tracking=True)
    scan_user_id = fields.Many2one(
        "res.users",
        string="Last Scanned By",
        readonly=True,
        tracking=True,
    )
    boxed_date = fields.Datetime(string="Boxed Date", readonly=True, tracking=True)
    boxed_user_id = fields.Many2one(
        "res.users",
        string="Boxed By",
        readonly=True,
        tracking=True,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        default=lambda self: self.env.company,
        required=True,
    )
    note = fields.Text(string="Notes")

    _sql_constraints = [
        ("name_unique", "unique(name)", "Bag barcode must be unique."),
    ]

    @api.constrains("name")
    def _check_name(self):
        for bag in self:
            if bag.name and not bag.name.strip():
                raise ValidationError("Bag barcode cannot be empty.")

    @api.constrains("picking_id")
    def _check_dispensing_picking(self):
        for bag in self:
            if not bag.picking_id:
                continue
            operation_type_name = bag.picking_id.picking_type_id.name or ""
            if "dispensing" not in operation_type_name.lower():
                raise ValidationError(
                    _(
                        "Bagging is only allowed for pickings whose Operation Type contains 'Dispensing'. Current Operation Type: %s"
                    )
                    % (operation_type_name or _("Not set"))
                )

    def action_mark_packed(self):
        self.write(
            {
                "status": "packed",
                "boxed_date": fields.Datetime.now(),
                "boxed_user_id": self.env.user.id,
            }
        )

    def action_mark_dispatched(self):
        self.write({"status": "dispatched"})

    def action_mark_delivered(self):
        self.write({"status": "delivered"})

    def action_cancel(self):
        self.write({"status": "cancelled"})

    def action_reset_to_scanned(self):
        self.write({"status": "scanned"})
