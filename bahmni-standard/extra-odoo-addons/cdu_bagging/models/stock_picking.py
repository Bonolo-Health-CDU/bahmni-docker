from odoo import _, api, fields, models
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _inherit = "stock.picking"

    cdu_bag_ids = fields.One2many("cdu.bag", "picking_id", string="Bags")
    cdu_bag_count = fields.Integer(
        string="Bags",
        compute="_compute_cdu_bag_fields",
    )
    cdu_bagging_status = fields.Selection(
        [
            ("none", "No Bags"),
            ("scanned", "Scanned"),
            ("packed", "Packed"),
            ("dispatched", "Dispatched"),
            ("delivered", "Delivered"),
            ("mixed", "Mixed"),
        ],
        string="Bagging Status",
        compute="_compute_cdu_bag_fields",
        store=True,
    )
    cdu_bag_barcode_scan = fields.Char(string="Scan Bag Barcode")
    cdu_is_dispensing_operation = fields.Boolean(
        string="Is Dispensing Operation",
        compute="_compute_cdu_is_dispensing_operation",
    )

    @api.depends("picking_type_id.name")
    def _compute_cdu_is_dispensing_operation(self):
        for picking in self:
            operation_type_name = picking.picking_type_id.name or ""
            picking.cdu_is_dispensing_operation = (
                "dispensing" in operation_type_name.lower()
            )

    @api.depends("cdu_bag_ids.status")
    def _compute_cdu_bag_fields(self):
        final_statuses = {"packed", "dispatched", "delivered"}
        for picking in self:
            bags = picking.cdu_bag_ids
            picking.cdu_bag_count = len(bags)
            statuses = set(bags.mapped("status"))

            if not statuses:
                picking.cdu_bagging_status = "none"
            elif len(statuses) == 1:
                status = next(iter(statuses))
                picking.cdu_bagging_status = (
                    status if status in final_statuses or status == "scanned" else "mixed"
                )
            else:
                picking.cdu_bagging_status = "mixed"

    def action_scan_bag_barcode(self):
        self.ensure_one()
        barcode = (self.cdu_bag_barcode_scan or "").strip()
        if not barcode:
            raise UserError(_("Please enter or scan a bag barcode."))

        self._cdu_scan_bag(barcode)
        self.cdu_bag_barcode_scan = False

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Bag scanned"),
                "message": _("Bag %s was linked to picking %s.") % (barcode, self.name),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def action_open_bag_scan_wizard(self):
        self.ensure_one()
        self._check_cdu_dispensing_operation()
        return {
            "name": _("Scan Bag"),
            "type": "ir.actions.act_window",
            "res_model": "cdu.bag.scan.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_picking_id": self.id},
        }

    def _cdu_scan_bag(self, barcode):
        self.ensure_one()
        self._check_cdu_dispensing_operation()
        barcode = (barcode or "").strip()
        if not barcode:
            raise UserError(_("Please enter or scan a bag barcode."))

        existing_bag = self.env["cdu.bag"].search([("name", "=", barcode)], limit=1)
        values = {
            "picking_id": self.id,
            "status": "scanned",
            "scan_date": fields.Datetime.now(),
            "scan_user_id": self.env.user.id,
            "company_id": self.company_id.id or self.env.company.id,
        }

        if existing_bag:
            if existing_bag.picking_id and existing_bag.picking_id != self:
                raise UserError(
                    _("Bag %(barcode)s is already linked to picking %(picking)s.")
                    % {
                        "barcode": barcode,
                        "picking": existing_bag.picking_id.name,
                    }
                )
            existing_bag.write(values)
            return existing_bag

        values["name"] = barcode
        return self.env["cdu.bag"].create(values)

    def _check_cdu_dispensing_operation(self):
        self.ensure_one()
        operation_type_name = self.picking_type_id.name or ""
        if "dispensing" not in operation_type_name.lower():
            raise UserError(
                _(
                    "Bagging is only allowed for pickings whose Operation Type contains 'Dispensing'. Current Operation Type: %s"
                )
                % (operation_type_name or _("Not set"))
            )

    def button_validate(self):
        result = super().button_validate()
        bags = self.mapped("cdu_bag_ids").filtered(lambda bag: bag.status == "packed")
        bags.action_mark_dispatched()
        return result
