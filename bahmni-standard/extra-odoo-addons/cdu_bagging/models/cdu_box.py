from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class CduBox(models.Model):
    _name = "cdu.box"
    _description = "CDU Box"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc, id desc"

    name = fields.Char(
        string="Box Reference",
        required=True,
        copy=False,
        default=lambda self: _("New"),
        tracking=True,
    )
    status = fields.Selection(
        [
            ("open", "Open"),
            ("closed", "Closed"),
            ("synced", "Synced"),
            ("cancelled", "Cancelled"),
        ],
        string="Status",
        default="open",
        required=True,
        tracking=True,
    )
    location_id = fields.Many2one(
        "stock.location",
        string="Destination Location",
        tracking=True,
    )
    bag_ids = fields.One2many("cdu.bag", "box_id", string="Bags")
    bag_count = fields.Integer(string="Bags", compute="_compute_bag_count")
    bag_barcode_scan = fields.Char(string="Scan Bag Barcode")
    closed_date = fields.Datetime(string="Closed Date", readonly=True, tracking=True)
    closed_user_id = fields.Many2one(
        "res.users",
        string="Closed By",
        readonly=True,
        tracking=True,
    )
    synced_date = fields.Datetime(string="Synced Date", readonly=True, tracking=True)
    synced_user_id = fields.Many2one(
        "res.users",
        string="Synced By",
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

    @api.model
    def create(self, vals):
        if vals.get("name", _("New")) == _("New"):
            vals["name"] = self.env["ir.sequence"].next_by_code("cdu.box") or _("New")
        return super().create(vals)

    @api.depends("bag_ids")
    def _compute_bag_count(self):
        for box in self:
            box.bag_count = len(box.bag_ids)

    @api.constrains("bag_ids", "location_id")
    def _check_bag_locations(self):
        for box in self:
            if not box.location_id:
                continue

            wrong_location_bags = box.bag_ids.filtered(
                lambda bag: bag.destination_location_id
                and bag.destination_location_id != box.location_id
            )
            if wrong_location_bags:
                raise ValidationError(
                    _("All bags in a box must have the same destination location.")
                )

    def action_scan_bag_barcode(self):
        self.ensure_one()
        barcode = (self.bag_barcode_scan or "").strip()
        if not barcode:
            raise UserError(_("Please enter or scan a bag barcode."))

        bag = self._find_scannable_bag(barcode)
        self._add_bag_to_box(bag)
        self.bag_barcode_scan = False

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Bag added"),
                "message": _("Bag %s was added to box %s.") % (bag.name, self.name),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def action_close_box(self):
        for box in self:
            if box.status != "open":
                continue
            if not box.bag_ids:
                raise UserError(_("You cannot close an empty box."))
            box.write(
                {
                    "status": "closed",
                    "closed_date": fields.Datetime.now(),
                    "closed_user_id": self.env.user.id,
                }
            )

    def action_sync_box(self):
        for box in self:
            if box.status != "closed":
                raise UserError(_("Only closed boxes can be synced."))
            box.write(
                {
                    "status": "synced",
                    "synced_date": fields.Datetime.now(),
                    "synced_user_id": self.env.user.id,
                }
            )

    def action_cancel(self):
        self.write({"status": "cancelled"})

    def action_reopen(self):
        self.write({"status": "open"})

    def _find_scannable_bag(self, barcode):
        bag = self.env["cdu.bag"].search([("name", "=", barcode)], limit=1)
        if not bag:
            raise UserError(
                _("Bag %s was not found. Scan it on the picking before boxing.")
                % barcode
            )
        if bag.box_id and bag.box_id != self:
            raise UserError(
                _("Bag %(bag)s is already in box %(box)s.")
                % {"bag": bag.name, "box": bag.box_id.name}
            )
        if bag.status in ("dispatched", "delivered", "cancelled"):
            raise UserError(
                _("Bag %s cannot be boxed because it is already %s.")
                % (bag.name, dict(bag._fields["status"].selection)[bag.status])
            )
        return bag

    def _add_bag_to_box(self, bag):
        self.ensure_one()
        if self.status != "open":
            raise UserError(_("You can only scan bags into an open box."))

        destination = bag.destination_location_id
        if self.location_id and destination and self.location_id != destination:
            raise UserError(
                _(
                    "Bag %(bag)s is for %(bag_location)s, but this box is for %(box_location)s. Close this box and create a new one."
                )
                % {
                    "bag": bag.name,
                    "bag_location": destination.display_name,
                    "box_location": self.location_id.display_name,
                }
            )

        values = {
            "box_id": self.id,
            "status": "packed",
            "boxed_date": fields.Datetime.now(),
            "boxed_user_id": self.env.user.id,
        }
        if not self.location_id and destination:
            self.location_id = destination.id
        bag.write(values)
