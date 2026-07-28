import math
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


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
    allocation_strategy = fields.Selection(
        [
            ("balanced_fefo", "Balanced FEFO"),
            ("manual", "Manual Override"),
        ],
        string="Allocation Strategy",
        required=True,
        default="balanced_fefo",
        copy=False,
        index=True,
    )
    manual_override_reason = fields.Text(
        string="Manual Override Reason",
        copy=False,
        help=(
            "Required when a user confirms an allocation that differs from the "
            "Balanced FEFO proposal."
        ),
    )
    manual_override_by = fields.Many2one(
        "res.users",
        string="Manual Override By",
        readonly=True,
        copy=False,
    )
    manual_override_at = fields.Datetime(
        string="Manual Override At",
        readonly=True,
        copy=False,
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
            line.with_context(cdu_allow_mode_switch=True).write(
                {
                    "allocation_mode": mode,
                    "allocation_strategy": "balanced_fefo",
                    "manual_override_reason": False,
                    "manual_override_by": False,
                    "manual_override_at": False,
                }
            )
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

    def _bulk_groups_are_ready(self):
        self.ensure_one()
        groups = self.bulk_group_ids
        resolution_ids = set(self.resolution_ids.ids)
        return bool(
            groups
            and all(
                group.lot_ids
                and set(group.member_ids.mapped("resolution_id").ids)
                == resolution_ids
                for group in groups
            )
        )

    def _allocation_start_date(self, resolution):
        self.ensure_one()
        today = fields.Date.context_today(self)
        pickup_date = resolution.prescription_id.next_drug_pickup_date
        return max(pickup_date, today) if pickup_date else today

    def _lot_consumption_end_date(self, resolution, coverage_days):
        self.ensure_one()
        days = max(int(math.ceil(coverage_days or 0)), 1)
        return self._allocation_start_date(resolution) + timedelta(days=days - 1)

    def _lot_can_be_consumed_before_expiry(
        self,
        lot,
        resolution,
        coverage_days,
    ):
        self.ensure_one()
        if not lot.expiration_date:
            return True
        today = fields.Date.context_today(self)
        if lot.expiration_date < today:
            return False
        return lot.expiration_date >= self._lot_consumption_end_date(
            resolution,
            coverage_days,
        )

    def _selected_lots_are_unique_between_components(self):
        self.ensure_one()
        option_group_ids = {}
        for group in self.bulk_group_ids:
            for lot in group.lot_ids:
                option_group_ids.setdefault(lot.stock_option_id.id, set()).add(
                    group.id
                )
        return all(len(group_ids) == 1 for group_ids in option_group_ids.values())

    def _balanced_stock_state(self):
        """Return selected FEFO stock capacity excluding this regimen's draft."""
        self.ensure_one()
        groups = self.bulk_group_ids.sorted(
            lambda group: (group.sequence, group.id)
        )
        options = groups.mapped("lot_ids.stock_option_id")
        outside_allocations = self.env[
            "cdu.picking.patient.allocation"
        ].search(
            [
                ("batch_id", "=", self.batch_id.id),
                ("stock_option_id", "in", options.ids),
                ("picking_line_id", "!=", self.id),
            ]
        )
        allocated_elsewhere = {}
        for allocation in outside_allocations:
            option_id = allocation.stock_option_id.id
            allocated_elsewhere[option_id] = (
                allocated_elsewhere.get(option_id, 0)
                + allocation.quantity_packs
            )

        option_remaining = {
            option.id: max(
                option.stock_on_hand - allocated_elsewhere.get(option.id, 0),
                0,
            )
            for option in options
        }
        lot_remaining = {}
        lots_by_group = {}
        no_expiry = fields.Date.to_date("9999-12-31")
        today = fields.Date.context_today(self)
        for group in groups:
            lots = group.lot_ids.filtered(
                lambda lot: (
                    lot.pack_size > 0
                    and lot.quantity_packs > 0
                    and (
                        not lot.expiration_date
                        or lot.expiration_date >= today
                    )
                )
            ).sorted(
                lambda lot: (
                    lot.expiration_date or no_expiry,
                    lot.sequence,
                    lot.id,
                )
            )
            lots_by_group[group.id] = lots
            for lot in lots:
                lot_remaining[lot.id] = min(
                    lot.quantity_packs,
                    option_remaining.get(lot.stock_option_id.id, 0),
                )
        return groups, lots_by_group, lot_remaining, option_remaining

    def _choose_balanced_lot(
        self,
        group,
        resolution,
        member,
        supplied_units,
        lots_by_group,
        lot_remaining,
        option_remaining,
    ):
        """Choose one unopened pack using safe FEFO and minimum excess."""
        self.ensure_one()
        target_days = resolution.target_days or 0
        candidates = []
        no_expiry = fields.Date.to_date("9999-12-31")
        for lot in lots_by_group.get(group.id, self.env["cdu.picking.bulk.lot"]):
            option_id = lot.stock_option_id.id
            if (
                lot_remaining.get(lot.id, 0) <= 0
                or option_remaining.get(option_id, 0) <= 0
                or lot.pack_size <= 0
            ):
                continue
            new_coverage = (
                (supplied_units + lot.pack_size) / member.daily_units
                if member.daily_units > 0
                else 0
            )
            if not self._lot_can_be_consumed_before_expiry(
                lot,
                resolution,
                new_coverage,
            ):
                continue
            candidates.append(
                (
                    lot.expiration_date or no_expiry,
                    max(new_coverage - target_days, 0),
                    lot.pack_size,
                    lot.sequence,
                    lot.id,
                    lot,
                )
            )
        return min(candidates, default=(None,) * 6)[5]

    def _build_balanced_distribution_plan(self):
        """Build a deterministic max-min whole-pack plan across all components.

        Every step assigns one unopened pack from every regimen component.
        The next patient chosen is always the one with the lowest overall
        coverage; patient sequence is only the final tie-breaker. A step is
        atomic, so surplus stock from one component is retained when another
        component cannot complete the pack bundle.
        """
        self.ensure_one()
        self._ensure_patient_resolutions()
        if (
            self.allocation_mode != "bulk"
            or not self._bulk_groups_are_ready()
            or not self._selected_lots_are_unique_between_components()
        ):
            return {}

        groups, lots_by_group, lot_remaining, option_remaining = (
            self._balanced_stock_state()
        )
        resolutions = self.resolution_ids.sorted(
            lambda resolution: (resolution.sequence, resolution.id)
        )
        member_by_key = {
            (member.resolution_id.id, member.group_id.id): member
            for member in groups.mapped("member_ids")
        }
        supplied_units = {
            (resolution.id, group.id): 0
            for resolution in resolutions
            for group in groups
        }
        plan = {}
        epsilon = 0.0001
        maximum_steps = sum(lot_remaining.values())

        for _step in range(maximum_steps):
            patient_states = []
            for resolution in resolutions:
                target_days = resolution.target_days or 0
                component_coverages = []
                valid = True
                for group in groups:
                    member = member_by_key.get((resolution.id, group.id))
                    if not member or member.daily_units <= 0:
                        valid = False
                        break
                    component_coverages.append(
                        supplied_units[(resolution.id, group.id)]
                        / member.daily_units
                    )
                if not valid or not component_coverages:
                    continue
                overall_coverage = min(component_coverages)
                if overall_coverage + epsilon >= target_days:
                    continue
                patient_states.append(
                    (
                        overall_coverage,
                        resolution.sequence,
                        resolution.id,
                        resolution,
                        component_coverages,
                    )
                )

            if not patient_states:
                break

            progress = False
            for (
                _overall_coverage,
                _sequence,
                _resolution_id,
                resolution,
                _component_coverages,
            ) in sorted(patient_states):
                choices = []
                provisional_lot_remaining = dict(lot_remaining)
                provisional_option_remaining = dict(option_remaining)
                for group in groups:
                    member = member_by_key[(resolution.id, group.id)]
                    lot = self._choose_balanced_lot(
                        group,
                        resolution,
                        member,
                        supplied_units[(resolution.id, group.id)],
                        lots_by_group,
                        provisional_lot_remaining,
                        provisional_option_remaining,
                    )
                    if not lot:
                        choices = []
                        break
                    choices.append((group, member, lot))
                    provisional_lot_remaining[lot.id] -= 1
                    provisional_option_remaining[lot.stock_option_id.id] -= 1

                if not choices:
                    continue

                for group, member, lot in choices:
                    member_plan = plan.setdefault(member.id, {})
                    member_plan[lot.id] = member_plan.get(lot.id, 0) + 1
                    supplied_units[(resolution.id, group.id)] += lot.pack_size
                    lot_remaining[lot.id] -= 1
                    option_remaining[lot.stock_option_id.id] -= 1
                progress = True
                break

            if not progress:
                break

        return plan

    def _distribution_signature_from_plan(self, plan):
        self.ensure_one()
        members = {
            member.id: member
            for member in self.bulk_group_ids.mapped("member_ids")
        }
        lots = {
            lot.id: lot
            for lot in self.bulk_group_ids.mapped("lot_ids")
        }
        return {
            (
                members[member_id].resolution_id.id,
                members[member_id].group_id.id,
                lots[lot_id].stock_option_id.id,
            ): quantity
            for member_id, member_plan in plan.items()
            for lot_id, quantity in member_plan.items()
            if member_id in members and lot_id in lots and quantity > 0
        }

    def _actual_bulk_distribution_signature(self):
        self.ensure_one()
        return {
            (
                allocation.resolution_id.id,
                allocation.bulk_group_id.id,
                allocation.stock_option_id.id,
            ): allocation.quantity_packs
            for allocation in self.patient_allocation_ids.filtered(
                lambda allocation: allocation.allocation_method == "bulk"
            )
        }

    def _manual_override_is_required(self):
        self.ensure_one()
        if self.allocation_strategy != "manual":
            return False
        recommended = self._distribution_signature_from_plan(
            self._build_balanced_distribution_plan()
        )
        return self._actual_bulk_distribution_signature() != recommended

    def _finalize_bulk_distribution(self, groups_ready=None):
        self.ensure_one()
        groups_ready = (
            self._bulk_groups_are_ready()
            if groups_ready is None
            else groups_ready
        )
        resolutions = self.resolution_ids
        resolutions._reset_coverage_confirmation()
        for resolution in resolutions:
            if resolution.allocation_line_ids:
                resolution._refresh_status()
            elif groups_ready:
                resolution.with_context(
                    cdu_reset_coverage_confirmation=True
                ).write(
                    {
                        "status": "unserved",
                        "unserved_reason": "insufficient_stock",
                        "unserved_note": False,
                        "confirmed_supplied_days": 0,
                        "coverage_confirmed": False,
                    }
                )
            else:
                resolution.with_context(
                    cdu_reset_coverage_confirmation=True
                ).write(
                    {
                        "status": "draft",
                        "unserved_reason": False,
                        "unserved_note": False,
                        "confirmed_supplied_days": 0,
                        "coverage_confirmed": False,
                    }
                )
        resolutions._sync_patient_supply_calculations()
        self.batch_id._sync_fulfilment_lines_from_allocations()

    def _replace_bulk_distribution(self, plan):
        self.ensure_one()
        groups = self.bulk_group_ids
        members = {member.id: member for member in groups.mapped("member_ids")}
        lots = {lot.id: lot for lot in groups.mapped("lot_ids")}
        existing = self.patient_allocation_ids.filtered(
            lambda allocation: allocation.allocation_method == "bulk"
        )
        if existing:
            existing.with_context(
                cdu_skip_allocation_side_effects=True
            ).unlink()

        values = []
        for member_id, member_plan in plan.items():
            member = members.get(member_id)
            if not member:
                continue
            member.with_context(
                cdu_skip_auto_bulk_recalculation=True
            ).write({"requested_packs": sum(member_plan.values())})
            for lot_id, quantity in member_plan.items():
                lot = lots.get(lot_id)
                if not lot or quantity <= 0:
                    continue
                values.append(
                    {
                        "batch_id": self.batch_id.id,
                        "picking_line_id": self.id,
                        "resolution_id": member.resolution_id.id,
                        "stock_option_id": lot.stock_option_id.id,
                        "quantity_packs": quantity,
                        "daily_units": member.daily_units,
                        "allocation_method": "bulk",
                        "bulk_group_id": member.group_id.id,
                        "bulk_member_id": member.id,
                        "sequence": lot.sequence,
                    }
                )
        if values:
            self.env["cdu.picking.patient.allocation"].with_context(
                cdu_skip_allocation_side_effects=True
            ).create(values)
        for member in groups.mapped("member_ids").filtered(
            lambda candidate: candidate.id not in plan
        ):
            member.with_context(
                cdu_skip_auto_bulk_recalculation=True
            ).write({"requested_packs": 0})

        groups.with_context(
            cdu_skip_auto_bulk_recalculation=True
        ).write({"distribution_stale": not self._bulk_groups_are_ready()})
        self._finalize_bulk_distribution()

    def _apply_balanced_distribution(self):
        for line in self:
            if (
                line.allocation_mode != "bulk"
                or line.batch_id.picking_confirmed_at
                or line.allocation_strategy == "manual"
            ):
                continue
            line._replace_bulk_distribution(
                line._build_balanced_distribution_plan()
            )

    def _bulk_allocation_safety_errors(self, include_completeness=True):
        self.ensure_one()
        errors = []
        today = fields.Date.context_today(self)
        if not self._selected_lots_are_unique_between_components():
            errors.append(
                _(
                    "%s: the same eLMIS lot cannot be selected in more than one "
                    "regimen component."
                )
                % self.openmrs_drug_name
            )

        for group in self.bulk_group_ids:
            selected_by_option = {
                lot.stock_option_id.id: lot for lot in group.lot_ids
            }
            group_allocations = self.patient_allocation_ids.filtered(
                lambda allocation, group=group: (
                    allocation.bulk_group_id == group
                )
            )
            for allocation in group_allocations:
                lot = selected_by_option.get(allocation.stock_option_id.id)
                if not lot:
                    errors.append(
                        _(
                            "%(component)s / %(patient)s: the allocated stock lot "
                            "is not selected for this component."
                        )
                        % {
                            "component": group.name,
                            "patient": (
                                allocation.resolution_id.patient_id.display_name
                            ),
                        }
                    )
                if (
                    allocation.selected_lot_expiry
                    and allocation.selected_lot_expiry < today
                ):
                    errors.append(
                        _(
                            "%(product)s / %(lot)s is expired and cannot be allocated."
                        )
                        % {
                            "product": allocation.selected_orderable_name,
                            "lot": allocation.selected_lot or _("No batch"),
                        }
                    )
            for lot in group.lot_ids:
                allocated = sum(
                    group_allocations.filtered(
                        lambda allocation, lot=lot: (
                            allocation.stock_option_id == lot.stock_option_id
                        )
                    ).mapped("quantity_packs")
                )
                if allocated > lot.quantity_packs:
                    errors.append(
                        _(
                            "%(component)s / %(lot)s: %(allocated)s packs are "
                            "allocated but the selected maximum is %(maximum)s."
                        )
                        % {
                            "component": group.name,
                            "lot": lot.lot or _("No batch"),
                            "allocated": allocated,
                            "maximum": lot.quantity_packs,
                        }
                    )

        for resolution in self.resolution_ids:
            any_allocations = bool(resolution.allocation_line_ids)
            if include_completeness and any_allocations:
                component_pack_counts = [
                    sum(
                        resolution.allocation_line_ids.filtered(
                            lambda allocation, group=group: (
                                allocation.bulk_group_id == group
                            )
                        ).mapped("quantity_packs")
                    )
                    for group in self.bulk_group_ids
                ]
                if len(set(component_pack_counts)) > 1:
                    errors.append(
                        _(
                            "%(patient)s has an incomplete pack bundle; every "
                            "regimen component must have the same number of "
                            "unopened packs."
                        )
                        % {"patient": resolution.patient_id.display_name}
                    )
            for group in self.bulk_group_ids:
                member = group.member_ids.filtered(
                    lambda candidate, resolution=resolution: (
                        candidate.resolution_id == resolution
                    )
                )[:1]
                allocations = resolution.allocation_line_ids.filtered(
                    lambda allocation, group=group: (
                        allocation.bulk_group_id == group
                    )
                ).sorted(
                    lambda allocation: (
                        allocation.selected_lot_expiry
                        or fields.Date.to_date("9999-12-31"),
                        allocation.sequence,
                        allocation.id,
                    )
                )
                if include_completeness and any_allocations and not allocations:
                    errors.append(
                        _(
                            "%(patient)s is missing %(component)s; every partial "
                            "supply must contain all regimen components."
                        )
                        % {
                            "patient": resolution.patient_id.display_name,
                            "component": group.name,
                        }
                    )
                    continue
                if not member or member.daily_units <= 0:
                    continue
                cumulative_units = 0
                for allocation in allocations:
                    cumulative_units += (
                        allocation.quantity_packs
                        * allocation.selected_pack_size
                    )
                    coverage_days = cumulative_units / member.daily_units
                    if (
                        allocation.selected_lot_expiry
                        and allocation.selected_lot_expiry
                        < self._lot_consumption_end_date(
                            resolution,
                            coverage_days,
                        )
                    ):
                        errors.append(
                            _(
                                "%(patient)s / %(product)s / %(lot)s expires "
                                "before the allocated packs are expected to be "
                                "consumed."
                            )
                            % {
                                "patient": resolution.patient_id.display_name,
                                "product": allocation.selected_orderable_name,
                                "lot": allocation.selected_lot or _("No batch"),
                            }
                        )
        return errors

    def _manual_override_errors(self):
        self.ensure_one()
        if self._manual_override_is_required() and not (
            self.manual_override_reason or ""
        ).strip():
            return [
                _(
                    "%s: enter a reason for the manual allocation override "
                    "before confirming picking."
                )
                % self.openmrs_drug_name
            ]
        return []

    def _ensure_bulk_allocation_editable(self):
        self.ensure_one()
        self.batch_id._ensure_batch_workflow_access()
        if self.allocation_mode != "bulk":
            raise ValidationError(
                _("The allocation board is available only for regimen components.")
            )
        if self.batch_id.picking_confirmed_at:
            raise UserError(_("Confirmed picking allocations are locked."))

    def action_reset_balanced_allocation(self):
        self.ensure_one()
        self._ensure_bulk_allocation_editable()
        self.write(
            {
                "allocation_strategy": "balanced_fefo",
                "manual_override_reason": False,
                "manual_override_by": False,
                "manual_override_at": False,
            }
        )
        self._replace_bulk_distribution(
            self._build_balanced_distribution_plan()
        )
        return self.get_allocation_board_data()

    def action_set_manual_override_reason(self, reason):
        self.ensure_one()
        self._ensure_bulk_allocation_editable()
        clean_reason = (reason or "").strip()
        self.write(
            {
                "manual_override_reason": clean_reason or False,
                "manual_override_by": (
                    self.env.user.id if clean_reason else False
                ),
                "manual_override_at": (
                    fields.Datetime.now() if clean_reason else False
                ),
            }
        )
        return self.get_allocation_board_data()

    def action_update_allocation_board(self, changes):
        """Apply whole-pack manual changes atomically and return fresh board data."""
        self.ensure_one()
        self._ensure_bulk_allocation_editable()
        if not isinstance(changes, list) or not changes:
            raise ValidationError(_("No allocation changes were supplied."))

        resolutions = {record.id: record for record in self.resolution_ids}
        groups = {record.id: record for record in self.bulk_group_ids}
        members = {
            (member.resolution_id.id, member.group_id.id): member
            for member in self.bulk_group_ids.mapped("member_ids")
        }
        lots = {
            (lot.group_id.id, lot.stock_option_id.id): lot
            for lot in self.bulk_group_ids.mapped("lot_ids")
        }
        actual = {
            (
                allocation.resolution_id.id,
                allocation.bulk_group_id.id,
                allocation.stock_option_id.id,
            ): allocation.quantity_packs
            for allocation in self.patient_allocation_ids.filtered(
                lambda allocation: allocation.allocation_method == "bulk"
            )
        }

        normalized_changes = []
        for change in changes:
            try:
                resolution_id = int(change.get("resolution_id"))
                group_id = int(change.get("group_id"))
                stock_option_id = int(change.get("stock_option_id"))
                delta = int(change.get("delta"))
            except (AttributeError, TypeError, ValueError):
                raise ValidationError(_("Every allocation change must use valid IDs and a whole-pack quantity."))
            if not delta:
                continue
            resolution = resolutions.get(resolution_id)
            group = groups.get(group_id)
            member = members.get((resolution_id, group_id))
            lot = lots.get((group_id, stock_option_id))
            if not resolution or not group or not member or not lot:
                raise ValidationError(
                    _("The requested patient, component, or stock lot is not available on this allocation board.")
                )
            key = (resolution_id, group_id, stock_option_id)
            new_quantity = actual.get(key, 0) + delta
            if new_quantity < 0:
                raise ValidationError(_("Allocated packs cannot be negative."))
            actual[key] = new_quantity
            normalized_changes.append(
                {
                    "resolution": resolution,
                    "group": group,
                    "member": member,
                    "lot": lot,
                    "delta": delta,
                }
            )

        if not normalized_changes:
            return self.get_allocation_board_data()

        for group in self.bulk_group_ids:
            for lot in group.lot_ids:
                allocated = sum(
                    quantity
                    for (
                        _resolution_id,
                        group_id,
                        option_id,
                    ), quantity in actual.items()
                    if group_id == group.id
                    and option_id == lot.stock_option_id.id
                )
                if allocated > lot.quantity_packs:
                    raise ValidationError(
                        _(
                            "%(component)s / %(lot)s: the change would use "
                            "%(allocated)s packs, above the selected maximum of "
                            "%(maximum)s."
                        )
                        % {
                            "component": group.name,
                            "lot": lot.lot or _("No batch"),
                            "allocated": allocated,
                            "maximum": lot.quantity_packs,
                        }
                    )

        outside_allocations = self.env[
            "cdu.picking.patient.allocation"
        ].search(
            [
                ("batch_id", "=", self.batch_id.id),
                ("picking_line_id", "!=", self.id),
            ]
        )
        outside_by_option = {}
        for allocation in outside_allocations:
            option_id = allocation.stock_option_id.id
            outside_by_option[option_id] = (
                outside_by_option.get(option_id, 0)
                + allocation.quantity_packs
            )
        proposed_by_option = {}
        for (
            _resolution_id,
            _group_id,
            option_id,
        ), quantity in actual.items():
            proposed_by_option[option_id] = (
                proposed_by_option.get(option_id, 0) + quantity
            )
        options = self.env["cdu.elmis.stock.option"].browse(
            proposed_by_option.keys()
        )
        for option in options:
            total = (
                outside_by_option.get(option.id, 0)
                + proposed_by_option.get(option.id, 0)
            )
            if total > option.stock_on_hand:
                raise ValidationError(
                    _(
                        "%(product)s / %(lot)s: %(quantity)s packs would be "
                        "allocated but only %(available)s are available."
                    )
                    % {
                        "product": option.orderable_name,
                        "lot": option.lot or _("No batch"),
                        "quantity": total,
                        "available": option.stock_on_hand,
                    }
                )

        Allocation = self.env["cdu.picking.patient.allocation"].with_context(
            cdu_skip_allocation_side_effects=True
        )
        existing_by_key = {
            (
                allocation.resolution_id.id,
                allocation.bulk_group_id.id,
                allocation.stock_option_id.id,
            ): allocation
            for allocation in self.patient_allocation_ids.filtered(
                lambda allocation: allocation.allocation_method == "bulk"
            )
        }
        for key, quantity in actual.items():
            existing = existing_by_key.get(key)
            if not quantity:
                if existing:
                    existing.with_context(
                        cdu_skip_allocation_side_effects=True
                    ).unlink()
                continue
            resolution_id, group_id, option_id = key
            member = members[(resolution_id, group_id)]
            lot = lots[(group_id, option_id)]
            if existing:
                if existing.quantity_packs != quantity:
                    existing.with_context(
                        cdu_skip_allocation_side_effects=True
                    ).write({"quantity_packs": quantity})
            else:
                Allocation.create(
                    {
                        "batch_id": self.batch_id.id,
                        "picking_line_id": self.id,
                        "resolution_id": resolution_id,
                        "stock_option_id": option_id,
                        "quantity_packs": quantity,
                        "daily_units": member.daily_units,
                        "allocation_method": "bulk",
                        "bulk_group_id": group_id,
                        "bulk_member_id": member.id,
                        "sequence": lot.sequence,
                    }
                )

        self.write(
            {
                "allocation_strategy": "manual",
                "manual_override_reason": False,
                "manual_override_by": False,
                "manual_override_at": False,
            }
        )
        self.bulk_group_ids.with_context(
            cdu_skip_auto_bulk_recalculation=True
        ).write({"distribution_stale": False})
        for member in self.bulk_group_ids.mapped("member_ids"):
            quantity = sum(
                allocation.quantity_packs
                for allocation in member.resolution_id.allocation_line_ids
                if allocation.bulk_member_id == member
            )
            member.with_context(
                cdu_skip_auto_bulk_recalculation=True
            ).write({"requested_packs": quantity})
        self._finalize_bulk_distribution(groups_ready=True)
        safety_errors = self._bulk_allocation_safety_errors(
            include_completeness=False
        )
        if safety_errors:
            raise ValidationError("\n".join(safety_errors))
        return self.get_allocation_board_data()

    def action_add_next_pack_bundle(self, resolution_id):
        self.ensure_one()
        self._ensure_bulk_allocation_editable()
        resolution = self.resolution_ids.filtered(
            lambda candidate: candidate.id == int(resolution_id)
        )[:1]
        if not resolution:
            raise ValidationError(_("The selected patient is not part of this regimen."))
        if not self._bulk_groups_are_ready():
            raise ValidationError(
                _("Select stock lots for every regimen component first.")
            )

        groups = self.bulk_group_ids.sorted(
            lambda group: (group.sequence, group.id)
        )
        member_by_group = {
            member.group_id.id: member
            for member in groups.mapped("member_ids").filtered(
                lambda member: member.resolution_id == resolution
            )
        }
        component_units = {}
        for group in groups:
            member = member_by_group[group.id]
            allocations = resolution.allocation_line_ids.filtered(
                lambda allocation, group=group: (
                    allocation.bulk_group_id == group
                )
            )
            units = sum(
                allocation.quantity_packs * allocation.selected_pack_size
                for allocation in allocations
            )
            component_units[group.id] = units

        if (resolution.coverage_days or 0) >= (resolution.target_days or 0):
            raise ValidationError(_("This patient is already fully served."))

        actual_by_option = {}
        actual_by_group_option = {}
        for allocation in self.patient_allocation_ids:
            option_id = allocation.stock_option_id.id
            actual_by_option[option_id] = (
                actual_by_option.get(option_id, 0)
                + allocation.quantity_packs
            )
            group_key = (allocation.bulk_group_id.id, option_id)
            actual_by_group_option[group_key] = (
                actual_by_group_option.get(group_key, 0)
                + allocation.quantity_packs
            )
        changes = []
        no_expiry = fields.Date.to_date("9999-12-31")
        for group in groups:
            member = member_by_group[group.id]
            candidates = []
            for lot in group.lot_ids:
                used_for_option = actual_by_option.get(
                    lot.stock_option_id.id, 0
                )
                used_for_group = actual_by_group_option.get(
                    (group.id, lot.stock_option_id.id), 0
                )
                if (
                    used_for_option >= lot.stock_option_id.stock_on_hand
                    or used_for_group >= lot.quantity_packs
                ):
                    continue
                new_coverage = (
                    component_units[group.id] + lot.pack_size
                ) / member.daily_units
                if not self._lot_can_be_consumed_before_expiry(
                    lot,
                    resolution,
                    new_coverage,
                ):
                    continue
                candidates.append(
                    (
                        lot.expiration_date or no_expiry,
                        max(
                            new_coverage - (resolution.target_days or 0),
                            0,
                        ),
                        lot.pack_size,
                        lot.sequence,
                        lot.id,
                        lot,
                    )
                )
            if not candidates:
                raise ValidationError(
                    _(
                        "%s has no valid whole pack that can extend this "
                        "patient's coverage before expiry."
                    )
                    % group.name
                )
            selected = min(candidates)[5]
            changes.append(
                {
                    "resolution_id": resolution.id,
                    "group_id": group.id,
                    "stock_option_id": selected.stock_option_id.id,
                    "delta": 1,
                }
            )
            actual_by_option[selected.stock_option_id.id] = (
                actual_by_option.get(selected.stock_option_id.id, 0) + 1
            )
            key = (group.id, selected.stock_option_id.id)
            actual_by_group_option[key] = (
                actual_by_group_option.get(key, 0) + 1
            )
        data = self.action_update_allocation_board(changes)
        data["applied_changes"] = changes
        return data

    def action_open_allocation_board(self):
        self.ensure_one()
        self._ensure_patient_resolutions()
        return {
            "type": "ir.actions.client",
            "tag": "cdu_elmis_allocation_board",
            "name": _("%s - Allocation Board") % self.openmrs_drug_name,
            "target": "current",
            "params": {"picking_line_id": self.id},
        }

    def get_allocation_board_data(self):
        self.ensure_one()
        self.batch_id._ensure_batch_workflow_access()
        groups = self.bulk_group_ids.sorted(
            lambda group: (group.sequence, group.id)
        )
        resolutions = self.resolution_ids.sorted(
            lambda resolution: (resolution.sequence, resolution.id)
        )
        allocations = self.patient_allocation_ids.filtered(
            lambda allocation: allocation.allocation_method == "bulk"
        )
        today = fields.Date.context_today(self)
        no_expiry = fields.Date.to_date("9999-12-31")

        allocated_by_option = {}
        for allocation in self.batch_id.patient_allocation_ids:
            option_id = allocation.stock_option_id.id
            allocated_by_option[option_id] = (
                allocated_by_option.get(option_id, 0)
                + allocation.quantity_packs
            )

        component_data = []
        for group in groups:
            lots = []
            for lot in group.lot_ids.sorted(
                lambda candidate: (
                    candidate.expiration_date or no_expiry,
                    candidate.sequence,
                    candidate.id,
                )
            ):
                allocated = sum(
                    allocations.filtered(
                        lambda allocation, group=group, lot=lot: (
                            allocation.bulk_group_id == group
                            and allocation.stock_option_id
                            == lot.stock_option_id
                        )
                    ).mapped("quantity_packs")
                )
                expiration_date = lot.expiration_date
                if expiration_date and expiration_date < today:
                    status = "expired"
                    status_label = _("Expired")
                elif (
                    expiration_date
                    and expiration_date <= today + timedelta(days=90)
                ):
                    status = "expires_soon"
                    status_label = _("Expires Soon")
                elif not expiration_date:
                    status = "no_expiry"
                    status_label = _("No Expiry")
                else:
                    status = "valid"
                    status_label = _("Valid")
                option_allocated = allocated_by_option.get(
                    lot.stock_option_id.id, 0
                )
                usable_packs = min(
                    lot.quantity_packs,
                    max(
                        lot.available_packs
                        - max(option_allocated - allocated, 0),
                        0,
                    ),
                )
                lots.append(
                    {
                        "id": lot.id,
                        "stock_option_id": lot.stock_option_id.id,
                        "product": lot.orderable_name or _("Unnamed product"),
                        "pack_size": lot.pack_size,
                        "lot": lot.lot or _("No batch"),
                        "expiry": (
                            fields.Date.to_string(expiration_date)
                            if expiration_date
                            else False
                        ),
                        "status": status,
                        "status_label": status_label,
                        "available_packs": lot.available_packs,
                        "selected_maximum": lot.quantity_packs,
                        "usable_packs": usable_packs,
                        "allocated_packs": allocated,
                        "remaining_packs": max(usable_packs - allocated, 0),
                    }
                )
            component_data.append(
                {
                    "id": group.id,
                    "name": group.name,
                    "sequence": group.sequence,
                    "lots": lots,
                    "required_units": group.required_units,
                    "supplied_units": group.supplied_units,
                    "allocated_packs": group.allocated_packs,
                }
            )

        status_labels = dict(
            self.env["cdu.picking.patient.resolution"]._fields[
                "status"
            ].selection
        )
        patient_data = []
        for resolution in resolutions:
            components = []
            for group in groups:
                member = group.member_ids.filtered(
                    lambda candidate, resolution=resolution: (
                        candidate.resolution_id == resolution
                    )
                )[:1]
                member_allocations = allocations.filtered(
                    lambda allocation, resolution=resolution, group=group: (
                        allocation.resolution_id == resolution
                        and allocation.bulk_group_id == group
                    )
                ).sorted(
                    lambda allocation: (
                        allocation.selected_lot_expiry or no_expiry,
                        allocation.sequence,
                        allocation.id,
                    )
                )
                components.append(
                    {
                        "group_id": group.id,
                        "name": group.name,
                        "coverage_days": round(
                            member.coverage_days if member else 0,
                            2,
                        ),
                        "daily_units": member.daily_units if member else 0,
                        "supplied_units": (
                            member.supplied_units if member else 0
                        ),
                        "allocations": [
                            {
                                "id": allocation.id,
                                "stock_option_id": (
                                    allocation.stock_option_id.id
                                ),
                                "product": (
                                    allocation.selected_orderable_name
                                ),
                                "lot": (
                                    allocation.selected_lot
                                    or _("No batch")
                                ),
                                "pack_size": allocation.selected_pack_size,
                                "quantity": allocation.quantity_packs,
                                "expiry": (
                                    fields.Date.to_string(
                                        allocation.selected_lot_expiry
                                    )
                                    if allocation.selected_lot_expiry
                                    else False
                                ),
                            }
                            for allocation in member_allocations
                        ],
                    }
                )
            patient_data.append(
                {
                    "resolution_id": resolution.id,
                    "sequence": resolution.sequence,
                    "prescription": resolution.prescription_id.display_name,
                    "patient": resolution.patient_id.display_name,
                    "target_days": resolution.target_days,
                    "coverage_days": round(resolution.coverage_days or 0, 2),
                    "status": resolution.status,
                    "status_label": status_labels.get(
                        resolution.status,
                        resolution.status,
                    ),
                    "components": components,
                }
            )

        status_counts = {
            status: len(
                resolutions.filtered(
                    lambda resolution, status=status: (
                        resolution.status == status
                    )
                )
            )
            for status in ("full", "partial", "unserved", "draft")
        }
        readiness_errors = self.batch_id._get_picking_readiness_errors()
        override_required = self._manual_override_is_required()
        return {
            "picking_line_id": self.id,
            "batch_id": self.batch_id.id,
            "batch_name": self.batch_id.display_name,
            "regimen": self.openmrs_drug_name,
            "locked": bool(self.batch_id.picking_confirmed_at),
            "strategy": self.allocation_strategy,
            "strategy_label": (
                _("Manual Override")
                if self.allocation_strategy == "manual"
                else _("Balanced FEFO")
            ),
            "override_required": override_required,
            "override_reason": self.manual_override_reason or "",
            "override_by": (
                self.manual_override_by.display_name
                if self.manual_override_by
                else False
            ),
            "override_at": (
                fields.Datetime.to_string(self.manual_override_at)
                if self.manual_override_at
                else False
            ),
            "components": component_data,
            "patients": patient_data,
            "summary": status_counts,
            "readiness_errors": readiness_errors,
            "can_confirm": not readiness_errors,
        }

    def action_open_bulk_allocation_overview(self):
        self.ensure_one()
        form_view = self.env.ref(
            "cdu_elmis.view_cdu_picking_line_bulk_allocation_form"
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("%s - Regimen Component Allocation")
            % self.openmrs_drug_name,
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "view_id": form_view.id,
            "views": [(form_view.id, "form")],
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
 
