from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CduPrescription(models.Model):
    _inherit = "cdu.prescription"

    is_backorder = fields.Boolean(
        string="Back Order",
        default=False,
        copy=False,
        readonly=True,
        index=True,
    )
    backorder_source_prescription_id = fields.Many2one(
        "cdu.prescription",
        string="Balance From Prescription",
        copy=False,
        readonly=True,
        ondelete="restrict",
        index=True,
    )
    backorder_root_prescription_id = fields.Many2one(
        "cdu.prescription",
        string="Original Prescription",
        copy=False,
        readonly=True,
        ondelete="restrict",
        index=True,
    )
    backorder_created_from_batch_id = fields.Many2one(
        "cdu.batch",
        string="Balance From Batch",
        copy=False,
        readonly=True,
        ondelete="restrict",
    )
    backorder_required_days = fields.Integer(
        string="Outstanding Days",
        copy=False,
        readonly=True,
    )
    backorder_component_ids = fields.One2many(
        "cdu.prescription.backorder.component",
        "backorder_prescription_id",
        string="Outstanding Regimen Components",
        readonly=True,
    )
    backorder_prescription_ids = fields.One2many(
        "cdu.prescription",
        "backorder_source_prescription_id",
        string="Balance Prescriptions",
        readonly=True,
    )
    has_backorders = fields.Boolean(
        compute="_compute_has_backorders",
        string="Has Balance Prescriptions",
    )

    _sql_constraints = [
        (
            "unique_backorder_source_prescription",
            "unique(backorder_source_prescription_id)",
            "Only one balance prescription can be created from a prescription.",
        ),
    ]

    @api.depends("backorder_prescription_ids")
    def _compute_has_backorders(self):
        for prescription in self:
            prescription.has_backorders = bool(
                prescription.backorder_prescription_ids
            )

    _POST_DISPENSING_STATES = frozenset(
        (
            "awaiting_bagging_qa",
            "awaiting_boxing",
            "awaiting_dispatch",
            "dispatched",
        )
    )

    def action_open_dispensing(self):
        self.ensure_one()
        self._ensure_cdu_groups(
            "cdu_prescription.group_cdu_dispensing_officer",
            "cdu_prescription.group_cdu_admin",
        )
        self._ensure_states(("awaiting_dispensing",))
        dispense = self.env["cdu.dispense"].search(
            [("prescription_id", "=", self.id)],
            limit=1,
        )
        if not dispense:
            dispense = self.env["cdu.dispense"].create({"prescription_id": self.id})
        auth_action = dispense._auto_refresh_production_stock(silent=False)
        if auth_action:
            return auth_action
        return dispense._action_open()

    def action_open_bagging_qa(self):
        self.ensure_one()
        self._ensure_cdu_groups(
            "cdu_prescription.group_cdu_dispensing_officer",
            "cdu_prescription.group_cdu_admin",
        )
        self._ensure_states(("awaiting_bagging_qa",))
        dispense = self.env["cdu.dispense"].search(
            [("prescription_id", "=", self.id)],
            limit=1,
        )
        if not dispense:
            raise UserError(_("This prescription has no dispense record yet."))
        if dispense.state != "confirmed":
            raise UserError(_("Dispensing must be confirmed before Bagging / QA."))
        qa = self.env["cdu.bagging.qa"].search(
            [("prescription_id", "=", self.id)],
            limit=1,
        )
        if not qa:
            qa = self.env["cdu.bagging.qa"].create(
                {
                    "prescription_id": self.id,
                    "dispense_id": dispense.id,
                }
            )
        return qa._action_open()

    def action_reprint_dispensing_labels(self):
        self.ensure_one()
        self._ensure_cdu_groups(
            "cdu_prescription.group_cdu_dispensing_officer",
            "cdu_prescription.group_cdu_admin",
        )
        if self.state not in self._POST_DISPENSING_STATES:
            raise UserError(
                _("Labels can only be reprinted after dispensing has been confirmed.")
            )
        dispense = self.env["cdu.dispense"].search(
            [("prescription_id", "=", self.id)],
            limit=1,
        )
        if not dispense or dispense.state != "confirmed":
            raise UserError(_("This prescription has no confirmed dispensing record."))
        return dispense.action_print_labels()


class CduPrescriptionBackorderComponent(models.Model):
    _name = "cdu.prescription.backorder.component"
    _description = "CDU Prescription Back-order Regimen Component"
    _order = "sequence, id"

    backorder_prescription_id = fields.Many2one(
        "cdu.prescription",
        required=True,
        ondelete="cascade",
        index=True,
    )
    source_group_id = fields.Many2one(
        "cdu.picking.bulk.group",
        string="Source Regimen Component",
        readonly=True,
        ondelete="set null",
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True, readonly=True)
    selected_products = fields.Char(readonly=True)
    daily_units = fields.Integer(readonly=True)
    target_days = fields.Integer(readonly=True)
    supplied_days = fields.Float(readonly=True)
    outstanding_days = fields.Float(readonly=True)
    required_units = fields.Float(readonly=True)
    supplied_units = fields.Float(readonly=True)
    outstanding_units = fields.Float(readonly=True)

    _sql_constraints = [
        (
            "unique_bo_source_component",
            "unique(backorder_prescription_id, source_group_id)",
            "A source regimen component can only appear once on a balance prescription.",
        ),
    ]
