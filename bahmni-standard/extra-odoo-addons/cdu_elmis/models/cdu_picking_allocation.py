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
        string="Bulk Group Member",
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
        "allocation_line_ids.selected_orderable_id",
        "allocation_line_ids.selected_orderable_code",
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
        [("bulk", "Bulk"), ("individual", "Individual")],
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
    _description = "CDU Bulk Prescription Picking Group"
    _order = "picking_line_id, sequence, id"

    name = fields.Char(required=True, default=lambda self: _("Bulk Group"))
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
        string="Product / Pack Size",
        required=True,
        ondelete="restrict",
        domain="[('batch_id', '=', batch_id), ('stock_on_hand', '>', 0), ('pack_size', '>', 0)]",
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
        compute="_compute_totals", string="Selected Lot Packs"
    )
    allocated_packs = fields.Integer(
        compute="_compute_totals", string="Assigned Packs"
    )
    distribution_stale = fields.Boolean(
        string="Distribution Needs Reapplying", default=False, copy=False
    )
    locked = fields.Boolean(default=False, copy=False, index=True)
    legacy = fields.Boolean(default=False, copy=False, index=True)

    @api.depends(
        "picking_line_id.resolution_ids",
        "picking_line_id.bulk_group_ids.member_ids.resolution_id",
    )
    def _compute_eligible_resolution_ids(self):
        for group in self:
            used = group.picking_line_id.bulk_group_ids.filtered(
                lambda candidate: candidate != group
            ).mapped("member_ids.resolution_id")
            group.eligible_resolution_ids = group.picking_line_id.resolution_ids - used

    @api.depends(
        "member_ids.requested_packs",
        "member_ids.allocated_packs",
        "lot_ids.quantity_packs",
    )
    def _compute_totals(self):
        for group in self:
            group.requested_packs = sum(group.member_ids.mapped("requested_packs"))
            group.picked_packs = sum(group.lot_ids.mapped("quantity_packs"))
            group.allocated_packs = sum(group.member_ids.mapped("allocated_packs"))

    @api.constrains("batch_id", "picking_line_id", "product_stock_option_id")
    def _check_group(self):
        for group in self:
            if group.picking_line_id.batch_id != group.batch_id:
                raise ValidationError(_("The bulk group belongs to another batch."))
            if group.picking_line_id.allocation_mode != "bulk":
                raise ValidationError(_("Bulk groups require Bulk allocation mode."))
            if (
                group.product_stock_option_id
                and group.product_stock_option_id.batch_id != group.batch_id
            ):
                raise ValidationError(
                    _("The selected product belongs to another batch.")
                )

    def _ensure_unlocked(self):
        if self.env.context.get("cdu_allocation_migration"):
            return
        if any(
            group.locked or group.batch_id.picking_confirmed_at for group in self
        ):
            raise UserError(_("Confirmed picking allocations are locked."))

    def write(self, vals):
        self._ensure_unlocked()
        if "product_stock_option_id" in vals and any(
            group.member_ids or group.lot_ids for group in self
        ):
            raise UserError(
                _(
                    "Remove this group's prescriptions and lots before changing "
                    "its product or pack size."
                )
            )
        return super().write(vals)

    def unlink(self):
        self._ensure_unlocked()
        return super().unlink()

    def action_distribute_by_priority(self):
        self.ensure_one()
        self._ensure_unlocked()
        if not self.member_ids:
            raise ValidationError(_("Add at least one prescription to this group."))
        if not self.lot_ids:
            raise ValidationError(_("Add at least one stock lot to this group."))
        if any(lot.quantity_packs <= 0 for lot in self.lot_ids):
            raise ValidationError(_("Every selected lot must contain picked packs."))

        self.member_ids.mapped("resolution_id.allocation_line_ids").filtered(
            lambda allocation: allocation.bulk_group_id == self
        ).unlink()

        members = self.member_ids.sorted(lambda member: (member.sequence, member.id))
        lots = self.lot_ids.sorted(lambda lot: (lot.sequence, lot.id))
        remaining_by_lot = {
            lot.id: lot.quantity_packs for lot in lots
        }
        Allocation = self.env["cdu.picking.patient.allocation"]
        for member in members:
            remaining_request = member.requested_packs
            for lot in lots:
                available = remaining_by_lot[lot.id]
                if remaining_request <= 0 or available <= 0:
                    continue
                quantity = min(remaining_request, available)
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
                remaining_request -= quantity
                remaining_by_lot[lot.id] -= quantity

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

        unassigned = sum(remaining_by_lot.values())
        if unassigned:
            raise ValidationError(
                _(
                    "%s picked packs remain unassigned. Reduce the lot quantities "
                    "or increase patient pack allocations."
                )
                % unassigned
            )
        self.distribution_stale = False
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Bulk allocation updated"),
                "message": _(
                    "Packs were assigned by prescription priority and lot sequence."
                ),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def _get_distribution_errors(self):
        self.ensure_one()
        errors = []
        if self.distribution_stale:
            errors.append(
                _(
                    "%s: daily units changed; review patient pack quantities "
                    "and apply the priority distribution again."
                )
                % self.name
            )
        if not self.member_ids:
            return [_("%s: add at least one prescription.") % self.name]
        if not self.lot_ids:
            return [_("%s: add at least one stock lot.") % self.name]
        members = self.member_ids.sorted(lambda member: (member.sequence, member.id))
        lots = self.lot_ids.sorted(lambda lot: (lot.sequence, lot.id))
        remaining_by_lot = {lot.id: lot.quantity_packs for lot in lots}
        expected = {}
        for member in members:
            request = member.requested_packs
            for lot in lots:
                quantity = min(request, remaining_by_lot[lot.id])
                if quantity > 0:
                    expected[(member.id, lot.stock_option_id.id)] = quantity
                    request -= quantity
                    remaining_by_lot[lot.id] -= quantity

        actual = {
            (allocation.bulk_member_id.id, allocation.stock_option_id.id): (
                allocation.quantity_packs
            )
            for allocation in self.env["cdu.picking.patient.allocation"].search(
                [("bulk_group_id", "=", self.id)]
            )
        }
        if expected != actual or any(remaining_by_lot.values()):
            errors.append(
                _(
                    "%s: apply the priority distribution again so every "
                    "selected lot pack is assigned to the current patient list."
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


class CduPickingBulkMember(models.Model):
    _name = "cdu.picking.bulk.member"
    _description = "CDU Bulk Picking Group Prescription"
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
    calculated_packs = fields.Integer(compute="_compute_calculated_packs")
    daily_units = fields.Integer(
        string="Daily Units for This Product",
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
    requested_packs = fields.Integer(string="Packs for Patient", required=True)
    allocated_packs = fields.Integer(
        related="resolution_id.allocated_packs", readonly=True
    )
    allocation_status = fields.Selection(
        related="resolution_id.status", readonly=True
    )

    _sql_constraints = [
        (
            "unique_bulk_resolution",
            "unique(picking_line_id, resolution_id)",
            "A prescription can only belong to one bulk group for this regimen.",
        ),
    ]

    @api.depends(
        "resolution_id.patient_line_id.effective_repeat_days",
        "resolution_id.patient_line_id.prescription_repeat_days",
        "resolution_id.patient_line_id.repeat_days",
        "group_id.pack_size",
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
            member.calculated_packs = (
                math.ceil(
                    target_days
                    * member.daily_units
                    / member.group_id.pack_size
                )
                if target_days > 0 and member.group_id.pack_size > 0
                else 0
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
                    ("picking_line_id", "=", group.picking_line_id.id),
                    ("resolution_id", "=", resolution_id),
                ]
            ):
                raise ValidationError(
                    _(
                        "A prescription can only belong to one bulk group "
                        "for this regimen."
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
        return super().create(vals_list)

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
            self.mapped("group_id").write({"distribution_stale": True})
        return result

    def unlink(self):
        self.mapped("group_id")._ensure_unlocked()
        allocations = self.mapped("resolution_id.allocation_line_ids").filtered(
            lambda allocation: allocation.bulk_member_id in self
        )
        allocations.unlink()
        return super().unlink()

    @api.onchange("resolution_id")
    def _onchange_resolution_id(self):
        for member in self:
            member.requested_packs = member.calculated_packs


class CduPickingBulkLot(models.Model):
    _name = "cdu.picking.bulk.lot"
    _description = "CDU Bulk Picking Selected Lot"
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
        domain="[('batch_id', '=', batch_id), ('stock_on_hand', '>', 0), ('pack_size', '>', 0)]",
    )
    sequence = fields.Integer(string="Lot Sequence", default=10)
    expiration_date = fields.Date(
        related="stock_option_id.expiration_date", readonly=True
    )
    lot = fields.Char(related="stock_option_id.lot", readonly=True)
    available_packs = fields.Integer(
        related="stock_option_id.stock_on_hand", readonly=True
    )
    quantity_packs = fields.Integer(string="Picked Packs", required=True, default=1)

    _sql_constraints = [
        (
            "unique_bulk_group_lot",
            "unique(group_id, stock_option_id)",
            "A stock lot can only appear once in a bulk group.",
        ),
    ]

    @api.constrains("stock_option_id", "quantity_packs")
    def _check_lot(self):
        for lot in self:
            lot.group_id._ensure_unlocked()
            option = lot.stock_option_id
            product = lot.group_id.product_stock_option_id
            if lot.quantity_packs <= 0:
                raise ValidationError(_("Picked packs must be a positive whole number."))
            if lot.quantity_packs > option.stock_on_hand:
                raise ValidationError(
                    _("Picked packs exceed the available packs for this lot.")
                )
            same_orderable = (
                option.orderable_id
                and product.orderable_id
                and option.orderable_id == product.orderable_id
            ) or (
                not option.orderable_id
                and not product.orderable_id
                and option.orderable_code == product.orderable_code
            )
            if not same_orderable or option.pack_size != product.pack_size:
                raise ValidationError(
                    _(
                        "All lots in a bulk group must use the selected product "
                        "and pack size."
                    )
                )

    @api.model_create_multi
    def create(self, vals_list):
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
                record.sequence = index * 10
        return records

    def write(self, vals):
        self.mapped("group_id")._ensure_unlocked()
        return super().write(vals)

    def unlink(self):
        self.mapped("group_id")._ensure_unlocked()
        return super().unlink()
