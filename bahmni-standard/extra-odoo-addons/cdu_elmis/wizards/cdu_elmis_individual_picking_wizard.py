from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class CduElmisIndividualPickingWizard(models.TransientModel):
    _name = "cdu.elmis.individual.picking.wizard"
    _description = "CDU Individual Prescription Picking Wizard"

    resolution_id = fields.Many2one(
        "cdu.picking.patient.resolution",
        required=True,
        ondelete="cascade",
    )
    batch_id = fields.Many2one(
        related="resolution_id.batch_id", readonly=True
    )
    picking_line_id = fields.Many2one(
        related="resolution_id.picking_line_id", readonly=True
    )
    patient_line_id = fields.Many2one(
        related="resolution_id.patient_line_id", readonly=True
    )
    prescription_id = fields.Many2one(
        related="resolution_id.prescription_id", readonly=True
    )
    patient_id = fields.Many2one(
        related="resolution_id.patient_id", readonly=True
    )
    status = fields.Selection(related="resolution_id.status", readonly=True)
    unserved_note = fields.Text(
        related="resolution_id.unserved_note", readonly=False
    )
    required_units = fields.Float(
        related="resolution_id.required_units", readonly=True
    )
    target_days = fields.Integer(
        related="resolution_id.target_days", readonly=True
    )
    supplied_units = fields.Float(
        related="resolution_id.supplied_units", readonly=True
    )
    coverage_days = fields.Float(
        related="resolution_id.coverage_days", readonly=True
    )
    coverage_variance_days = fields.Float(
        related="resolution_id.coverage_variance_days", readonly=True
    )
    distinct_product_count = fields.Integer(
        related="resolution_id.distinct_product_count", readonly=True
    )
    coverage_confirmation_required = fields.Boolean(
        related="resolution_id.coverage_confirmation_required", readonly=True
    )
    confirmed_supplied_days = fields.Integer(
        related="resolution_id.confirmed_supplied_days", readonly=False
    )
    coverage_confirmed = fields.Boolean(
        related="resolution_id.coverage_confirmed", readonly=True
    )
    coverage_confirmed_by = fields.Many2one(
        related="resolution_id.coverage_confirmed_by", readonly=True
    )
    coverage_confirmed_at = fields.Datetime(
        related="resolution_id.coverage_confirmed_at", readonly=True
    )
    allocation_line_ids = fields.One2many(
        related="resolution_id.allocation_line_ids", readonly=False
    )
    progress = fields.Char(compute="_compute_navigation")
    has_previous = fields.Boolean(compute="_compute_navigation")
    has_next = fields.Boolean(compute="_compute_navigation")

    @api.depends("resolution_id", "picking_line_id.resolution_ids")
    def _compute_navigation(self):
        for wizard in self:
            resolutions = wizard._ordered_resolutions()
            current_index = (
                resolutions.ids.index(wizard.resolution_id.id)
                if wizard.resolution_id.id in resolutions.ids
                else 0
            )
            total = len(resolutions)
            wizard.progress = _("Prescription %(current)s of %(total)s") % {
                "current": current_index + 1 if total else 0,
                "total": total,
            }
            wizard.has_previous = current_index > 0
            wizard.has_next = current_index < total - 1

    def _ordered_resolutions(self):
        self.ensure_one()
        return self.picking_line_id.resolution_ids.sorted(
            lambda resolution: (resolution.sequence, resolution.id)
        )

    def _open_action(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Individual Prescription Picking"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_resolution_id": self.resolution_id.id,
                "default_batch_id": self.batch_id.id,
                "default_picking_line_id": self.picking_line_id.id,
            },
        }

    def _validate_current_resolution(self):
        self.ensure_one()
        resolution = self.resolution_id
        if resolution.allocation_line_ids:
            if (
                resolution.coverage_confirmation_required
                and not resolution.coverage_confirmed
            ):
                raise ValidationError(
                    _(
                        "Enter and confirm the total supplied days for this "
                        "mixed-product prescription before continuing."
                    )
                )
            resolution._refresh_status()
            return
        if (
            resolution.status != "unserved"
            or resolution.unserved_reason != "insufficient_stock"
        ):
            raise ValidationError(
                _(
                    "Allocate at least one pack or explicitly mark this "
                    "prescription unserved before continuing."
                )
            )

    def _navigate(self, offset, validate=True):
        self.ensure_one()
        if validate:
            self._validate_current_resolution()
        resolutions = self._ordered_resolutions()
        current_index = resolutions.ids.index(self.resolution_id.id)
        target_index = current_index + offset
        if target_index < 0 or target_index >= len(resolutions):
            return self._open_batch_review()
        wizard = self.create({"resolution_id": resolutions[target_index].id})
        return wizard._open_action()

    def action_previous(self):
        return self._navigate(-1, validate=False)

    def action_next(self):
        return self._navigate(1)

    def action_mark_unserved(self):
        self.ensure_one()
        self.resolution_id.action_mark_unserved()
        return self._open_action()

    def action_review(self):
        self._validate_current_resolution()
        return self._open_batch_review()

    def _open_batch_review(self):
        wizard = self.env["cdu.elmis.picking.wizard"].create(
            {"batch_id": self.batch_id.id, "step": "review"}
        )
        return wizard._open_action()
