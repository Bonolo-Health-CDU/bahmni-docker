import math

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class CduPickingLine(models.Model):
    _name = "cdu.picking.line"
    _description = "CDU eLMIS Picking Line"
    _order = "batch_id, openmrs_drug_name, id"
    _rec_name = "openmrs_drug_name"

    batch_id = fields.Many2one(
        "cdu.batch",
        required=True,
        ondelete="cascade",
        index=True,
    )
    prescription_id = fields.Many2one(
        "cdu.prescription",
        ondelete="set null",
        index=True,
    )
    prescription_item_id = fields.Many2one(
        "cdu.batch.patient.line",
        string="Prescription Item",
        ondelete="set null",
        index=True,
    )
    summary_line_id = fields.Many2one(
        "cdu.batch.picking.line",
        string="Picking Summary Line",
        ondelete="cascade",
        index=True,
    )
    prescription_count = fields.Integer(readonly=True)
    openmrs_drug_name = fields.Char(string="Regimen", required=True)
    openmrs_drug_uuid = fields.Char(
        help="Retained for Phase 2 product mapping from OpenMRS drugs to OpenLMIS orderables.",
    )
    selected_orderable_code = fields.Char(string="eLMIS Orderable Code")
    selected_orderable_id = fields.Char(string="eLMIS Orderable UUID")
    selected_orderable_name = fields.Char(string="Fulfil With eLMIS Product")
    selected_lot = fields.Char(string="Batch Number")
    selected_lot_id = fields.Char(string="eLMIS Lot UUID")
    selected_lot_expiry = fields.Date(string="Expiry")
    selected_stock_option_id = fields.Many2one(
        "cdu.elmis.stock.option",
        string="Fulfil With",
        domain="[('batch_id', '=', batch_id), ('stock_on_hand', '>', 0)]",
    )
    selected_stock_on_hand = fields.Integer(string="Available Packs", readonly=True)
    available_quantity = fields.Float(
        string="Available Quantity",
        compute="_compute_stock_quantities",
    )
    picked_quantity = fields.Float(
        string="Picked Quantity",
        compute="_compute_stock_quantities",
    )
    remaining_quantity = fields.Float(
        string="Remaining Quantity",
        compute="_compute_stock_quantities",
    )
    remaining_packs_to_pick = fields.Float(
        string="Remaining Packs",
        compute="_compute_stock_quantities",
    )
    pack_size = fields.Integer(
        string="Pack Size",
        compute="_compute_report_quantity_fields",
    )
    required_units = fields.Float(
        string="Required Units/Tablets",
        compute="_compute_report_quantity_fields",
    )
    packs_to_pick = fields.Float(
        string="Packs/Bottles To Pick",
        compute="_compute_report_quantity_fields",
    )
    estimated_available_repeats = fields.Char(
        string="Estimated Available Repeats",
        compute="_compute_repeat_report_fields",
    )
    repeats_to_dispense = fields.Char(
        string="Repeats To Dispense",
        compute="_compute_repeat_report_fields",
    )
    coverage_days = fields.Char(
        string="Coverage Days",
        compute="_compute_repeat_report_fields",
    )
    quantity_to_pick = fields.Float(
        string="Required Packs",
        required=True
    )
    quantity_picked = fields.Float(string="Picked Packs")
    allocation_mode = fields.Selection(
        [
            ("bulk", "Regimen Components"),
            ("individual", "Individual"),
        ],
        string="Allocation Mode",
        required=True,
        default="bulk",
        index=True,
    )
    regimen_component_mode = fields.Boolean(
        string="Regimen Component Allocation",
        default=True,
        copy=False,
        help=(
            "When enabled, every component is required "
            "and every prescription is allocated from every component."
        ),
    )
    resolution_ids = fields.One2many(
        "cdu.picking.patient.resolution",
        "picking_line_id",
        string="Prescription Resolutions",
    )
    patient_allocation_ids = fields.One2many(
        "cdu.picking.patient.allocation",
        "picking_line_id",
        string="Exact Prescription Allocations",
    )
    bulk_group_ids = fields.One2many(
        "cdu.picking.bulk.group",
        "picking_line_id",
        string="Regimen Components",
    )
    resolved_prescription_count = fields.Integer(
        compute="_compute_allocation_progress"
    )
    allocation_progress = fields.Char(compute="_compute_allocation_progress")
    allocation_status = fields.Selection(
        [
            ("not_started", "Not Started"),
            ("in_progress", "In Progress"),
            ("ready", "Ready"),
            ("locked", "Locked"),
        ],
        compute="_compute_allocation_progress",
    )
    fulfilment_line_ids = fields.One2many(
        "cdu.picking.fulfilment.line",
        "picking_line_id",
        string="eLMIS Fulfilment Lines",
    )
    total_quantity_picked = fields.Float(
        string="Total Picked Packs",
        compute="_compute_total_quantity_picked",
    )

    _sql_constraints = [
        (
            "unique_prescription_item",
            "unique(prescription_item_id)",
            "Only one eLMIS picking line is allowed per prescription item.",
        ),
        (
            "unique_summary_line",
            "unique(summary_line_id)",
            "Only one eLMIS picking line is allowed per picking summary line.",
        )
    ]

    @api.constrains("quantity_to_pick", "quantity_picked")
    def _check_quantities(self):
        for line in self:
            if line.quantity_to_pick < 0:
                raise ValidationError(_("Quantity to pick cannot be negative."))
            if line.quantity_picked < 0:
                raise ValidationError(_("Quantity picked cannot be negative."))

    @api.depends("fulfilment_line_ids.quantity_picked")
    def _compute_total_quantity_picked(self):
        for line in self:
            line.total_quantity_picked = sum(line.fulfilment_line_ids.mapped("quantity_picked"))

    @api.depends(
        "resolution_ids.status",
        "resolution_ids.locked",
        "prescription_count",
        "batch_id.picking_confirmed_at",
    )
    def _compute_allocation_progress(self):
        for line in self:
            total = len(line.resolution_ids)
            resolved = len(
                line.resolution_ids.filtered(
                    lambda resolution: resolution.status
                    in ("full", "partial", "unserved")
                )
            )
            line.resolved_prescription_count = resolved
            line.allocation_progress = _("%(resolved)s of %(total)s") % {
                "resolved": resolved,
                "total": total or line.prescription_count or 0,
            }
            if line.batch_id.picking_confirmed_at:
                line.allocation_status = "locked"
            elif not resolved:
                line.allocation_status = "not_started"
            elif resolved < total:
                line.allocation_status = "in_progress"
            else:
                line.allocation_status = "ready"

    @api.depends(
        "fulfilment_line_ids.quantity_picked",
        "fulfilment_line_ids.selected_stock_on_hand",
        "fulfilment_line_ids.selected_pack_size",
        "fulfilment_line_ids.selected_stock_option_id.pack_size",
        "summary_line_id.pack_size",
        "required_units",
        "pack_size",
    )
    def _compute_stock_quantities(self):
        for line in self:
            pack_size = line._get_effective_pack_size()
            available_packs = sum(line.fulfilment_line_ids.mapped("selected_stock_on_hand"))
            picked_packs = sum(line.fulfilment_line_ids.mapped("quantity_picked"))
            line.available_quantity = available_packs * pack_size
            line.picked_quantity = picked_packs * pack_size
            line.remaining_quantity = max((line.required_units or 0.0) - line.picked_quantity, 0.0)
            line.remaining_packs_to_pick = max((line._calculate_required_packs() or 0.0) - picked_packs, 0.0)

    @api.depends(
        "quantity_to_pick",
        "summary_line_id.pack_size",
        "selected_stock_option_id.pack_size",
        "fulfilment_line_ids.selected_pack_size",
        "fulfilment_line_ids.selected_stock_option_id.pack_size",
        "summary_line_id.total_tablets",
        "batch_id.patient_picking_line_ids.effective_repeat_days",
        "batch_id.patient_picking_line_ids.required_units",
        "batch_id.patient_picking_line_ids.daily_dose",
        "batch_id.patient_picking_line_ids.drug_name",
        "batch_id.patient_picking_line_ids.product_id",
    )
    def _compute_report_quantity_fields(self):
        for line in self:
            matching_patient_lines = line._get_matching_patient_picking_lines()
            pack_size = (
                line.selected_stock_option_id.pack_size
                or next(
                    (
                        option.pack_size
                        for option in line.fulfilment_line_ids.mapped(
                            "selected_stock_option_id"
                        )
                        if option.pack_size
                    ),
                    0,
                )
                or line.summary_line_id.pack_size
                or 0
            )
            required_units = sum(matching_patient_lines.mapped("required_units"))

            line.pack_size = pack_size
            line.required_units = (
                required_units or line.summary_line_id.total_tablets or 0.0
            )
            line.packs_to_pick = line._calculate_required_packs()

    def _get_effective_pack_size(self):
        self.ensure_one()
        return (
            self.selected_stock_option_id.pack_size
            or next(
                (
                    line.selected_pack_size or line.selected_stock_option_id.pack_size
                    for line in self.fulfilment_line_ids
                    if line.selected_pack_size or line.selected_stock_option_id.pack_size
                ),
                0,
            )
            or self.summary_line_id.pack_size
            or 0
        )

    def _calculate_required_packs(self):
        self.ensure_one()
        required_units = self.required_units or self.summary_line_id.total_tablets or 0.0
        pack_size = self._get_effective_pack_size()
        if required_units > 0 and pack_size > 0:
            return math.ceil(required_units / pack_size)
        return self.quantity_to_pick or 0.0

    @api.depends(
        "batch_id.patient_picking_line_ids.effective_repeat_days",
        "batch_id.patient_picking_line_ids.drug_name",
        "batch_id.patient_picking_line_ids.product_id",
        "summary_line_id.product_id",
        "openmrs_drug_name",
    )
    def _compute_repeat_report_fields(self):
        for line in self:
            matching_patient_lines = line._get_matching_patient_picking_lines()
            total_coverage_days = sum(matching_patient_lines.mapped("effective_repeat_days"))

            if total_coverage_days:
                estimated_repeats = total_coverage_days / 30.0
                repeat_value = (
                    str(int(estimated_repeats))
                    if estimated_repeats.is_integer()
                    else ("%.2f" % estimated_repeats).rstrip("0").rstrip(".")
                )
                line.estimated_available_repeats = repeat_value
                line.coverage_days = str(int(total_coverage_days))
            else:
                line.estimated_available_repeats = False
                line.coverage_days = False

            # No persisted CDU-selected repeat field exists yet. Keep this blank
            # rather than inventing a selected value in the printed report.
            line.repeats_to_dispense = False

    def _get_matching_patient_picking_lines(self):
        self.ensure_one()
        patient_lines = self.batch_id.patient_picking_line_ids
        if not patient_lines:
            return patient_lines

        product = self.summary_line_id.product_id
        if product:
            product_lines = patient_lines.filtered(
                lambda patient_line: patient_line.product_id == product
            )
            if product_lines:
                return product_lines

        regimen_name = (self.openmrs_drug_name or "").strip().lower()
        if not regimen_name:
            return self.env["cdu.batch.patient.line"].browse()
        return patient_lines.filtered(
            lambda patient_line: (patient_line.drug_name or "").strip().lower() == regimen_name
        )

    def _get_patient_fulfilment_allocations(self, patient_line):
        """Allocate this patient's picked packs across the selected stock lots."""
        self.ensure_one()
        if not patient_line:
            return {}

        exact_allocations = self.patient_allocation_ids.filtered(
            lambda allocation: allocation.patient_line_id == patient_line
        )
        if exact_allocations:
            result = {}
            for allocation in exact_allocations:
                fulfilment = self.fulfilment_line_ids.filtered(
                    lambda line: line.selected_stock_option_id
                    == allocation.stock_option_id
                )[:1]
                if fulfilment:
                    result[fulfilment.id] = allocation.quantity_packs
            return result

        pack_size = self._get_effective_pack_size()
        if pack_size <= 0:
            return {}

        patient_lines = self._get_matching_patient_picking_lines().sorted("id")
        preceding_packs = 0.0
        for candidate in patient_lines:
            if candidate == patient_line:
                break
            preceding_packs += (
                candidate.picked_units or candidate.picked_quantity or 0.0
            ) / pack_size

        remaining_packs = (
            patient_line.picked_units or patient_line.picked_quantity or 0.0
        ) / pack_size
        allocations = {}
        for fulfilment_line in self.fulfilment_line_ids.sorted("id"):
            lot_packs = fulfilment_line.quantity_picked or 0.0
            if preceding_packs >= lot_packs:
                preceding_packs -= lot_packs
                continue

            available_packs = lot_packs - preceding_packs
            preceding_packs = 0.0
            allocated_packs = min(remaining_packs, available_packs)
            if allocated_packs > 0:
                allocations[fulfilment_line.id] = allocated_packs
                remaining_packs -= allocated_packs
            if remaining_packs <= 0:
                break
        return allocations

    def _ensure_default_fulfilment_line(self):
        # Fulfilment rows are aggregate, read-only movement rows in the new
        # workflow. Legacy callers may still request defaults for historical
        # records, but draft prescription-level allocation starts empty.
        if not self.env.context.get("cdu_create_legacy_fulfilment_default"):
            return
        for line in self:
            if line.fulfilment_line_ids:
                continue
            values = {
                "batch_id": line.batch_id.id,
                "picking_line_id": line.id,
                "quantity_picked": line.quantity_picked or line.quantity_to_pick or 0,
            }
            if line.selected_stock_option_id:
                values["selected_stock_option_id"] = line.selected_stock_option_id.id
            self.env["cdu.picking.fulfilment.line"].create(values)

    def _ensure_patient_resolutions(self):
        Resolution = self.env["cdu.picking.patient.resolution"]
        for line in self:
            existing_patient_ids = set(line.resolution_ids.mapped("patient_line_id").ids)
            values = []
            for sequence, patient_line in enumerate(
                line._get_matching_patient_picking_lines().sorted("id"), start=1
            ):
                if patient_line.id in existing_patient_ids:
                    continue
                values.append(
                    {
                        "batch_id": line.batch_id.id,
                        "picking_line_id": line.id,
                        "patient_line_id": patient_line.id,
                        "sequence": sequence * 10,
                    }
                )
            if values:
                Resolution.create(values)

    def _has_draft_allocations(self):
        self.ensure_one()
        return bool(
            self.patient_allocation_ids
            or self.bulk_group_ids
            or self.resolution_ids.filtered(
                lambda resolution: resolution.status != "draft"
                or resolution.unserved_note
            )
        )

    def _switch_allocation_mode(self, mode):
        if mode not in ("bulk", "individual"):
            raise ValidationError(_("Unknown allocation mode."))
        for line in self:
            if line.batch_id.picking_confirmed_at:
                raise ValidationError(_("Confirmed picking allocations are locked."))
            if line.allocation_mode == mode:
                continue
            line.patient_allocation_ids.unlink()
            line.bulk_group_ids.unlink()
            line.resolution_ids.write(
                {
                    "status": "draft",
                    "unserved_reason": False,
                    "unserved_note": False,
                }
            )
            line.with_context(cdu_allow_mode_switch=True).allocation_mode = mode
        return True

    def action_use_bulk_mode(self):
        self._switch_allocation_mode("bulk")
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_use_individual_mode(self):
        self._switch_allocation_mode("individual")
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_open_allocation(self):
        self.ensure_one()
        self._ensure_patient_resolutions()
        if self.allocation_mode == "individual":
            first = self.resolution_ids.sorted(
                lambda resolution: (resolution.sequence, resolution.id)
            )[:1]
            if not first:
                raise ValidationError(_("No prescriptions were found for this regimen."))
            wizard = self.env["cdu.elmis.individual.picking.wizard"].create(
                {"resolution_id": first.id}
            )
            return wizard._open_action()
        group = self._get_or_create_primary_bulk_group()
        return group._open_allocation_action()

    def _get_or_create_primary_bulk_group(self):
        self.ensure_one()
        self._ensure_patient_resolutions()
        group = self.bulk_group_ids.sorted(
            lambda candidate: (candidate.sequence, candidate.id)
        )[:1]
        if not group:
            group = self.env["cdu.picking.bulk.group"].create(
                {
                    "batch_id": self.batch_id.id,
                    "picking_line_id": self.id,
                }
            )
        else:
            group._populate_prescriptions()
        return group

    def action_open_bulk_allocation_overview(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("%s - Regimen Component Allocation")
            % self.openmrs_drug_name,
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "view_id": self.env.ref(
                "cdu_elmis.view_cdu_picking_line_bulk_allocation_form"
            ).id,
            "target": "current",
            "context": {
                "default_batch_id": self.batch_id.id,
                "default_picking_line_id": self.id,
            },
        }

    def action_confirm_picking(self):
        self.ensure_one()
        result = self.batch_id.action_confirm_elmis_picking()
        if result.get("res_model") == "cdu.elmis.auth.wizard":
            return result

        action = self.env["ir.actions.actions"]._for_xml_id(
            "cdu_prescription.action_cdu_batch"
        )
        action.update(
            {
                "res_id": self.batch_id.id,
                "views": [
                    (
                        self.env.ref(
                            "cdu_prescription.view_cdu_batch_form"
                        ).id,
                        "form",
                    )
                ],
                "view_mode": "form",
                "target": "current",
            }
        )
        return action

    def action_back_to_allocation_overview(self):
        self.ensure_one()
        wizard = self.env["cdu.elmis.picking.wizard"].create(
            {"batch_id": self.batch_id.id, "step": "overview"}
        )
        return wizard._open_action()

    def write(self, vals):
        if (
            "allocation_mode" in vals
            and not self.env.context.get("cdu_allow_mode_switch")
        ):
            changed = self.filtered(
                lambda line: line.allocation_mode != vals["allocation_mode"]
            )
            if any(line._has_draft_allocations() for line in changed):
                raise ValidationError(
                    _(
                        "Use the mode-switch button so the affected regimen's "
                        "draft allocations can be cleared safely."
                    )
                )
        result = super().write(vals)
        if "selected_stock_option_id" in vals:
            self._sync_selected_stock_option()
            self.mapped("batch_id")._sync_picking_quantities_from_elmis_pack_sizes()
        return result

    @api.onchange("selected_stock_option_id")
    def _onchange_selected_stock_option_id(self):
        for line in self:
            line._sync_selected_stock_option()

    def _sync_selected_stock_option(self):
        for line in self:
            option = line.selected_stock_option_id
            if not option:
                line.selected_orderable_code = False
                line.selected_orderable_id = False
                line.selected_orderable_name = False
                line.selected_lot = False
                line.selected_lot_id = False
                line.selected_lot_expiry = False
                line.selected_stock_on_hand = 0
                continue
            line.selected_orderable_code = option.orderable_code
            line.selected_orderable_id = option.orderable_id
            line.selected_orderable_name = option.orderable_name
            line.selected_lot = option.lot
            line.selected_lot_id = option.lot_id
            line.selected_lot_expiry = option.expiration_date
            line.selected_stock_on_hand = option.stock_on_hand
 
