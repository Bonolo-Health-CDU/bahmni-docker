from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CduElmisPickingWizard(models.TransientModel):
    _name = "cdu.elmis.picking.wizard"
    _description = "CDU eLMIS Picking Wizard"

    batch_id = fields.Many2one(
        "cdu.batch",
        required=True,
        readonly=True,
        ondelete="cascade",
    )
    step = fields.Selection(
        [
            ("overview", "Regimen Overview"),
            ("review", "Final Review"),
        ],
        required=True,
        default="overview",
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
        readonly=True,
    )
    patient_allocation_ids = fields.One2many(
        related="batch_id.patient_allocation_ids",
        readonly=True,
    )
    picking_resolution_ids = fields.One2many(
        related="batch_id.picking_resolution_ids",
        readonly=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.mapped("batch_id.elmis_picking_line_ids")._ensure_patient_resolutions()
        return records

    def _open_action(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Generate Picking List"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
            "context": {"default_batch_id": self.batch_id.id},
        }

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
            raise UserError(_("Confirmed picking allocations are locked."))
        return batch.action_confirm_elmis_picking()

    def action_review(self):
        self.ensure_one()
        self.step = "review"
        return self._open_action()

    def action_back_to_overview(self):
        self.ensure_one()
        self.step = "overview"
        return self._open_action()
