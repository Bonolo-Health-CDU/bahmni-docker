import math
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class CduPickingPatientResolution(models.Model):
    _name = "cdu.picking.patient.resolution"
    _description = "CDU Prescription Picking Resolution"
    _order = "picking_line_id, sequence, id"

    batch_id = fields.Many2one(
        "cdu.batch", required=True, ondelete="cascade", index=True
    )
    picking_line_id = fields.Many2one(
        "cdu.picking.line", required=True, ondelete="cascade", index=True
    )
    patient_line_id = fields.Many2one(
        "cdu.batch.patient.line",
        string="Prescription Requirement",
        required=True,
        ondelete="restrict",
        index=True,
    )
    prescription_id = fields.Many2one(
        "cdu.prescription",
        related="patient_line_id.prescription_id",
        store=True,
        readonly=True,
        index=True,
    )
    patient_id = fields.Many2one(
        "res.partner",
        related="patient_line_id.patient_id",
        store=True,
        readonly=True,
    )
    sequence = fields.Integer(default=10)
    # Retained for migration/compatibility with draft allocations created by
    # 16.0.1.3.0. New calculations use the daily units on each selected
    # eLMIS product allocation.
    daily_dose = fields.Integer(
        string="Legacy Prescription Daily Units",
        required=True,
        default=1,
        readonly=True,
    )
    original_daily_dose = fields.Integer(
        string="Original Daily Units",
        required=True,
        default=1,
        readonly=True,
        copy=False,
    )
    daily_dose_changed_by = fields.Many2one(
        "res.users", string="Daily Units Changed By", readonly=True, copy=False
    )
    daily_dose_changed_at = fields.Datetime(
        string="Daily Units Changed At", readonly=True, copy=False
    )
    confirmed_supplied_days = fields.Integer(
        string="Confirmed Supplied Days",
        default=0,
        copy=False,
        help=(
            "Required only when more than one distinct eLMIS product is used. "
            "This is the user's confirmed total prescription coverage."
        ),
    )
    coverage_confirmed = fields.Boolean(
        string="Mixed-Product Coverage Confirmed",
        default=False,
        copy=False,
    )
    coverage_confirmed_by = fields.Many2one(
        "res.users", string="Coverage Confirmed By", readonly=True, copy=False
    )
    coverage_confirmed_at = fields.Datetime(
        string="Coverage Confirmed At", readonly=True, copy=False
    )
    status = fields.Selection(
        [
            ("draft", "Not Allocated"),
            ("full", "Full"),
            ("partial", "Partial"),
            ("unserved", "Unserved"),
        ],
        required=True,
        default="draft",
        index=True,
    )
    unserved_reason = fields.Selection(
        [("insufficient_stock", "Insufficient Stock")],
        string="Unserved Reason",
    )
    unserved_note = fields.Text(string="Unserved Note")
    allocation_line_ids = fields.One2many(
        "cdu.picking.patient.allocation",
        "resolution_id",
        string="Pack Allocations",
    )
    bulk_member_id = fields.Many2one(
        "cdu.picking.bulk.member",
        compute="_compute_bulk_member_id",
        string="Regimen Component Member",
    )
    required_units = fields.Float(
        related="patient_line_id.required_units", readonly=True
    )
    target_days = fields.Integer(
        string="Target Supply Days", compute="_compute_target_days"
    )
    allocated_packs = fields.Integer(
        compute="_compute_allocation_totals", store=True
    )
    supplied_units = fields.Float(
        compute="_compute_allocation_totals", store=True
    )
    distinct_product_count = fields.Integer(
        compute="_compute_allocation_totals", store=True
    )
    coverage_confirmation_required = fields.Boolean(
        compute="_compute_allocation_totals", store=True
    )
    automatic_coverage_days = fields.Float(
        compute="_compute_allocation_totals", store=True
    )
    coverage_days = fields.Float(
        compute="_compute_allocation_totals", store=True
    )
    coverage_variance_days = fields.Float(
        compute="_compute_allocation_totals", store=True
    )
    locked = fields.Boolean(default=False, copy=False, index=True)
    legacy = fields.Boolean(default=False, copy=False, index=True)

    _sql_constraints = [
        (
            "unique_patient_resolution",
            "unique(picking_line_id, patient_line_id)",
            "A prescription can only be resolved once for a regimen.",
        ),
    ]

    @api.depends(
        "patient_line_id.effective_repeat_days",
        "patient_line_id.prescription_repeat_days",
        "patient_line_id.repeat_days",
    )
    def _compute_target_days(self):
        for resolution in self:
            resolution.target_days = (
                resolution.patient_line_id.effective_repeat_days
                or resolution.patient_line_id.prescription_repeat_days
                or resolution.patient_line_id.repeat_days
                or 0
            )

    @api.depends("picking_line_id.bulk_group_ids.member_ids.resolution_id")
    def _compute_bulk_member_id(self):
        Member = self.env["cdu.picking.bulk.member"]
        for resolution in self:
            resolution.bulk_member_id = Member.search(
                [("resolution_id", "=", resolution.id)], limit=1
            )

    @api.depends(
        "allocation_line_ids.quantity_packs",
        "allocation_line_ids.selected_pack_size",
        "allocation_line_ids.daily_units",
        "allocation_line_ids.bulk_group_id",
        "allocation_line_ids.bulk_member_id",
        "allocation_line_ids.selected_orderable_id",
        "allocation_line_ids.selected_orderable_code",
        "allocation_line_ids.allocation_method",
        "picking_line_id.regimen_component_mode",
        "picking_line_id.bulk_group_ids.member_ids.daily_units",
        "picking_line_id.bulk_group_ids.member_ids.resolution_id",
        "confirmed_supplied_days",
        "coverage_confirmed",
        "patient_line_id.required_units",
    )
    def _compute_allocation_totals(self):
        for resolution in self:
            resolution.allocated_packs = sum(
                resolution.allocation_line_ids.mapped("quantity_packs")
            )
            supplied_units = sum(
                allocation.quantity_packs * allocation.selected_pack_size
                for allocation in resolution.allocation_line_ids
            )
            resolution.supplied_units = supplied_units
            products = {}
            for allocation in resolution.allocation_line_ids:
                key = allocation._get_product_key()
                values = products.setdefault(
                    key,
                    {"units": 0, "daily_units": allocation.daily_units or 0},
                )
                values["units"] += (
                    allocation.quantity_packs * allocation.selected_pack_size
                )
            product_coverages = [
                values["units"] / values["daily_units"]
                for values in products.values()
                if values["daily_units"] > 0
            ]
            product_count = len(products)
            bulk_allocations = resolution.allocation_line_ids.filtered(
                lambda allocation: allocation.allocation_method == "bulk"
            )
            is_bulk_allocation = bool(
                resolution.allocation_line_ids
                and len(bulk_allocations) == len(resolution.allocation_line_ids)
            )
            if (
                is_bulk_allocation
                and resolution.picking_line_id.regimen_component_mode
            ):
                # Every bulk group is one regimen component. A prescription is
                # only covered for as many days as its least-covered component.
                component_coverages = []
                for group in resolution.picking_line_id.bulk_group_ids:
                    member = group.member_ids.filtered(
                        lambda candidate, resolution=resolution: (
                            candidate.resolution_id == resolution
                        )
                    )[:1]
                    component_allocations = bulk_allocations.filtered(
                        lambda allocation, group=group: (
                            allocation.bulk_group_id == group
                        )
                    )
                    component_units = sum(
                        allocation.quantity_packs
                        * allocation.selected_pack_size
                        for allocation in component_allocations
                    )
                    daily_units = member.daily_units if member else 0
                    component_coverages.append(
                        component_units / daily_units
                        if daily_units > 0
                        else 0
                    )
                automatic_coverage = (
                    min(component_coverages) if component_coverages else 0
                )
                confirmation_required = False
            elif is_bulk_allocation:
                # Preserve the calculation used by confirmed historical picks
                # created before regimen components were introduced.
                daily_units = bulk_allocations[0].daily_units or 0
                automatic_coverage = (
                    supplied_units / daily_units if daily_units > 0 else 0
                )
                confirmation_required = False
            else:
                automatic_coverage = (
                    product_coverages[0] if product_count == 1 else 0
                )
                confirmation_required = product_count > 1
            coverage = (
                resolution.confirmed_supplied_days
                if confirmation_required and resolution.coverage_confirmed
                else automatic_coverage
            )
            required_days = (
                resolution.patient_line_id.effective_repeat_days
                or resolution.patient_line_id.repeat_days
                or 0
            )
            resolution.distinct_product_count = product_count
            resolution.coverage_confirmation_required = confirmation_required
            resolution.automatic_coverage_days = automatic_coverage
            resolution.coverage_days = coverage
            resolution.coverage_variance_days = coverage - required_days

    def _ensure_unlocked(self):
        if self.env.context.get("cdu_allocation_migration"):
            return
        if any(
            resolution.locked or resolution.batch_id.picking_confirmed_at
            for resolution in self
        ):
            raise UserError(_("Confirmed picking allocations are locked."))

    def action_mark_unserved(self):
        self._ensure_unlocked()
        for resolution in self:
            resolution.allocation_line_ids.unlink()
            resolution.with_context(cdu_reset_coverage_confirmation=True).write(
                {
                    "status": "unserved",
                    "unserved_reason": "insufficient_stock",
                    "confirmed_supplied_days": 0,
                    "coverage_confirmed": False,
                }
            )
        return True

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            patient_line = self.env["cdu.batch.patient.line"].browse(
                vals.get("patient_line_id")
            )
            source_value = vals.get("daily_dose", patient_line.daily_dose or 1)
            daily_dose = self._validated_positive_integer(
                source_value, _("Legacy prescription daily units")
            )
            vals["daily_dose"] = daily_dose
            vals["original_daily_dose"] = daily_dose
        return super().create(vals_list)

    @api.model
    def _validated_positive_integer(self, value, label):
        try:
            raw_value = float(value)
            integer_value = int(raw_value)
        except (TypeError, ValueError):
            raw_value = 0
            integer_value = 0
        if integer_value <= 0 or raw_value != integer_value:
            raise ValidationError(
                _("%s must be a whole number greater than zero.") % label
            )
        return integer_value

    @api.constrains("daily_dose")
    def _check_daily_dose(self):
        if any(resolution.daily_dose <= 0 for resolution in self):
            raise ValidationError(
                _("Daily units for supply calculation must be a whole number greater than zero.")
            )

    def write(self, vals):
        if not (
            set(vals) == {"locked"}
            and vals.get("locked")
            and not any(self.mapped("locked"))
        ):
            self._ensure_unlocked()
        values = dict(vals)
        if "confirmed_supplied_days" in values:
            if (
                values["confirmed_supplied_days"]
                and not self.env.context.get("cdu_reset_coverage_confirmation")
            ):
                values["confirmed_supplied_days"] = self._validated_positive_integer(
                    values["confirmed_supplied_days"],
                    _("Confirmed supplied days"),
                )
                values.update(
                    {
                        "coverage_confirmed": True,
                        "coverage_confirmed_by": self.env.user.id,
                        "coverage_confirmed_at": fields.Datetime.now(),
                    }
                )
            elif not self.env.context.get("cdu_reset_coverage_confirmation"):
                raise ValidationError(
                    _("Confirmed supplied days must be a whole number greater than zero.")
                )
        result = super().write(values)
        if {
            "confirmed_supplied_days",
            "coverage_confirmed",
        }.intersection(values):
            self._refresh_status()
            self._sync_patient_supply_calculations()
        return result

    def unlink(self):
        self._ensure_unlocked()
        return super().unlink()

    def _refresh_status(self):
        for resolution in self:
            if not resolution.allocation_line_ids:
                if resolution.status != "unserved":
                    resolution.status = "draft"
                continue
            if (
                resolution.coverage_confirmation_required
                and not resolution.coverage_confirmed
            ):
                if resolution.status != "draft":
                    resolution.status = "draft"
                continue
            supplied_days = resolution.coverage_days
            required_days = (
                resolution.patient_line_id.effective_repeat_days
                or resolution.patient_line_id.prescription_repeat_days
                or resolution.patient_line_id.repeat_days
                or 0
            )
            resolution.write(
                {
                    "status": (
                        "full" if supplied_days >= required_days else "partial"
                    ),
                    "unserved_reason": False,
                    "unserved_note": False,
                }
            )

    def _reset_coverage_confirmation(self):
        confirmed = self.filtered(
            lambda resolution: (
                resolution.confirmed_supplied_days
                or resolution.coverage_confirmed
            )
        )
        if confirmed:
            super(
                CduPickingPatientResolution,
                confirmed.with_context(cdu_reset_coverage_confirmation=True),
            ).write(
                {
                    "confirmed_supplied_days": 0,
                    "coverage_confirmed": False,
                    "coverage_confirmed_by": False,
                    "coverage_confirmed_at": False,
                }
            )

    def _sync_patient_supply_calculations(self):
        for resolution in self:
            patient_line = resolution.patient_line_id
            effective_days = (
                patient_line.effective_repeat_days
                or patient_line.prescription_repeat_days
                or patient_line.repeat_days
                or 0
            )
            supplied_units = resolution.supplied_units
            supplied_days = resolution.coverage_days
            back_order_days = max(
                (patient_line.cdu_days or effective_days) - supplied_days, 0
            )
            next_pickup_date = False
            if patient_line.prescription_id.next_drug_pickup_date and supplied_days:
                next_pickup_date = (
                    patient_line.prescription_id.next_drug_pickup_date
                    + timedelta(days=int(supplied_days))
                )
            patient_line.write(
                {
                    "available_quantity": supplied_units,
                    "picked_quantity": supplied_units,
                    "picked_units": supplied_units,
                    "actual_supplied_days": supplied_days,
                    "back_order_days": back_order_days,
                    "served_days": int(supplied_days),
                    "remaining_days": int(back_order_days),
                    "recalculated_next_drug_pickup_date": next_pickup_date,
                    "calculated_next_pickup_date": next_pickup_date,
                }
            )

    def _create_partial_backorder_prescriptions(self):
        """Create one traceable balance prescription for every partial supply."""
        Component = self.env["cdu.prescription.backorder.component"]
        created = self.env["cdu.prescription"]
        for resolution in self.filtered(
            lambda candidate: candidate.status == "partial"
        ):
            source = resolution.prescription_id
            existing = source.backorder_prescription_ids[:1]
            if existing:
                created |= existing
                continue

            remaining_days = int(
                math.ceil(
                    max(
                        (resolution.target_days or 0)
                        - (resolution.coverage_days or 0),
                        0,
                    )
                )
            )
            if remaining_days <= 0:
                continue

            source.remaining_days_supply = remaining_days
            next_pickup_date = (
                source.next_drug_pickup_date
                or resolution.patient_line_id.calculated_next_pickup_date
                or fields.Date.context_today(source)
            )
            root = source.backorder_root_prescription_id or source
            note = _(
                "System-created balance from %(source)s after partial supply "
                "in %(batch)s. Outstanding supply: %(days)s day(s)."
            ) % {
                "source": source.display_name,
                "batch": resolution.batch_id.display_name,
                "days": remaining_days,
            }
            existing_notes = (source.validation_notes or "").strip()
            balance = source.copy(
                default={
                    "state": "awaiting_verification",
                    "batch_id": False,
                    "repeat_days": remaining_days,
                    "remaining_days_supply": remaining_days,
                    "next_drug_pickup_date": next_pickup_date,
                    "next_clinical_visit_date": (
                        next_pickup_date + timedelta(days=remaining_days)
                    ),
                    "verified_by": False,
                    "verified_at": False,
                    "validated_by": False,
                    "validated_at": False,
                    "active_rejection_id": False,
                    "rejected_from_state": False,
                    "is_backorder": True,
                    "backorder_source_prescription_id": source.id,
                    "backorder_root_prescription_id": root.id,
                    "backorder_created_from_batch_id": resolution.batch_id.id,
                    "backorder_required_days": remaining_days,
                    "validation_notes": "\n\n".join(
                        value for value in (existing_notes, note) if value
                    ),
                }
            )

            members = resolution.picking_line_id.bulk_group_ids.mapped(
                "member_ids"
            ).filtered(
                lambda member, resolution=resolution: (
                    member.resolution_id == resolution
                )
            )
            if members:
                Component.create(
                    [
                        {
                            "backorder_prescription_id": balance.id,
                            "source_group_id": member.group_id.id,
                            "sequence": member.group_id.sequence,
                            "name": member.group_id.name,
                            "selected_products": (
                                member.group_id.selected_products_display
                            ),
                            "daily_units": member.daily_units,
                            "target_days": member.target_days,
                            "supplied_days": member.coverage_days,
                            "outstanding_days": max(
                                member.target_days - member.coverage_days,
                                0,
                            ),
                            "required_units": member.required_units,
                            "supplied_units": member.supplied_units,
                            "outstanding_units": max(
                                member.required_units - member.supplied_units,
                                0,
                            ),
                        }
                        for member in members.sorted(
                            lambda member: (
                                member.group_id.sequence,
                                member.group_id.id,
                            )
                        )
                    ]
                )
            else:
                Component.create(
                    {
                        "backorder_prescription_id": balance.id,
                        "name": _("Prescription balance"),
                        "daily_units": resolution.daily_dose,
                        "target_days": resolution.target_days,
                        "supplied_days": resolution.coverage_days,
                        "outstanding_days": remaining_days,
                        "required_units": resolution.required_units,
                        "supplied_units": resolution.supplied_units,
                        "outstanding_units": max(
                            resolution.required_units - resolution.supplied_units,
                            0,
                        ),
                    }
                )
            created |= balance
        return created


