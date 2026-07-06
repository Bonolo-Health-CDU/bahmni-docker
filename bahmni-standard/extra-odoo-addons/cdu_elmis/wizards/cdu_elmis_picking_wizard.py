from odoo import _, api, fields, models


class CduElmisPickingWizard(models.TransientModel):
    _name = "cdu.elmis.picking.wizard"
    _description = "CDU eLMIS Picking Wizard"

    batch_id = fields.Many2one(
        "cdu.batch",
        required=True,
        readonly=True,
        ondelete="cascade",
    )
    batch_name = fields.Char(related="batch_id.name", readonly=True)
    batch_state = fields.Selection(related="batch_id.state", readonly=True)
    prescription_count = fields.Integer(related="batch_id.prescription_count", readonly=True)
    pickup_date_from = fields.Date(
        related="batch_id.filter_next_drug_pickup_date_from",
        readonly=True,
    )
    subtitle = fields.Char(compute="_compute_subtitle")
    picking_ready = fields.Boolean(compute="_compute_picking_status")
    picking_readiness_message = fields.Text(compute="_compute_picking_status")
    elmis_picking_line_ids = fields.One2many(
        related="batch_id.elmis_picking_line_ids",
        readonly=True,
    )
    elmis_picking_fulfilment_line_ids = fields.One2many(
        related="batch_id.elmis_picking_fulfilment_line_ids",
        readonly=False,
    )

    @api.depends("batch_name", "prescription_count", "pickup_date_from")
    def _compute_subtitle(self):
        for wizard in self:
            parts = []
            if wizard.batch_name:
                parts.append(wizard.batch_name)
            parts.append(_("%s prescriptions") % (wizard.prescription_count or 0))
            if wizard.pickup_date_from:
                parts.append(_("Pickup %s") % fields.Date.to_string(wizard.pickup_date_from))
            wizard.subtitle = " · ".join(parts)

    @api.depends(
        "batch_id.elmis_picking_line_ids.fulfilment_line_ids.selected_stock_option_id",
        "batch_id.elmis_picking_line_ids.fulfilment_line_ids.selected_pack_size",
        "batch_id.elmis_picking_line_ids.fulfilment_line_ids.quantity_picked",
        "batch_id.elmis_picking_line_ids.fulfilment_line_ids.selected_stock_on_hand",
        "batch_id.elmis_picking_line_ids.quantity_to_pick",
    )
    def _compute_picking_status(self):
        for wizard in self:
            errors = (
                wizard.batch_id._get_picking_readiness_errors()
                if wizard.batch_id
                else [_("No batch selected.")]
            )
            wizard.picking_ready = not errors
            wizard.picking_readiness_message = "\n".join(errors)

    def action_confirm(self):
        self.ensure_one()
        batch = self.batch_id
        if batch.picking_confirmed_at:
            batch._ensure_picking_ready()
            batch._link_elmis_lines_to_summary_lines()
            batch.elmis_picking_fulfilment_line_ids._sync_selected_stock_option()
            batch._apply_repeat_fulfilment_results()
            batch._sync_picking_summary_from_elmis_lines()
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Picking selection revised"),
                    "message": _("The picking selection was updated."),
                    "type": "success",
                    "sticky": False,
                    "next": {"type": "ir.actions.client", "tag": "reload"},
                },
            }
        return batch.action_confirm_elmis_picking()
