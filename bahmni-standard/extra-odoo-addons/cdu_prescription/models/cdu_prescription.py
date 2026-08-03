from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from .constants import E_LOCKER_DISTRICT_SELECTION


class CduPrescription(models.Model):
    _name = "cdu.prescription"
    _description = "CDU Prescription"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "prescription_date desc, create_date desc"

    name = fields.Char(
        default="/",
        copy=False,
        readonly=True,
        tracking=True,
    )
    source_key = fields.Char(copy=False, readonly=True, index=True)
    row_hash = fields.Char(copy=False, readonly=True, index=True)
    report_run_id = fields.Many2one("cdu.report.run", string="Report Run", readonly=True)
    report_row_id = fields.Many2one("cdu.report.row", string="Report Row", readonly=True)
    source_file_name = fields.Char(readonly=True)
    source_row_number = fields.Integer(readonly=True)
    source_report_generated_at = fields.Datetime(readonly=True)
    facility_id = fields.Many2one("cdu.facility", string="Facility", tracking=True)
    facility_name = fields.Char(string="Location", required=True, tracking=True)
    facility_code = fields.Char(string="Facility Code", tracking=True)
    batch_id = fields.Many2one(
        "cdu.batch",
        string="Workload Batch",
        ondelete="set null",
        copy=False,
        tracking=True,
    )
    prescription_date = fields.Date(required=True, tracking=True)
    patient_id = fields.Many2one(
        "res.partner",
        string="Patient",
        domain=[("customer_rank", ">", 0)],
        tracking=True,
    )
    patient_identifier = fields.Char(string="eRegister ID", tracking=True)
    hiv_program_id = fields.Char(string="HIV Program ID")
    national_id = fields.Char(string="National ID")
    patient_first_name = fields.Char(string="Patient Name")
    patient_date_of_birth = fields.Date(string="DOB")
    patient_gender = fields.Selection(
        [("male", "Male"), ("female", "Female"), ("other", "Other")]
    )
    patient_phone = fields.Char(string="Primary Contact")
    patient_address = fields.Text(string="Address")
    allergies = fields.Char()
    has_allergies = fields.Selection(
        [("yes", "Yes"), ("no", "No"), ("unknown", "Unknown")],
        string="Has Allergies",
    )
    hiv_diagnosis_date = fields.Date(string="HIV Diagnosis Date")
    latest_vl_collection_date = fields.Date(string="Latest VL Collection Date")
    latest_vl_result = fields.Char(string="Latest VL Result")
    regimen_prescribed_raw = fields.Char(string="Regimen Prescribed")
    dosage_instructions = fields.Text(string="Dosage Instructions")
    regimen_id = fields.Many2one(
        "cdu.regimen",
        string="Matched Regimen",
        readonly=True,
        copy=True,
    )
    regimen_option_id = fields.Many2one(
        "cdu.regimen.option",
        string="Dispensing Option",
        domain="[('regimen_id', '=', regimen_id), ('active', '=', True)]",
        copy=True,
        tracking=True,
    )
    regimen_mapping_status = fields.Selection(
        [
            ("missing", "No Regimen"),
            ("unmatched", "Regimen Not Configured"),
            ("matched", "Regimen Matched"),
        ],
        compute="_compute_regimen_mapping_status",
        string="Regimen Match",
    )
    medicine_line_ids = fields.One2many(
        "cdu.prescription.medicine.line",
        "prescription_id",
        string="Medicines",
        copy=True,
    )
    medicine_amended = fields.Boolean(
        string="Medicines Amended",
        copy=True,
        readonly=True,
        tracking=True,
    )
    medicine_amendment_note = fields.Text(
        string="Amendment Reason",
        copy=True,
        tracking=True,
        help="Briefly explain why the imported regimen medicines were changed.",
    )
    medicines_locked = fields.Boolean(
        compute="_compute_medicines_locked",
        string="Medicine Composition Locked",
    )
    new_or_revisit = fields.Selection(
        [("new", "New"), ("revisit", "Revisit"), ("restarted", "Restarted")],
        string="New or Revisit",
    )
    next_drug_pickup_date = fields.Date(string="Next Drug Pickup Date")
    drug_pickup_point_raw = fields.Char(string="Drug Pickup Point")
    e_locker_district = fields.Selection(
        E_LOCKER_DISTRICT_SELECTION,
        string="E-locker District",
        tracking=True,
    )
    collection_point_id = fields.Many2one("cdu.collection.point",
        tracking=True,
    )
    secondary_contact = fields.Char(string="Secondary Contact")
    prescriber_name = fields.Char(string="Prescriber Name")
    next_clinical_visit_date = fields.Date(string="Next Clinical Appointment Date")
    # cdu_days_supply = fields.Integer(compute="_compute_cdu_days_supply", store=True,
    # )
    facility_days_supply = fields.Integer(
        compute="_compute_prescription_durations", 
        store=True,
        string="Facility Dispensing Days"
    )
    cdu_days_supply = fields.Integer(
        compute="_compute_prescription_durations", 
        store=True,
        string="CDU Dispensing Days"
    )
    repeat_days = fields.Integer(
        string="Repeats in Days",
        tracking=True,
        help="Number of CDU repeat days requested for this prescription.",
    )
    total_days_supply = fields.Integer(
        compute="_compute_prescription_durations",
        store=True,
        string="Total Dispensing Days"
    )
    remaining_days_supply = fields.Integer(
        string="Remaining Days"
    )
    state = fields.Selection(
        [
            ("awaiting_verification", "Awaiting verification"),
            ("awaiting_validation", "Awaiting validation"),
            ("rejected_to_call_center", "Rejected to call center"),
            ("rejected_to_facility", "Rejected to facility"),
            ("awaiting_batching", "Awaiting batching"),
            ("awaiting_picking", "Awaiting picking"),
            ("awaiting_dispensing", "Awaiting dispensing"),
            ("awaiting_bagging_qa", "Awaiting bagging / QA"),
            ("awaiting_boxing", "Awaiting boxing"),
            ("awaiting_dispatch", "Awaiting dispatch"),
            ("dispatched", "Dispatched"),
            ("cancelled", "Cancelled"),
        ],
        default="awaiting_verification",
        required=True,
        tracking=True,
    )
    rejected_from_state = fields.Selection(
        [
            ("awaiting_verification", "Verification"),
            ("awaiting_validation", "Validation"),
            ("awaiting_dispensing", "Dispensing"),
        ],
        string="Rejected From",
        readonly=True,
        copy=False,
        tracking=True,
    )
    rejection_history_ids = fields.One2many(
        "cdu.prescription.rejection",
        "prescription_id",
        string="Rejection History",
        readonly=True,
    )
    active_rejection_id = fields.Many2one(
        "cdu.prescription.rejection",
        string="Active Rejection",
        readonly=True,
        copy=False,
    )
    rejection_reason = fields.Char(
        related="active_rejection_id.reason_display",
        string="Rejection Reasons",
        store=True,
        readonly=True,
    )
    rejection_destination = fields.Selection(
        related="active_rejection_id.destination",
        string="Rejected To",
        store=True,
        readonly=True,
    )
    verified_by = fields.Many2one("res.users", readonly=True)
    verified_at = fields.Datetime(readonly=True)
    validated_by = fields.Many2one("res.users", readonly=True)
    validated_at = fields.Datetime(readonly=True)
    validation_notes = fields.Text(string="Review Notes", tracking=True)

    _sql_constraints = [
        ("unique_source_key", "unique(source_key)", "This eRegister prescription has already been ingested."),
    ]


    def name_get(self):
        if self.env.context.get("display_regimen_prescribed_raw"):
            return [
                (
                    prescription.id,
                    prescription.regimen_prescribed_raw
                    or _("No regimen"),
                )
                for prescription in self
            ]
        return super().name_get()

    @api.model
    def name_search(self, name="", args=None, operator="ilike", limit=100):
        if self.env.context.get("display_regimen_prescribed_raw"):
            args = list(args or [])
            if name:
                args.append(("regimen_prescribed_raw", operator, name))
            prescriptions = self.search(args, limit=limit)
            return prescriptions.name_get()
        return super().name_search(name=name, args=args, operator=operator, limit=limit)

    @api.model
    def create(self, vals):

        if vals.get("name", "/") == "/":
            vals["name"] = (self.env["ir.sequence"].next_by_code("cdu.prescription") or "/")

        if vals.get("facility_id"):
            facility = self.env["cdu.facility"].browse(vals["facility_id"])
            vals.setdefault("facility_name",facility.name)
            vals.setdefault("facility_code",facility.code)

        if vals.get("patient_id"):
            vals.update(self._patient_snapshot_values(vals["patient_id"], vals))

        prescription = super().create(vals)
        if not vals.get("medicine_line_ids"):
            prescription.with_context(
                cdu_skip_medicine_audit=True,
                cdu_skip_regimen_sync=True,
            )._sync_regimen_from_raw()
        prescription._sync_repeat_days_from_cdu_days()
        prescription._check_required_next_drug_pickup_date()

        return prescription

    def write(self, vals):
        composition_fields = {"regimen_prescribed_raw", "regimen_option_id"}
        if composition_fields.intersection(vals):
            self._ensure_medicine_composition_editable()
        previous_regimens = {
            prescription.id: prescription.regimen_prescribed_raw
            for prescription in self
        }
        previous_options = {
            prescription.id: prescription.regimen_option_id.id
            for prescription in self
        }
        if vals.get("facility_id"):
            facility = self.env["cdu.facility"].browse(vals["facility_id"])
            vals.setdefault("facility_name", facility.name)
            vals.setdefault("facility_code", facility.code)
        
        if vals.get("patient_id"):
            vals.update(self._patient_snapshot_values(vals["patient_id"], vals))

        result = super().write(vals)

        if not self.env.context.get("cdu_skip_regimen_sync"):
            if "regimen_prescribed_raw" in vals and "medicine_line_ids" not in vals:
                changed = self.filtered(
                    lambda prescription: (
                        (previous_regimens[prescription.id] or "").strip()
                        != (prescription.regimen_prescribed_raw or "").strip()
                    )
                )
                changed.with_context(
                    cdu_skip_medicine_audit=True,
                    cdu_skip_regimen_sync=True,
                )._sync_regimen_from_raw()
                if changed:
                    changed.with_context(cdu_skip_regimen_sync=True).write(
                        {"medicine_amended": True}
                    )
                    changed._invalidate_unconfirmed_picking()
                    for prescription in changed:
                        prescription.message_post(
                            body=_("The prescribed regimen was changed and its medicine list was refreshed.")
                        )
            elif "regimen_option_id" in vals and "medicine_line_ids" not in vals:
                changed = self.filtered(
                    lambda prescription: (
                        previous_options[prescription.id]
                        != prescription.regimen_option_id.id
                    )
                )
                changed.with_context(
                    cdu_skip_medicine_audit=True,
                    cdu_skip_regimen_sync=True,
                )._replace_regimen_medicine_lines()
                if changed:
                    changed.with_context(cdu_skip_regimen_sync=True).write(
                        {"medicine_amended": True}
                    )
                    changed._invalidate_unconfirmed_picking()
                    for prescription in changed:
                        prescription.message_post(
                            body=_("The regimen dispensing option was changed.")
                        )

            if "dosage_instructions" in vals:
                unchanged = self.filtered(lambda prescription: not prescription.medicine_amended)
                unchanged.medicine_line_ids.filtered(
                    lambda line: line.source == "regimen"
                ).with_context(cdu_skip_medicine_audit=True).write(
                    {"dosage_instructions": vals.get("dosage_instructions") or False}
                )

        duration_fields = {
            "prescription_date",
            "next_drug_pickup_date",
            "next_clinical_visit_date",
        }
        if duration_fields.intersection(vals) and "repeat_days" not in vals:
            self._sync_repeat_days_from_cdu_days()

        if "next_drug_pickup_date" in vals:
            self._check_required_next_drug_pickup_date()
        return result

    def copy(self, default=None):
        # A back-order or other system copy represents the same clinical
        # prescription. Copy its medicine rows without recording a new manual
        # amendment merely because the ORM recreated the one2many records.
        return super(
            CduPrescription,
            self.with_context(cdu_skip_medicine_audit=True),
        ).copy(default)

    @api.depends("regimen_prescribed_raw", "regimen_id")
    def _compute_regimen_mapping_status(self):
        for prescription in self:
            if not (prescription.regimen_prescribed_raw or "").strip():
                prescription.regimen_mapping_status = "missing"
            elif prescription.regimen_id:
                prescription.regimen_mapping_status = "matched"
            else:
                prescription.regimen_mapping_status = "unmatched"

    @api.depends("batch_id")
    def _compute_medicines_locked(self):
        for prescription in self:
            batch = prescription.batch_id
            prescription.medicines_locked = bool(
                batch
                and "picking_confirmed_at" in batch._fields
                and batch.picking_confirmed_at
            )

    def _ensure_medicine_composition_editable(self):
        # Read the confirmation boundary directly. The non-stored UI helper may
        # be cached when picking is confirmed in the same transaction.
        locked = self.filtered(
            lambda prescription: (
                prescription.batch_id
                and "picking_confirmed_at" in prescription.batch_id._fields
                and prescription.batch_id.picking_confirmed_at
            )
        )
        if locked:
            raise ValidationError(
                _(
                    "The medicines are locked because picking has been confirmed. "
                    "Reverse the confirmed picking before changing the prescription composition."
                )
            )

    def _invalidate_unconfirmed_picking(self):
        batches = self.mapped("batch_id")
        for batch in batches:
            if "picking_confirmed_at" in batch._fields and batch.picking_confirmed_at:
                continue
            batch._invalidate_picking_after_prescription_change()

    def _sync_regimen_from_raw(self):
        Regimen = self.env["cdu.regimen"]
        for prescription in self:
            regimen = Regimen.find_exact(prescription.regimen_prescribed_raw)
            option = regimen._get_default_option() if regimen else self.env["cdu.regimen.option"]
            prescription.with_context(cdu_skip_regimen_sync=True).write(
                {
                    "regimen_id": regimen.id if regimen else False,
                    "regimen_option_id": option.id if option else False,
                }
            )
            prescription._replace_regimen_medicine_lines()

    def _replace_regimen_medicine_lines(self):
        Line = self.env["cdu.prescription.medicine.line"]
        for prescription in self:
            existing_regimen_lines = prescription.medicine_line_ids.filtered(
                lambda line: line.source == "regimen"
            )
            dosage_by_medicine = {
                line.medicine_id.id: line.dosage_instructions
                for line in existing_regimen_lines
            }
            manual_medicine_ids = set(
                prescription.medicine_line_ids.filtered(
                    lambda line: line.source == "manual"
                ).mapped("medicine_id").ids
            )
            existing_regimen_lines.with_context(cdu_skip_medicine_audit=True).unlink()
            option = prescription.regimen_option_id
            if not option:
                continue
            values = []
            for option_line in option.line_ids.sorted(lambda line: (line.sequence, line.id)):
                if option_line.medicine_id.id in manual_medicine_ids:
                    continue
                values.append(
                    {
                        "prescription_id": prescription.id,
                        "sequence": option_line.sequence,
                        "medicine_id": option_line.medicine_id.id,
                        "dosage_instructions": dosage_by_medicine.get(
                            option_line.medicine_id.id,
                            prescription.dosage_instructions or False,
                        ),
                        "source": "regimen",
                        "source_option_line_id": option_line.id,
                    }
                )
            if values:
                Line.with_context(cdu_skip_medicine_audit=True).create(values)

    @api.onchange("regimen_prescribed_raw")
    def _onchange_regimen_prescribed_raw(self):
        for prescription in self:
            regimen = self.env["cdu.regimen"].find_exact(
                prescription.regimen_prescribed_raw
            )
            prescription.regimen_id = regimen
            prescription.regimen_option_id = (
                regimen._get_default_option() if regimen else False
            )
            prescription._onchange_regimen_option_id()

    @api.onchange("regimen_option_id")
    def _onchange_regimen_option_id(self):
        for prescription in self:
            option = prescription.regimen_option_id
            if not option:
                prescription.medicine_line_ids = [(5, 0, 0)]
                continue
            prescription.medicine_line_ids = [(5, 0, 0)] + [
                (
                    0,
                    0,
                    {
                        "sequence": option_line.sequence,
                        "medicine_id": option_line.medicine_id.id,
                        "dosage_instructions": prescription.dosage_instructions or False,
                        "source": "regimen",
                        "source_option_line_id": option_line.id,
                    },
                )
                for option_line in option.line_ids.sorted(
                    lambda line: (line.sequence, line.id)
                )
            ]
    
    def _check_required_next_drug_pickup_date(self):
        for prescription in self:
            if not prescription.next_drug_pickup_date:
                raise ValidationError(_("Next Drug Pickup Date is required."))
    
    def _patient_snapshot_values(self, patient_id, existing_vals=None):
        patient = self.env["res.partner"].browse(patient_id).exists()
        if not patient:
            return {}

        snapshot = patient._cdu_prescription_snapshot_values()
        existing_vals = existing_vals or {}
        return {
            field_name: value
            for field_name, value in snapshot.items()
            if field_name not in existing_vals
        }

    @api.onchange("facility_id")
    def _onchange_facility_id(self):
        if self.facility_id:
            self.facility_name = self.facility_id.name
            self.facility_code = self.facility_id.code

    @api.onchange("patient_id")
    def _onchange_patient_id(self):
        if self.patient_id:
            self.update(self._patient_snapshot_values(self.patient_id.id))

    @api.onchange("next_drug_pickup_date", "next_clinical_visit_date")
    def _onchange_repeat_days_dates(self):
        return

    @api.onchange("repeat_days")
    def _onchange_repeat_days(self):
        for prescription in self:
            if prescription.repeat_days and prescription.repeat_days < 0:
                prescription.repeat_days = 0

    @api.constrains("repeat_days")
    def _check_repeat_days(self):
        for prescription in self:
            if prescription.repeat_days < 0:
                raise ValidationError(_("Repeats in Days cannot be negative."))

    @api.model
    def _calculate_repeat_days_from_values(self, values):
        next_pickup = fields.Date.to_date(values.get("next_drug_pickup_date"))
        next_clinical = fields.Date.to_date(values.get("next_clinical_visit_date"))
        if not next_pickup or not next_clinical:
            return 0
        return max(0, (next_clinical - next_pickup).days)

    def _sync_repeat_days_from_cdu_days(self):
        for prescription in self:
            if prescription.repeat_days <= 0 and prescription.cdu_days_supply > 0:
                prescription.repeat_days = prescription.cdu_days_supply

    def _ensure_cdu_groups(self, *xml_ids):
        if self.env.user.has_group("cdu_prescription.group_cdu_admin"):
            return
        if any(self.env.user.has_group(xml_id) for xml_id in xml_ids):
            return
        raise AccessError(_("You do not have permission to perform this CDU action."))

    def _ensure_states(self, allowed_states):
        invalid = self.filtered(lambda prescription: prescription.state not in allowed_states)
        if invalid:
            raise ValidationError(_("This action is not allowed for the current prescription status."))

    def _action_open_prescription_queue(self, action_xml_id):
        action = self.env["ir.actions.actions"]._for_xml_id(action_xml_id)
        # Queue transitions are the end of the current work item. Opening the
        # queue as the main action replaces the breadcrumb stack instead of
        # adding another queue/prescription pair after every processed record.
        action["target"] = "main"
        return action

    def action_mark_patient_verified(self):
        self._ensure_cdu_groups("cdu_prescription.group_cdu_data_clerk")
        self._ensure_states(("awaiting_verification",))
        self.write({
            "state": "awaiting_validation",
            "verified_by": self.env.user.id,
            "verified_at": fields.Datetime.now(),
        })
        return self._action_open_prescription_queue(
            "cdu_prescription.action_cdu_prescription_awaiting_verification"
        )

    def action_mark_medicine_validated(self):
        self._ensure_cdu_groups("cdu_prescription.group_cdu_dispensing_officer")
        self._ensure_states(("awaiting_validation",))
        errors_by_prescription = []
        for prescription in self:
            missing = prescription._get_validation_errors()
            if missing:
                errors_by_prescription.append(
                    "%s: %s" % (prescription.display_name, ", ".join(missing))
                )
        if errors_by_prescription:
            raise ValidationError(
                _("Validation cannot continue. Please complete:\n%s")
                % "\n".join(errors_by_prescription)
            )
        self.write({
            "state": "awaiting_batching",
            "validated_by": self.env.user.id,
            "validated_at": fields.Datetime.now(),
        })
        return self._action_open_prescription_queue(
            "cdu_prescription.action_cdu_prescription_awaiting_validation"
        )

    def action_reject_to_call_center(self):
        return self._action_open_rejection_wizard("call_center")

    def action_reject_to_facility(self):
        return self._action_open_rejection_wizard("facility")

    def _action_open_rejection_wizard(self, destination):
        self.ensure_one()
        self._ensure_cdu_groups(
            "cdu_prescription.group_cdu_data_clerk",
            "cdu_prescription.group_cdu_dispensing_officer",
        )
        self._ensure_states(
            ("awaiting_verification", "awaiting_validation", "awaiting_dispensing")
        )
        wizard_view = self.env.ref(
            "cdu_prescription.view_cdu_prescription_reject_wizard_form"
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Reject Prescription"),
            "res_model": "cdu.prescription.reject.wizard",
            "view_mode": "form",
            "views": [(wizard_view.id, "form")],
            "target": "new",
            "context": {
                "default_prescription_id": self.id,
                "default_stage": self.state,
                "default_destination": destination,
            },
        }

    def _perform_rejection(self, destination, reason_codes):
        self.ensure_one()
        self._ensure_cdu_groups(
            "cdu_prescription.group_cdu_data_clerk",
            "cdu_prescription.group_cdu_dispensing_officer",
        )
        self._ensure_states(
            ("awaiting_verification", "awaiting_validation", "awaiting_dispensing")
        )
        if destination not in ("call_center", "facility"):
            raise ValidationError(_("Select a valid rejection destination."))
        if isinstance(reason_codes, str):
            reason_codes = [reason_codes]
        reason_codes = list(dict.fromkeys(reason_codes or []))
        if not reason_codes:
            raise ValidationError(_("Select at least one rejection reason."))
        reasons = self.env["cdu.rejection.reason"].search(
            [("code", "in", reason_codes), ("active", "=", True)]
        )
        if len(reasons) != len(reason_codes):
            raise ValidationError(_("One or more rejection reasons are invalid."))
        if self.state != "awaiting_dispensing" and any(reasons.mapped("dispensing_only")):
            raise ValidationError(_("Out of Stock is only available during Dispensing."))
        source_state = self.state
        rejected_state = (
            "rejected_to_call_center"
            if destination == "call_center"
            else "rejected_to_facility"
        )
        rejection = self.env["cdu.prescription.rejection"].create(
            {
                "prescription_id": self.id,
                "stage": source_state,
                "destination": destination,
                "reason_ids": [(6, 0, reasons.ids)],
                "rejected_by": self.env.user.id,
                "rejected_at": fields.Datetime.now(),
            }
        )
        self.write(
            {
                "state": rejected_state,
                "rejected_from_state": source_state,
                "active_rejection_id": rejection.id,
            }
        )
        action_xml_id = (
            "cdu_prescription.action_cdu_prescription_awaiting_verification"
            if source_state == "awaiting_verification"
            else (
                "cdu_prescription.action_cdu_prescription_awaiting_validation"
                if source_state == "awaiting_validation"
                else "cdu_elmis.action_cdu_dispensing_work_queue"
            )
        )
        return self._action_open_prescription_queue(action_xml_id)

    def action_return_to_previous_stage(self):
        self._ensure_cdu_groups(
            "cdu_prescription.group_cdu_data_clerk",
            "cdu_prescription.group_cdu_call_agent",
            "cdu_prescription.group_cdu_dispensing_officer",
        )
        self._ensure_states(("rejected_to_call_center", "rejected_to_facility"))

        destination_states = set()
        for prescription in self:
            destination_state = prescription.rejected_from_state
            if not destination_state:
                # Compatibility for prescriptions rejected before origin tracking existed.
                destination_state = (
                    "awaiting_validation"
                    if prescription.verified_at
                    else "awaiting_verification"
                )
            active_rejection = prescription.active_rejection_id
            if active_rejection:
                active_rejection.write(
                    {
                        "is_active": False,
                        "returned_by": self.env.user.id,
                        "returned_at": fields.Datetime.now(),
                    }
                )
            prescription.write(
                {
                    "state": destination_state,
                    "active_rejection_id": False,
                }
            )
            destination_states.add(destination_state)

        if destination_states == {"awaiting_validation"}:
            action_xml_id = "cdu_prescription.action_cdu_prescription_awaiting_validation"
        elif destination_states == {"awaiting_dispensing"} and (
            self.env.user.has_group("cdu_prescription.group_cdu_dispensing_officer")
            or self.env.user.has_group("cdu_prescription.group_cdu_admin")
        ):
            action_xml_id = "cdu_elmis.action_cdu_dispensing_work_queue"
        elif destination_states == {"awaiting_dispensing"}:
            action_xml_id = "cdu_prescription.action_cdu_dashboard"
        else:
            action_xml_id = "cdu_prescription.action_cdu_prescription_awaiting_verification"
        return self._action_open_prescription_queue(action_xml_id)

    def action_cancel(self):
        self._ensure_cdu_groups("cdu_prescription.group_cdu_admin")
        self.write({"state": "cancelled"})

    def _get_validation_errors(self):
        self.ensure_one()
        missing = []
        if not self.patient_id and not self.patient_identifier:
            missing.append("patient")
        if not self.drug_pickup_point_raw and not self.collection_point_id:
            missing.append("drug pickup point")
        if not self.regimen_prescribed_raw:
            missing.append("regimen prescribed")
        if self.regimen_prescribed_raw and not self.regimen_id:
            missing.append("an exact configured regimen match")
        if self.regimen_id and not self.regimen_option_id:
            missing.append("a dispensing option")
        if not self.medicine_line_ids:
            missing.append("at least one prescription medicine")
        incomplete_medicines = self.medicine_line_ids.filtered(
            lambda line: not (line.dosage_instructions or "").strip()
        )
        if incomplete_medicines:
            missing.append("dosage instructions for every medicine")
        if self.medicine_amended and not (self.medicine_amendment_note or "").strip():
            missing.append("a reason for the medicine amendment")
        if not self.next_drug_pickup_date:
            missing.append("next drug pickup date")
        return missing

    @api.depends(
        "prescription_date",
        "next_drug_pickup_date",
        "next_clinical_visit_date",
    )
    def _compute_prescription_durations(self):
        for prescription in self:
            # 1. Calculate Facility Days: Next Drug Pickup Date - Prescription Date
            if prescription.prescription_date and prescription.next_drug_pickup_date:
                facility_delta = prescription.next_drug_pickup_date - prescription.prescription_date
                prescription.facility_days_supply = max(0, facility_delta.days)
            else:
                prescription.facility_days_supply = 0

            # 2. Calculate CDU Days: Next Clinical Appointment Date - Next Drug Pickup Date
            if prescription.next_drug_pickup_date and prescription.next_clinical_visit_date:
                cdu_delta = prescription.next_clinical_visit_date - prescription.next_drug_pickup_date
                prescription.cdu_days_supply = max(0, cdu_delta.days)
            else:
                prescription.cdu_days_supply = 0

            # 3. Total Days
            prescription.total_days_supply = (
                prescription.facility_days_supply + prescription.cdu_days_supply
            )