class CduPickingPatientAllocation(models.Model):
    _name = "cdu.picking.patient.allocation"
    _description = "Exact Prescription-to-Lot Pack Allocation"
    _order = "batch_id, picking_line_id, resolution_id, sequence, id"

    batch_id = fields.Many2one(
        "cdu.batch", required=True, ondelete="cascade", index=True
    )
    picking_line_id = fields.Many2one(
        "cdu.picking.line", required=True, ondelete="cascade", index=True
    )
    resolution_id = fields.Many2one(
        "cdu.picking.patient.resolution",
        required=True,
        ondelete="cascade",
        index=True,
    )
    patient_line_id = fields.Many2one(
        "cdu.batch.patient.line",
        related="resolution_id.patient_line_id",
        store=True,
        readonly=True,
        index=True,
    )
    prescription_id = fields.Many2one(
        "cdu.prescription",
        related="resolution_id.prescription_id",
        store=True,
        readonly=True,
        index=True,
    )
    stock_option_id = fields.Many2one(
        "cdu.elmis.stock.option",
        string="eLMIS Product / Lot",
        required=True,
        ondelete="restrict",
        domain="[('batch_id', '=', batch_id), ('stock_on_hand', '>', 0), ('pack_size', '>', 0)]",
    )
    selected_orderable_code = fields.Char(readonly=True)
    selected_orderable_id = fields.Char(readonly=True)
    selected_orderable_name = fields.Char(readonly=True)
    selected_pack_size = fields.Integer(string="Pack Size", readonly=True)
    selected_lot = fields.Char(string="Batch Number", readonly=True)
    selected_lot_id = fields.Char(readonly=True)
    selected_lot_expiry = fields.Date(string="Expiry", readonly=True)
    selected_stock_on_hand = fields.Integer(
        string="Available Packs", readonly=True
    )
    daily_units = fields.Integer(
        string="Daily Units for This Product",
        required=True,
        default=1,
        help=(
            "Whole product units taken per day for this prescription. Lots of "
            "the same eLMIS product share this value."
        ),
    )
    original_daily_units = fields.Integer(
        string="Original Daily Units",
        required=True,
        default=1,
        readonly=True,
        copy=False,
    )
    daily_units_changed_by = fields.Many2one(
        "res.users", string="Daily Units Changed By", readonly=True, copy=False
    )
    daily_units_changed_at = fields.Datetime(
        string="Daily Units Changed At", readonly=True, copy=False
    )
    quantity_packs = fields.Integer(string="Allocated Packs", required=True, default=1)
    suggested_packs = fields.Integer(
        string="Packs if Used Alone",
        compute="_compute_product_calculations",
        help=(
            "Full-coverage pack suggestion if this stock row's product were "
            "used alone. Keep the entered pack quantity authoritative."
        ),
    )
    allocated_units = fields.Integer(
        string="Allocated Units", compute="_compute_product_calculations"
    )
    product_coverage_days = fields.Float(
        string="Coverage from This Lot", compute="_compute_product_calculations"
    )
    allocation_method = fields.Selection(
        [("bulk", "Regimen Component"), ("individual", "Individual")],
        required=True,
        default="individual",
    )
    bulk_group_id = fields.Many2one(
        "cdu.picking.bulk.group", ondelete="cascade", index=True
    )
    bulk_member_id = fields.Many2one(
        "cdu.picking.bulk.member", ondelete="cascade", index=True
    )
    sequence = fields.Integer(default=10)
    locked = fields.Boolean(default=False, copy=False, index=True)
    legacy = fields.Boolean(default=False, copy=False, index=True)

    _sql_constraints = [
        (
            "unique_resolution_stock_option",
            "unique(resolution_id, stock_option_id)",
            "Combine packs for the same prescription and stock lot on one row.",
        ),
    ]

    @api.depends(
        "quantity_packs",
        "selected_pack_size",
        "daily_units",
        "resolution_id.patient_line_id.effective_repeat_days",
        "resolution_id.patient_line_id.prescription_repeat_days",
        "resolution_id.patient_line_id.repeat_days",
    )
    def _compute_product_calculations(self):
        for allocation in self:
            target_days = (
                allocation.resolution_id.patient_line_id.effective_repeat_days
                or allocation.resolution_id.patient_line_id.prescription_repeat_days
                or allocation.resolution_id.patient_line_id.repeat_days
                or 0
            )
            units = allocation.quantity_packs * allocation.selected_pack_size
            allocation.allocated_units = units
            allocation.product_coverage_days = (
                units / allocation.daily_units
                if allocation.daily_units > 0
                else 0
            )
            allocation.suggested_packs = (
                math.ceil(
                    target_days
                    * allocation.daily_units
                    / allocation.selected_pack_size
                )
                if target_days > 0 and allocation.selected_pack_size > 0
                else 0
            )

    @api.model
    def _option_product_key(self, option):
        return (
            ("id", option.orderable_id)
            if option.orderable_id
            else ("code", option.orderable_code or option.id)
        )

    def _get_product_key(self):
        self.ensure_one()
        if self.selected_orderable_id:
            return ("id", self.selected_orderable_id)
        if self.selected_orderable_code:
            return ("code", self.selected_orderable_code)
        return self._option_product_key(self.stock_option_id)

    @api.constrains(
        "batch_id",
        "picking_line_id",
        "resolution_id",
        "stock_option_id",
        "quantity_packs",
        "daily_units",
    )
    def _check_allocation(self):
        for allocation in self:
            if allocation.quantity_packs <= 0:
                raise ValidationError(
                    _("Allocated packs must be a positive whole number.")
                )
            if allocation.daily_units <= 0:
                raise ValidationError(
                    _("Daily units for each selected product must be a whole number greater than zero.")
                )
            option = allocation.stock_option_id
            if not option or option.pack_size <= 0:
                raise ValidationError(
                    _("The selected eLMIS stock option must have a positive pack size.")
                )
            if option.batch_id != allocation.batch_id:
                raise ValidationError(
                    _("The selected stock option belongs to another batch.")
                )
            if (
                option.expiration_date
                and option.expiration_date < fields.Date.context_today(allocation)
            ):
                raise ValidationError(
                    _(
                        "%(product)s / %(lot)s is expired and cannot be allocated."
                    )
                    % {
                        "product": option.orderable_name,
                        "lot": option.lot or _("No batch"),
                    }
                )
            if allocation.resolution_id.batch_id != allocation.batch_id:
                raise ValidationError(
                    _("The prescription resolution belongs to another batch.")
                )
            if allocation.resolution_id.picking_line_id != allocation.picking_line_id:
                raise ValidationError(
                    _("The prescription resolution belongs to another regimen.")
                )
            product_key = allocation._get_product_key()
            conflicting_lot = allocation.resolution_id.allocation_line_ids.filtered(
                lambda sibling, allocation=allocation, product_key=product_key: (
                    sibling != allocation
                    and sibling._get_product_key() == product_key
                    and sibling.daily_units != allocation.daily_units
                )
            )[:1]
            if conflicting_lot:
                raise ValidationError(
                    _(
                        "All lots of the same eLMIS product must use the same "
                        "daily units for a prescription."
                    )
                )

            # Serialize allocations for the same stock row so concurrent users
            # cannot both pass the batch-wide availability check.
            self.env.cr.execute(
                "SELECT id FROM cdu_elmis_stock_option WHERE id = %s FOR UPDATE",
                [option.id],
            )
            allocated = sum(
                self.search(
                    [
                        ("batch_id", "=", allocation.batch_id.id),
                        ("stock_option_id", "=", option.id),
                    ]
                ).mapped("quantity_packs")
            )
            if allocated > option.stock_on_hand:
                raise ValidationError(
                    _(
                        "%(product)s / %(lot)s: allocated packs (%(allocated)s) "
                        "exceed available packs (%(available)s) across this batch."
                    )
                    % {
                        "product": option.orderable_name,
                        "lot": option.lot or _("No batch"),
                        "allocated": allocated,
                        "available": option.stock_on_hand,
                    }
                )

    def _ensure_unlocked(self):
        if self.env.context.get("cdu_allocation_migration"):
            return
        if any(
            allocation.locked
            or allocation.resolution_id.locked
            or allocation.batch_id.picking_confirmed_at
            for allocation in self
        ):
            raise UserError(_("Confirmed picking allocations are locked."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            resolution = self.env["cdu.picking.patient.resolution"].browse(
                vals.get("resolution_id")
            )
            if resolution:
                resolution._ensure_unlocked()
                vals.setdefault("batch_id", resolution.batch_id.id)
                vals.setdefault("picking_line_id", resolution.picking_line_id.id)
                vals.setdefault(
                    "allocation_method", resolution.picking_line_id.allocation_mode
                )
            option = self.env["cdu.elmis.stock.option"].browse(
                vals.get("stock_option_id")
            )
            product_key = self._option_product_key(option)
            existing = resolution.allocation_line_ids.filtered(
                lambda allocation, product_key=product_key: (
                    allocation._get_product_key() == product_key
                )
            )[:1]
            if "daily_units" not in vals:
                vals["daily_units"] = (
                    existing.daily_units
                    if existing
                    else (resolution.daily_dose or 1)
                )
            daily_units = resolution._validated_positive_integer(
                vals["daily_units"], _("Daily units for this product")
            )
            if existing and existing.daily_units != daily_units:
                raise ValidationError(
                    _(
                        "All lots of the same eLMIS product must use the same "
                        "daily units for a prescription."
                    )
                )
            vals["daily_units"] = daily_units
            vals["original_daily_units"] = daily_units
        records = super().create(vals_list)
        records._sync_stock_snapshots()
        if self.env.context.get("cdu_skip_allocation_side_effects"):
            return records
        resolutions = records.mapped("resolution_id")
        resolutions._reset_coverage_confirmation()
        resolutions._refresh_status()
        resolutions._sync_patient_supply_calculations()
        records.mapped("batch_id")._sync_fulfilment_lines_from_allocations()
        return records

    def write(self, vals):
        self._ensure_unlocked()
        if set(vals) == {"locked"} and vals.get("locked"):
            return super().write(vals)
        values = dict(vals)
        daily_units_changed = False
        daily_write_records = self.browse()
        if "daily_units" in values:
            values["daily_units"] = self.env[
                "cdu.picking.patient.resolution"
            ]._validated_positive_integer(
                values["daily_units"], _("Daily units for this product")
            )
            daily_write_records = self
            for allocation in self:
                product_key = allocation._get_product_key()
                daily_write_records |= (
                    allocation.resolution_id.allocation_line_ids.filtered(
                        lambda sibling, product_key=product_key: (
                            sibling._get_product_key() == product_key
                        )
                    )
                )
            daily_write_records._ensure_unlocked()
            changed = daily_write_records.filtered(
                lambda allocation: allocation.daily_units
                != values["daily_units"]
            )
            if changed:
                daily_units_changed = True
        quantity_changed = (
            "quantity_packs" in values
            and any(
                allocation.quantity_packs != values["quantity_packs"]
                for allocation in self
            )
        )
        stock_changed = (
            "stock_option_id" in values
            and any(
                allocation.stock_option_id.id != values["stock_option_id"]
                for allocation in self
            )
        )
        relevant_change = (
            quantity_changed or stock_changed or daily_units_changed
        )
        if "daily_units" in values:
            daily_values = {"daily_units": values.pop("daily_units")}
            if daily_units_changed:
                daily_values.update(
                    {
                        "daily_units_changed_by": self.env.user.id,
                        "daily_units_changed_at": fields.Datetime.now(),
                    }
                )
                super(
                    CduPickingPatientAllocation, daily_write_records
                ).write(daily_values)
        result = super().write(values) if values else True
        if "stock_option_id" in vals:
            self._sync_stock_snapshots()
        if self.env.context.get("cdu_skip_allocation_side_effects"):
            return result
        touched_records = self | daily_write_records
        resolutions = touched_records.mapped("resolution_id")
        if relevant_change:
            resolutions._reset_coverage_confirmation()
        resolutions._refresh_status()
        resolutions._sync_patient_supply_calculations()
        if relevant_change:
            touched_records.mapped("bulk_group_id").write(
                {"distribution_stale": True}
            )
        touched_records.mapped(
            "batch_id"
        )._sync_fulfilment_lines_from_allocations()
        return result

    def unlink(self):
        self._ensure_unlocked()
        batches = self.mapped("batch_id")
        resolutions = self.mapped("resolution_id")
        result = super().unlink()
        if self.env.context.get("cdu_skip_allocation_side_effects"):
            return result
        resolutions = resolutions.exists()
        resolutions._reset_coverage_confirmation()
        resolutions._refresh_status()
        resolutions._sync_patient_supply_calculations()
        batches._sync_fulfilment_lines_from_allocations()
        return result

    @api.onchange("stock_option_id")
    def _onchange_stock_option_id(self):
        self._sync_stock_snapshots()
        for allocation in self:
            if not allocation.stock_option_id:
                continue
            product_key = allocation._option_product_key(
                allocation.stock_option_id
            )
            existing = allocation.resolution_id.allocation_line_ids.filtered(
                lambda sibling, allocation=allocation, product_key=product_key: (
                    sibling != allocation
                    and sibling._get_product_key() == product_key
                )
            )[:1]
            allocation.daily_units = existing.daily_units if existing else 1

    def _sync_stock_snapshots(self):
        for allocation in self:
            option = allocation.stock_option_id
            allocation.update(
                {
                    "selected_orderable_code": option.orderable_code,
                    "selected_orderable_id": option.orderable_id,
                    "selected_orderable_name": option.orderable_name,
                    "selected_pack_size": option.pack_size,
                    "selected_lot": option.lot,
                    "selected_lot_id": option.lot_id,
                    "selected_lot_expiry": option.expiration_date,
                    "selected_stock_on_hand": option.stock_on_hand,
                }
            )


class CduPickingBulkGroup(models.Model):
    _name = "cdu.picking.bulk.group"
    _description = "CDU Regimen Component"
    _order = "picking_line_id, sequence, id"

    name = fields.Char(required=True, default=lambda self: _("Regimen Component"))
    sequence = fields.Integer(default=10)
    batch_id = fields.Many2one(
        "cdu.batch", required=True, ondelete="cascade", index=True
    )
    picking_line_id = fields.Many2one(
        "cdu.picking.line",
        string="Regimen",
        required=True,
        ondelete="cascade",
        index=True,
        domain="[('batch_id', '=', batch_id), ('allocation_mode', '=', 'bulk')]",
    )
    product_stock_option_id = fields.Many2one(
        "cdu.elmis.stock.option",
        string="Legacy Product / Pack Size",
        ondelete="restrict",
        domain="[('batch_id', '=', batch_id), ('stock_on_hand', '>', 0), ('pack_size', '>', 0)]",
        help="Retained for historical records. New components select stock on the Lots tab.",
    )
    orderable_name = fields.Char(
        related="product_stock_option_id.orderable_name", readonly=True
    )
    pack_size = fields.Integer(
        related="product_stock_option_id.pack_size", readonly=True
    )
    member_ids = fields.One2many(
        "cdu.picking.bulk.member", "group_id", string="Prescriptions"
    )
    lot_ids = fields.One2many(
        "cdu.picking.bulk.lot", "group_id", string="Lots"
    )
    eligible_resolution_ids = fields.Many2many(
        "cdu.picking.patient.resolution",
        compute="_compute_eligible_resolution_ids",
        string="Eligible Prescriptions",
    )
    requested_packs = fields.Integer(
        compute="_compute_totals", string="Requested Packs"
    )
    picked_packs = fields.Integer(
        compute="_compute_totals", string="Allocated Packs"
    )
    allocated_packs = fields.Integer(
        compute="_compute_totals", string="Assigned Packs"
    )
    available_packs = fields.Integer(
        compute="_compute_totals", string="Selected Available Packs"
    )
    required_units = fields.Integer(
        compute="_compute_totals", string="Required Units"
    )
    supplied_units = fields.Integer(
        compute="_compute_totals", string="Supplied Units"
    )
    excess_units = fields.Integer(
        compute="_compute_totals", string="Excess Units"
    )
    selected_products_display = fields.Char(
        compute="_compute_totals", string="Selected Products"
    )
    selected_pack_sizes_display = fields.Char(
        compute="_compute_totals", string="Selected Pack Sizes"
    )
    distribution_stale = fields.Boolean(
        string="Automatic Allocation Pending", default=True, copy=False
    )
    locked = fields.Boolean(default=False, copy=False, index=True)
    legacy = fields.Boolean(default=False, copy=False, index=True)

    @api.depends(
        "picking_line_id.resolution_ids",
        "picking_line_id.bulk_group_ids.member_ids.resolution_id",
    )
    def _compute_eligible_resolution_ids(self):
        for group in self:
            group.eligible_resolution_ids = group.picking_line_id.resolution_ids

    @api.depends(
        "member_ids.requested_packs",
        "member_ids.allocated_packs",
        "member_ids.required_units",
        "member_ids.supplied_units",
        "lot_ids.quantity_packs",
        "lot_ids.stock_option_id",
        "lot_ids.stock_option_id.orderable_name",
        "lot_ids.stock_option_id.pack_size",
        "lot_ids.stock_option_id.stock_on_hand",
    )
    def _compute_totals(self):
        for group in self:
            group.requested_packs = sum(group.member_ids.mapped("requested_packs"))
            allocated_packs = sum(group.member_ids.mapped("allocated_packs"))
            group.picked_packs = allocated_packs
            group.allocated_packs = allocated_packs
            group.available_packs = sum(
                min(lot.quantity_packs, lot.available_packs)
                for lot in group.lot_ids
            )
            group.required_units = sum(group.member_ids.mapped("required_units"))
            group.supplied_units = sum(group.member_ids.mapped("supplied_units"))
            group.excess_units = sum(group.member_ids.mapped("excess_units"))
            group.selected_products_display = ", ".join(
                sorted(
                    {
                        name
                        for name in group.lot_ids.mapped("orderable_name")
                        if name
                    }
                )
            )
            group.selected_pack_sizes_display = ", ".join(
                str(pack_size)
                for pack_size in sorted(
                    set(group.lot_ids.mapped("pack_size"))
                )
                if pack_size
            )

    @api.constrains("batch_id", "picking_line_id", "product_stock_option_id")
    def _check_group(self):
        for group in self:
            if group.picking_line_id.batch_id != group.batch_id:
                raise ValidationError(
                    _("The regimen component belongs to another batch.")
                )
            if group.picking_line_id.allocation_mode != "bulk":
                raise ValidationError(
                    _("Regimen components require Regimen Components allocation mode.")
                )
            if (
                group.product_stock_option_id
                and group.product_stock_option_id.batch_id != group.batch_id
            ):
                raise ValidationError(
                    _("The selected product belongs to another batch.")
                )

    @api.model_create_multi
    def create(self, vals_list):
        existing_by_line = {}
        for values in vals_list:
            picking_line_id = values.get("picking_line_id")
            if (
                picking_line_id
                and values.get("name") in (False, _("Regimen Component"))
            ):
                existing_count = existing_by_line.setdefault(
                    picking_line_id,
                    self.search_count(
                        [("picking_line_id", "=", picking_line_id)]
                    ),
                )
                values["name"] = _("Component %s") % (existing_count + 1)
                existing_by_line[picking_line_id] = existing_count + 1
        groups = super().create(vals_list)
        groups._populate_prescriptions()
        return groups

    def _populate_prescriptions(self):
        """Add every prescription to this component in established patient order."""
        Member = self.env["cdu.picking.bulk.member"].with_context(
            cdu_skip_auto_bulk_recalculation=True
        )
        editable_groups = self.filtered(
            lambda group: not group.locked and not group.batch_id.picking_confirmed_at
        )
        for group in editable_groups.sorted(
            lambda candidate: (candidate.sequence, candidate.id)
        ):
            group.picking_line_id._ensure_patient_resolutions()
            for member in group.member_ids:
                patient_sequence = member.resolution_id.sequence
                if member.sequence != patient_sequence:
                    member.with_context(
                        cdu_skip_auto_bulk_recalculation=True
                    ).write({"sequence": patient_sequence})
            existing_resolutions = group.member_ids.mapped("resolution_id")
            resolutions = (
                group.picking_line_id.resolution_ids
                - existing_resolutions
            ).sorted(lambda resolution: (resolution.sequence, resolution.id))
            if not resolutions:
                continue
            Member.create(
                [
                    {
                        "group_id": group.id,
                        "resolution_id": resolution.id,
                        "sequence": resolution.sequence,
                        "daily_units": (
                            group.picking_line_id.bulk_group_ids.filtered(
                                lambda candidate, group=group: candidate != group
                            )
                            .sorted(lambda candidate: (candidate.sequence, candidate.id))
                            .mapped("member_ids")
                            .filtered(
                                lambda member, resolution=resolution: (
                                    member.resolution_id == resolution
                                )
                            )[:1].daily_units
                            or resolution.daily_dose
                        ),
                    }
                    for resolution in resolutions
                ]
            )
        self._auto_recalculate_distribution()

    def _ensure_unlocked(self):
        if self.env.context.get("cdu_allocation_migration"):
            return
        if any(
            group.locked or group.batch_id.picking_confirmed_at for group in self
        ):
            raise UserError(_("Confirmed picking allocations are locked."))

    def write(self, vals):
        self._ensure_unlocked()
        return super().write(vals)

    def unlink(self):
        self._ensure_unlocked()
        return super().unlink()

    def _apply_priority_distribution(self):
        self.ensure_one()
        plan = self._build_priority_distribution_plan()

        self.member_ids.mapped("resolution_id.allocation_line_ids").filtered(
            lambda allocation: allocation.bulk_group_id == self
        ).unlink()

        members = self.member_ids.sorted(lambda member: (member.sequence, member.id))
        lots_by_id = {lot.id: lot for lot in self.lot_ids}
        Allocation = self.env["cdu.picking.patient.allocation"]
        for member in members:
            member_plan = plan.get(member.id, {})
            member.with_context(cdu_skip_auto_bulk_recalculation=True).write(
                {"requested_packs": sum(member_plan.values())}
            )
            for lot_id, quantity in member_plan.items():
                if quantity <= 0:
                    continue
                lot = lots_by_id[lot_id]
                Allocation.create(
                    {
                        "batch_id": self.batch_id.id,
                        "picking_line_id": self.picking_line_id.id,
                        "resolution_id": member.resolution_id.id,
                        "stock_option_id": lot.stock_option_id.id,
                        "quantity_packs": quantity,
                        "daily_units": member.daily_units,
                        "allocation_method": "bulk",
                        "bulk_group_id": self.id,
                        "bulk_member_id": member.id,
                        "sequence": lot.sequence,
                    }
                )

            resolution = member.resolution_id
            if resolution.supplied_units <= 0:
                resolution.write(
                    {
                        "status": "unserved",
                        "unserved_reason": "insufficient_stock",
                    }
                )
            else:
                resolution._refresh_status()

        self.distribution_stale = False

    def _clear_automatic_distribution(self):
        for group in self:
            allocations = group.member_ids.mapped(
                "resolution_id.allocation_line_ids"
            ).filtered(
                lambda allocation, group=group: allocation.bulk_group_id == group
            )
            allocations.unlink()
            for resolution in group.member_ids.mapped("resolution_id"):
                resolution.with_context(cdu_reset_coverage_confirmation=True).write(
                    {
                        "status": "draft",
                        "unserved_reason": False,
                        "unserved_note": False,
                        "confirmed_supplied_days": 0,
                        "coverage_confirmed": False,
                    }
                )
            group.distribution_stale = True

    def _auto_recalculate_distribution(self):
        if self.env.context.get("cdu_skip_auto_bulk_recalculation"):
            return
        for picking_line in self.mapped("picking_line_id"):
            if picking_line.batch_id.picking_confirmed_at:
                continue
            if picking_line.allocation_strategy == "manual":
                picking_line.bulk_group_ids.with_context(
                    cdu_skip_auto_bulk_recalculation=True
                ).write({"distribution_stale": True})
                continue
            picking_line._apply_balanced_distribution()

    def action_distribute_by_priority(self):
        self.ensure_one()
        self._ensure_unlocked()
        if not self.member_ids:
            raise ValidationError(_("No prescriptions were found for this group."))
        if not self.lot_ids:
            raise ValidationError(_("Add at least one stock lot to this group."))
        if any(lot.quantity_packs <= 0 for lot in self.lot_ids):
            raise ValidationError(
                _("Every selected lot must have a positive maximum pack quantity.")
            )

        self.picking_line_id.action_reset_balanced_allocation()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Balanced FEFO allocation updated"),
                "message": _(
                    "Whole packs were balanced across patients using valid "
                    "earliest-expiry stock and complete regimen components."
                ),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def _sorted_distribution_lots(self):
        self.ensure_one()
        no_expiry = fields.Date.to_date("9999-12-31")
        return self.lot_ids.sorted(
            lambda lot: (
                lot.expiration_date or no_expiry,
                lot.sequence,
                lot.id,
            )
        )

    def _available_packs_by_lot(self):
        """Return selected capacity after allocations made outside this group."""
        self.ensure_one()
        options = self.lot_ids.mapped("stock_option_id")
        allocated_elsewhere = {}
        if options:
            allocations = self.env["cdu.picking.patient.allocation"].search(
                [
                    ("batch_id", "=", self.batch_id.id),
                    ("stock_option_id", "in", options.ids),
                ]
            )
            for allocation in allocations:
                if allocation.bulk_group_id == self:
                    continue
                option_id = allocation.stock_option_id.id
                allocated_elsewhere[option_id] = (
                    allocated_elsewhere.get(option_id, 0)
                    + allocation.quantity_packs
                )

        available = {}
        for lot in self.lot_ids:
            option_available = max(
                lot.available_packs
                - allocated_elsewhere.get(lot.stock_option_id.id, 0),
                0,
            )
            available[lot.id] = min(lot.quantity_packs, option_available)
        return available

    def _allocation_expiry_score(self, quantities, lot_data):
        used = [
            (index, quantity)
            for index, quantity in enumerate(quantities)
            if quantity > 0
        ]
        if not used:
            return (-1, 0, 0, tuple())
        latest_expiry_rank = max(
            lot_data[index]["expiry_rank"] for index, _quantity in used
        )
        weighted_expiry = sum(
            lot_data[index]["expiry_rank"]
            * quantity
            * lot_data[index]["pack_size"]
            for index, quantity in used
        )
        pack_count = sum(quantity for _index, quantity in used)
        # Prefer the earlier lot only as the final deterministic tie-breaker
        # when expiry, supplied units, excess, and pack count are identical.
        deterministic = tuple(-quantity for quantity in quantities)
        return (
            latest_expiry_rank,
            weighted_expiry,
            pack_count,
            deterministic,
        )

    def _find_priority_pack_combination(
        self,
        required_units,
        lots,
        available_by_lot,
    ):
        """Find a bounded whole-pack combination for one prescription.

        Full coverage is preferred over partial coverage. For full combinations,
        the priority is earliest expiry, then minimum excess, then fewer packs.
        Patient order is applied by the caller.
        """
        self.ensure_one()
        active_lots = [
            lot
            for lot in lots
            if lot.pack_size > 0 and available_by_lot.get(lot.id, 0) > 0
        ]
        if required_units <= 0 or not active_lots:
            return {}

        expiry_values = []
        no_expiry = fields.Date.to_date("9999-12-31")
        for lot in active_lots:
            expiry = lot.expiration_date or no_expiry
            if expiry not in expiry_values:
                expiry_values.append(expiry)
        expiry_rank_by_date = {
            expiry: index for index, expiry in enumerate(sorted(expiry_values))
        }
        lot_data = [
            {
                "lot": lot,
                "pack_size": lot.pack_size,
                "available": available_by_lot[lot.id],
                "expiry_rank": expiry_rank_by_date[
                    lot.expiration_date or no_expiry
                ],
            }
            for lot in active_lots
        ]

        max_pack_size = max(item["pack_size"] for item in lot_data)
        maximum_units = required_units + max_pack_size - 1
        empty_quantities = tuple(0 for _item in lot_data)
        states = {0: empty_quantities}

        for index, item in enumerate(lot_data):
            previous_states = dict(states)
            for supplied_units, quantities in previous_states.items():
                maximum_quantity = min(
                    item["available"],
                    (maximum_units - supplied_units) // item["pack_size"],
                )
                for quantity in range(1, maximum_quantity + 1):
                    total_units = supplied_units + quantity * item["pack_size"]
                    candidate = list(quantities)
                    candidate[index] = quantity
                    candidate = tuple(candidate)
                    existing = states.get(total_units)
                    if existing is None or self._allocation_expiry_score(
                        candidate, lot_data
                    ) < self._allocation_expiry_score(existing, lot_data):
                        states[total_units] = candidate

        full_candidates = [
            (supplied_units, quantities)
            for supplied_units, quantities in states.items()
            if supplied_units >= required_units
        ]
        if full_candidates:
            supplied_units, quantities = min(
                full_candidates,
                key=lambda candidate: (
                    self._allocation_expiry_score(candidate[1], lot_data)[0],
                    self._allocation_expiry_score(candidate[1], lot_data)[1],
                    candidate[0] - required_units,
                    self._allocation_expiry_score(candidate[1], lot_data)[2],
                    self._allocation_expiry_score(candidate[1], lot_data)[3],
                ),
            )
        else:
            supplied_units = max(states)
            quantities = states[supplied_units]

        return {
            item["lot"].id: quantity
            for item, quantity in zip(lot_data, quantities)
            if quantity > 0
        }

    def _build_priority_distribution_plan(self):
        self.ensure_one()
        lots = self._sorted_distribution_lots()
        available_by_lot = self._available_packs_by_lot()
        plan = {}
        for member in self.member_ids.sorted(
            lambda candidate: (candidate.sequence, candidate.id)
        ):
            member_plan = self._find_priority_pack_combination(
                member.required_units,
                lots,
                available_by_lot,
            )
            plan[member.id] = member_plan
            for lot_id, quantity in member_plan.items():
                available_by_lot[lot_id] -= quantity
        return plan

    def _get_distribution_errors(self):
        self.ensure_one()
        errors = []
        if self.distribution_stale:
            errors.append(
                _(
                    "%s: automatic allocation is pending; save the selected stock "
                    "or daily-unit changes."
                )
                % self.name
            )
        if not self.member_ids:
            return [_("%s: add at least one prescription.") % self.name]
        if not self.lot_ids:
            return [_("%s: add at least one stock lot.") % self.name]
        if self.picking_line_id.allocation_strategy == "manual":
            return errors

        expected = {
            key: quantity
            for key, quantity in self.picking_line_id._distribution_signature_from_plan(
                self.picking_line_id._build_balanced_distribution_plan()
            ).items()
            if key[1] == self.id
        }
        actual = {
            key: quantity
            for key, quantity in self.picking_line_id._actual_bulk_distribution_signature().items()
            if key[1] == self.id
        }
        if expected != actual:
            errors.append(
                _(
                    "%s: the Balanced FEFO allocation is out of date with the "
                    "selected stock or current patient requirements."
                )
                % self.name
            )
        return errors

    def action_back_to_regimen_overview(self):
        self.ensure_one()
        wizard = self.env["cdu.elmis.picking.wizard"].create(
            {"batch_id": self.batch_id.id, "step": "overview"}
        )
        return wizard._open_action()

    def action_allocate_or_reallocate(self):
        self.ensure_one()
        self._ensure_unlocked()
        self._populate_prescriptions()
        self._auto_recalculate_distribution()
        return self.picking_line_id.action_open_allocation_board()

    def _open_allocation_action(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("%s - Allocate or Reallocate")
            % self.picking_line_id.openmrs_drug_name,
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "view_id": self.env.ref(
                "cdu_elmis.view_cdu_picking_bulk_group_form"
            ).id,
            "target": "new",
            "context": {
                "default_batch_id": self.batch_id.id,
                "default_picking_line_id": self.picking_line_id.id,
            },
        }


class CduPickingBulkMember(models.Model):
    _name = "cdu.picking.bulk.member"
    _description = "CDU Regimen Component Prescription"
    _order = "group_id, sequence, id"

    group_id = fields.Many2one(
        "cdu.picking.bulk.group", required=True, ondelete="cascade", index=True
    )
    batch_id = fields.Many2one(related="group_id.batch_id", store=True, readonly=True)
    picking_line_id = fields.Many2one(
        related="group_id.picking_line_id", store=True, readonly=True
    )
    resolution_id = fields.Many2one(
        "cdu.picking.patient.resolution",
        string="Prescription",
        required=True,
        ondelete="restrict",
        domain="[('picking_line_id', '=', picking_line_id)]",
    )
    prescription_id = fields.Many2one(
        related="resolution_id.prescription_id", store=True, readonly=True
    )
    patient_id = fields.Many2one(
        related="resolution_id.patient_id", store=True, readonly=True
    )
    sequence = fields.Integer(string="Priority", default=10)
    target_days = fields.Integer(
        related="resolution_id.target_days",
        string="Target Days",
        readonly=True,
    )
    calculated_packs = fields.Integer(
        compute="_compute_calculated_packs",
        string="Minimum Pack Estimate",
    )
    daily_units = fields.Integer(
        string="Daily Units",
        required=True,
        default=1,
    )
    original_daily_units = fields.Integer(
        string="Original Daily Units",
        required=True,
        default=1,
        readonly=True,
        copy=False,
    )
    daily_units_changed_by = fields.Many2one(
        "res.users", string="Daily Units Changed By", readonly=True, copy=False
    )
    daily_units_changed_at = fields.Datetime(
        string="Daily Units Changed At", readonly=True, copy=False
    )
    required_units = fields.Integer(
        string="Required Units",
        compute="_compute_required_units",
    )
    requested_packs = fields.Integer(
        string="Planned Packs",
        required=True,
        default=0,
    )
    allocated_packs = fields.Integer(
        compute="_compute_component_totals", readonly=True
    )
    supplied_units = fields.Float(
        compute="_compute_component_totals",
        string="Supplied Units",
        readonly=True,
    )
    coverage_days = fields.Float(
        compute="_compute_component_totals",
        string="Coverage Days",
        readonly=True,
    )
    excess_units = fields.Integer(
        string="Excess Units",
        compute="_compute_required_units",
    )
    allocation_status = fields.Selection(
        [
            ("draft", "Not Allocated"),
            ("full", "Full"),
            ("partial", "Partial"),
            ("unserved", "Unserved"),
        ],
        compute="_compute_component_totals",
        readonly=True,
    )

    _sql_constraints = [
        (
            "unique_bulk_resolution",
            "unique(group_id, resolution_id)",
            "A prescription can only appear once in a regimen component.",
        ),
    ]

    @api.depends(
        "daily_units",
        "target_days",
        "resolution_id.allocation_line_ids.quantity_packs",
        "resolution_id.allocation_line_ids.selected_pack_size",
        "resolution_id.allocation_line_ids.bulk_member_id",
    )
    def _compute_component_totals(self):
        for member in self:
            allocations = member.resolution_id.allocation_line_ids.filtered(
                lambda allocation, member=member: (
                    allocation.bulk_member_id == member
                )
            )
            member.allocated_packs = sum(allocations.mapped("quantity_packs"))
            supplied_units = sum(
                allocation.quantity_packs * allocation.selected_pack_size
                for allocation in allocations
            )
            member.supplied_units = supplied_units
            coverage_days = (
                supplied_units / member.daily_units
                if member.daily_units > 0
                else 0
            )
            member.coverage_days = coverage_days
            if not allocations:
                member.allocation_status = "draft"
            elif coverage_days >= member.target_days:
                member.allocation_status = "full"
            elif supplied_units > 0:
                member.allocation_status = "partial"
            else:
                member.allocation_status = "unserved"

    @api.depends(
        "resolution_id.patient_line_id.effective_repeat_days",
        "resolution_id.patient_line_id.prescription_repeat_days",
        "resolution_id.patient_line_id.repeat_days",
        "group_id.pack_size",
        "group_id.lot_ids.stock_option_id.pack_size",
        "daily_units",
    )
    def _compute_calculated_packs(self):
        for member in self:
            target_days = (
                member.resolution_id.patient_line_id.effective_repeat_days
                or member.resolution_id.patient_line_id.prescription_repeat_days
                or member.resolution_id.patient_line_id.repeat_days
                or 0
            )
            pack_sizes = [
                pack_size
                for pack_size in member.group_id.lot_ids.mapped("pack_size")
                if pack_size > 0
            ]
            if not pack_sizes and member.group_id.pack_size > 0:
                pack_sizes = [member.group_id.pack_size]
            largest_pack_size = max(pack_sizes) if pack_sizes else 0
            member.calculated_packs = (
                math.ceil(
                    target_days
                    * member.daily_units
                    / largest_pack_size
                )
                if target_days > 0 and largest_pack_size > 0
                else 0
            )

    @api.depends(
        "target_days",
        "daily_units",
        "supplied_units",
    )
    def _compute_required_units(self):
        for member in self:
            member.required_units = member.target_days * member.daily_units
            member.excess_units = max(
                int(member.supplied_units or 0) - member.required_units,
                0,
            )

    @api.constrains(
        "requested_packs", "resolution_id", "group_id", "daily_units"
    )
    def _check_member(self):
        for member in self:
            if member.requested_packs < 0:
                raise ValidationError(_("Patient pack quantities cannot be negative."))
            if member.resolution_id.picking_line_id != member.group_id.picking_line_id:
                raise ValidationError(
                    _("The prescription does not belong to this regimen.")
                )
            if member.daily_units <= 0:
                raise ValidationError(
                    _("Daily units for this product must be a whole number greater than zero.")
                )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            group = self.env["cdu.picking.bulk.group"].browse(vals.get("group_id"))
            group._ensure_unlocked()
            daily_units = self.env[
                "cdu.picking.patient.resolution"
            ]._validated_positive_integer(
                vals.get("daily_units", 1),
                _("Daily units for this product"),
            )
            vals["daily_units"] = daily_units
            vals["original_daily_units"] = daily_units
            resolution_id = vals.get("resolution_id")
            if resolution_id and self.search_count(
                [
                    ("group_id", "=", group.id),
                    ("resolution_id", "=", resolution_id),
                ]
            ):
                raise ValidationError(
                    _(
                        "A prescription can only appear once in a regimen "
                        "component."
                    )
                )
            if "requested_packs" not in vals and group.pack_size > 0:
                resolution = self.env["cdu.picking.patient.resolution"].browse(
                    vals.get("resolution_id")
                )
                target_days = (
                    resolution.patient_line_id.effective_repeat_days
                    or resolution.patient_line_id.prescription_repeat_days
                    or resolution.patient_line_id.repeat_days
                    or 0
                )
                vals["requested_packs"] = (
                    math.ceil(
                        target_days * daily_units / group.pack_size
                    )
                    if target_days > 0
                    else 0
                )
        records = super().create(vals_list)
        groups = records.mapped("group_id")
        groups.write({"distribution_stale": True})
        groups._auto_recalculate_distribution()
        return records

    def write(self, vals):
        self.mapped("group_id")._ensure_unlocked()
        values = dict(vals)
        if "daily_units" in values:
            values["daily_units"] = self.env[
                "cdu.picking.patient.resolution"
            ]._validated_positive_integer(
                values["daily_units"], _("Daily units for this product")
            )
            if any(
                member.daily_units != values["daily_units"]
                for member in self
            ):
                values.update(
                    {
                        "daily_units_changed_by": self.env.user.id,
                        "daily_units_changed_at": fields.Datetime.now(),
                    }
                )
        result = super().write(values)
        if "daily_units" in values:
            allocations = self.mapped(
                "resolution_id.allocation_line_ids"
            ).filtered(lambda allocation: allocation.bulk_member_id in self)
            if allocations:
                for member in self:
                    member_allocations = allocations.filtered(
                        lambda allocation, member=member: (
                            allocation.bulk_member_id == member
                        )
                    )
                    member_allocations.write(
                        {"daily_units": member.daily_units}
                    )
            groups = self.mapped("group_id")
            groups.write({"distribution_stale": True})
            groups._auto_recalculate_distribution()
        elif {"sequence", "resolution_id"}.intersection(values):
            groups = self.mapped("group_id")
            groups.write({"distribution_stale": True})
            groups._auto_recalculate_distribution()
        return result

    def unlink(self):
        groups = self.mapped("group_id")
        groups._ensure_unlocked()
        allocations = self.mapped("resolution_id.allocation_line_ids").filtered(
            lambda allocation: allocation.bulk_member_id in self
        )
        allocations.unlink()
        result = super().unlink()
        groups = groups.exists()
        groups.write({"distribution_stale": True})
        groups._auto_recalculate_distribution()
        return result

    @api.onchange("resolution_id")
    def _onchange_resolution_id(self):
        for member in self:
            member.requested_packs = member.calculated_packs


class CduPickingBulkLot(models.Model):
    _name = "cdu.picking.bulk.lot"
    _description = "CDU Regimen Component Selected Lot"
    _order = "group_id, sequence, id"

    group_id = fields.Many2one(
        "cdu.picking.bulk.group", required=True, ondelete="cascade", index=True
    )
    batch_id = fields.Many2one(related="group_id.batch_id", store=True, readonly=True)
    stock_option_id = fields.Many2one(
        "cdu.elmis.stock.option",
        string="eLMIS Lot",
        required=True,
        ondelete="restrict",
        domain="['&', '&', '&', ('batch_id', '=', batch_id), ('stock_on_hand', '>', 0), ('pack_size', '>', 0), '|', ('expiration_date', '=', False), ('expiration_date', '>=', context_today().strftime('%Y-%m-%d'))]",
    )
    sequence = fields.Integer(string="Lot Sequence", default=10)
    expiration_date = fields.Date(
        related="stock_option_id.expiration_date", readonly=True
    )
    orderable_name = fields.Char(
        related="stock_option_id.orderable_name",
        string="Product",
        readonly=True,
    )
    pack_size = fields.Integer(
        related="stock_option_id.pack_size",
        string="Pack Size",
        readonly=True,
    )
    lot = fields.Char(related="stock_option_id.lot", readonly=True)
    available_packs = fields.Integer(
        related="stock_option_id.stock_on_hand", readonly=True
    )
    quantity_packs = fields.Integer(
        string="Maximum Packs to Use",
        required=True,
        default=1,
        help="Maximum packs from this selected stock row that the allocator may use.",
    )
    allocated_packs = fields.Integer(
        string="Allocated Packs",
        compute="_compute_allocation_totals",
    )
    remaining_selected_packs = fields.Integer(
        string="Remaining Selected Packs",
        compute="_compute_allocation_totals",
    )

    _sql_constraints = [
        (
            "unique_bulk_group_lot",
            "unique(group_id, stock_option_id)",
            "A stock lot can only appear once in a regimen component.",
        ),
    ]

    @api.constrains("stock_option_id", "quantity_packs")
    def _check_lot(self):
        for lot in self:
            lot.group_id._ensure_unlocked()
            option = lot.stock_option_id
            if lot.quantity_packs <= 0:
                raise ValidationError(
                    _("Maximum packs must be a positive whole number.")
                )
            if lot.quantity_packs > option.stock_on_hand:
                raise ValidationError(
                    _("Maximum packs cannot exceed the available packs for this lot.")
                )
            if option.batch_id != lot.group_id.batch_id:
                raise ValidationError(
                    _("The selected stock option belongs to another batch.")
                )
            if (
                option.expiration_date
                and option.expiration_date < fields.Date.context_today(lot)
            ):
                raise ValidationError(
                    _(
                        "%(product)s / %(batch)s is expired and cannot be "
                        "selected for picking."
                    )
                    % {
                        "product": option.orderable_name,
                        "batch": option.lot or _("No batch"),
                    }
                )
            duplicate_component = self.search(
                [
                    ("id", "!=", lot.id),
                    ("group_id.picking_line_id", "=", lot.group_id.picking_line_id.id),
                    ("group_id", "!=", lot.group_id.id),
                    ("stock_option_id", "=", option.id),
                ],
                limit=1,
            )
            if duplicate_component:
                raise ValidationError(
                    _(
                        "The same eLMIS lot cannot be selected in more than one "
                        "regimen component."
                    )
                )

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            option = self.env["cdu.elmis.stock.option"].browse(
                values.get("stock_option_id")
            )
            if option and "quantity_packs" not in values:
                values["quantity_packs"] = option.stock_on_hand or 1
        records = super().create(vals_list)
        for group in records.mapped("group_id"):
            for index, record in enumerate(
                group.lot_ids.sorted(
                    lambda lot: (
                        lot.expiration_date or fields.Date.to_date("9999-12-31"),
                        lot.id,
                    )
                ),
                start=1,
            ):
                record.with_context(
                    cdu_skip_auto_bulk_recalculation=True
                ).sequence = index * 10
            group.write({"distribution_stale": True})
            group._auto_recalculate_distribution()
        return records

    def write(self, vals):
        groups = self.mapped("group_id")
        groups._ensure_unlocked()
        result = super().write(vals)
        if {"stock_option_id", "quantity_packs", "sequence"}.intersection(vals):
            groups.write({"distribution_stale": True})
            groups._auto_recalculate_distribution()
        return result

    def unlink(self):
        groups = self.mapped("group_id")
        groups._ensure_unlocked()
        result = super().unlink()
        groups = groups.exists()
        groups.write({"distribution_stale": True})
        groups._auto_recalculate_distribution()
        return result

    @api.onchange("stock_option_id")
    def _onchange_stock_option_id(self):
        for lot in self:
            if lot.stock_option_id:
                lot.quantity_packs = lot.stock_option_id.stock_on_hand or 1

    @api.depends(
        "group_id.member_ids.resolution_id.allocation_line_ids.quantity_packs",
        "group_id.member_ids.resolution_id.allocation_line_ids.stock_option_id",
        "stock_option_id",
        "quantity_packs",
    )
    def _compute_allocation_totals(self):
        for lot in self:
            allocations = lot.group_id.member_ids.mapped(
                "resolution_id.allocation_line_ids"
            ).filtered(
                lambda allocation, lot=lot: (
                    allocation.bulk_group_id == lot.group_id
                    and allocation.stock_option_id == lot.stock_option_id
                )
            )
            lot.allocated_packs = sum(allocations.mapped("quantity_packs"))
            lot.remaining_selected_packs = max(
                min(lot.quantity_packs, lot.available_packs)
                - lot.allocated_packs,
                0,
            )
