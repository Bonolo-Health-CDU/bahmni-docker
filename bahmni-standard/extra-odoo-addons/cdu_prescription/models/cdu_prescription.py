import re
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
    batch_sequence = fields.Integer(
        string="Planned Position",
        copy=False,
        index=True,
        help="The prescription's fixed position when its workload batch is confirmed.",
    )
    dispensing_sequence = fields.Integer(
        string="Dispensing Completion Position",
        readonly=True,
        copy=False,
        index=True,
    )
    qa_sequence = fields.Integer(
        string="QA Completion Position",
        readonly=True,
        copy=False,
        index=True,
    )
    dispensing_deferred = fields.Boolean(
        string="Dispensing Skipped for Now",
        readonly=True,
        copy=False,
        index=True,
    )
    qa_deferred = fields.Boolean(
        string="QA Skipped for Now",
        readonly=True,
        copy=False,
        index=True,
    )
    boxing_deferred = fields.Boolean(
        string="Boxing Skipped for Now",
        readonly=True,
        copy=False,
        index=True,
    )
    production_position_display = fields.Char(
        string="Current Flow Position",
        compute="_compute_production_position_display",
    )
    production_skip_history_ids = fields.One2many(
        "cdu.production.skip",
        "prescription_id",
        string="Production Skip History",
        readonly=True,
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
        string="Mapped Regimen",
        tracking=True,
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

    def init(self):
        """Backfill deterministic positions for production work already in progress."""
        self.env.cr.execute(
            """
            WITH ranked AS (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY batch_id
                           ORDER BY prescription_date, id
                       ) AS position
                  FROM cdu_prescription
                 WHERE batch_id IS NOT NULL
            )
            UPDATE cdu_prescription AS prescription
               SET batch_sequence = ranked.position
              FROM ranked
             WHERE prescription.id = ranked.id
               AND COALESCE(prescription.batch_sequence, 0) = 0
            """
        )
        self.env.cr.execute(
            """
            UPDATE cdu_prescription
               SET dispensing_sequence = batch_sequence
             WHERE COALESCE(dispensing_sequence, 0) = 0
               AND state IN (
                   'awaiting_bagging_qa',
                   'awaiting_boxing',
                   'awaiting_dispatch',
                   'dispatched'
               )
            """
        )
        self.env.cr.execute(
            """
            UPDATE cdu_prescription
               SET qa_sequence = dispensing_sequence
             WHERE COALESCE(qa_sequence, 0) = 0
               AND state IN ('awaiting_boxing', 'awaiting_dispatch', 'dispatched')
            """
        )

    @api.depends("batch_sequence", "dispensing_sequence", "qa_sequence")
    def _compute_production_position_display(self):
        for prescription in self:
            planned = prescription.batch_sequence
            if prescription.qa_sequence:
                current_stage = _("QA")
                current_position = prescription.qa_sequence
            elif prescription.dispensing_sequence:
                current_stage = _("Dispensing")
                current_position = prescription.dispensing_sequence
            elif planned:
                current_stage = _("Planned")
                current_position = planned
            else:
                prescription.production_position_display = False
                continue

            display = _("%(stage)s #%(position)s") % {
                "stage": current_stage,
                "position": current_position,
            }
            if planned and planned != current_position:
                display += _(" (planned #%(planned)s)") % {"planned": planned}
            prescription.production_position_display = display

    def _assign_production_completion_sequence(self, stage):
        self.ensure_one()
        field_by_stage = {
            "dispensing": "dispensing_sequence",
            "bagging_qa": "qa_sequence",
        }
        field_name = field_by_stage.get(stage)
        if not field_name:
            raise ValidationError(_("Unknown production stage: %s") % stage)
        if getattr(self, field_name):
            self._resolve_production_skip(stage)
            return getattr(self, field_name)
        if not self.batch_id:
            self.write({field_name: 1})
            self._resolve_production_skip(stage)
            return 1

        self.env.cr.execute(
            "SELECT id FROM cdu_batch WHERE id = %s FOR UPDATE",
            (self.batch_id.id,),
        )
        self.env["cdu.prescription"].flush_model(["batch_id", field_name])
        self.env.cr.execute(
            """
            SELECT COALESCE(MAX(%s), 0) + 1
              FROM cdu_prescription
             WHERE batch_id = %%s
            """
            % field_name,
            (self.batch_id.id,),
        )
        sequence = self.env.cr.fetchone()[0]
        self.write({field_name: sequence})
        self._resolve_production_skip(stage)
        return sequence

    def _defer_production_stage(self, stage, reason):
        self.ensure_one()
        field_by_stage = {
            "dispensing": "dispensing_deferred",
            "bagging_qa": "qa_deferred",
            "boxing": "boxing_deferred",
        }
        state_by_stage = {
            "dispensing": "awaiting_dispensing",
            "bagging_qa": "awaiting_bagging_qa",
            "boxing": "awaiting_boxing",
        }
        deferred_field = field_by_stage.get(stage)
        if not deferred_field or self.state != state_by_stage.get(stage):
            raise ValidationError(
                _("This prescription is no longer available to skip at this stage.")
            )
        self.write({deferred_field: True})
        self.env["cdu.production.skip"].create(
            {
                "prescription_id": self.id,
                "stage": stage,
                "reason": reason,
            }
        )

    def _resolve_production_skip(self, stage):
        self.ensure_one()
        field_by_stage = {
            "dispensing": "dispensing_deferred",
            "bagging_qa": "qa_deferred",
            "boxing": "boxing_deferred",
        }
        deferred_field = field_by_stage.get(stage)
        if deferred_field and getattr(self, deferred_field):
            self.write({deferred_field: False})
        active_skips = self.env["cdu.production.skip"].search(
            [
                ("prescription_id", "=", self.id),
                ("stage", "=", stage),
                ("active", "=", True),
            ]
        )
        if active_skips:
            active_skips.write(
                {
                    "active": False,
                    "resolved_by": self.env.user.id,
                    "resolved_at": fields.Datetime.now(),
                }
            )

    def action_open_production_skip_wizard(self, stage):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Skip for Now"),
            "res_model": "cdu.production.skip.wizard",
            "view_mode": "form",
            "view_id": self.env.ref(
                "cdu_prescription.view_cdu_production_skip_wizard_form"
            ).id,
            "target": "new",
            "context": {
                "default_prescription_id": self.id,
                "default_stage": stage,
            },
        }

    def _get_action_after_production_skip(self, stage):
        self.ensure_one()
        if stage == "boxing":
            return self.env.ref("cdu_elmis.action_cdu_boxing_work_queue").read()[0]

        configuration = {
            "dispensing": (
                "awaiting_dispensing",
                "dispensing_deferred asc, batch_sequence asc, id asc",
                "action_open_dispensing",
                "cdu_elmis.action_cdu_dispensing_work_queue",
            ),
            "bagging_qa": (
                "awaiting_bagging_qa",
                "qa_deferred asc, dispensing_sequence asc, batch_sequence asc, id asc",
                "action_open_bagging_qa",
                "cdu_elmis.action_cdu_bagging_qa_work_queue",
            ),
        }
        state, order, method_name, action_xmlid = configuration[stage]
        domain = [
            ("state", "=", state),
            ("id", "!=", self.id),
            ("batch_id", "=", self.batch_id.id or False),
        ]
        next_prescription = self.search(domain, order=order, limit=1)
        if next_prescription:
            return getattr(next_prescription, method_name)()
        return self.env.ref(action_xmlid).read()[0]


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

        if vals.get("regimen_prescribed_raw"):
            regimen = self._find_matching_regimen(vals.get("regimen_prescribed_raw"))
            if regimen:
                vals["regimen_id"] = regimen.id

        prescription = super().create(vals)
        prescription._sync_repeat_days_from_cdu_days()
        prescription._check_required_next_drug_pickup_date()

        return prescription

    def write(self, vals):
        if vals.get("facility_id"):
            facility = self.env["cdu.facility"].browse(vals["facility_id"])
            vals.setdefault("facility_name", facility.name)
            vals.setdefault("facility_code", facility.code)
        
        if vals.get("patient_id"):
            vals.update(self._patient_snapshot_values(vals["patient_id"], vals))

        if "regimen_prescribed_raw" in vals:
            regimen = self._find_matching_regimen(vals.get("regimen_prescribed_raw"))
            vals["regimen_id"] = (regimen.id if regimen else False )
        result = super().write(vals)

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
    
    def _check_required_next_drug_pickup_date(self):
        for prescription in self:
            if not prescription.next_drug_pickup_date:
                raise ValidationError(_("Next Drug Pickup Date is required."))
    
    def _find_matching_regimen(self, raw_value):
           
        code = self._extract_regimen_code(raw_value)

        if not code:
            return False

        return self.env["cdu.regimen"].search(
            [("code", "=ilike", code)],
            limit=1,
        )

    def _extract_regimen_code(self, raw_value):
        if not raw_value:
            return False
        prefix = str(raw_value).split('=')[0].strip().lower()
        # Match everything before the first space or equal sign, allowing slashes and hyphens
        match = re.match(r"([^=\s]+)", prefix)
        return match.group(1) if match else False
    
    def action_remap_regimens(self):
        mapping_data = {}
        for record in self:
            # code = self._extract_code(record.regimen_prescribed_raw)
            code = self._extract_regimen_code(record.regimen_prescribed_raw)
            if code:
                mapping_data[record.id] = code.lower()

        if not mapping_data:
            self.write({'regimen_id': False})
            return

        unique_codes = list(set(mapping_data.values()))
        regimens = self.env["cdu.regimen"].search([
            ("code", "in", unique_codes)
        ])
        
        regimen_lookup = {r.code.lower(): r.id for r in regimens}

        for record in self:
            code = mapping_data.get(record.id)
            regimen_id = regimen_lookup.get(code) if code else False
            record.regimen_id = regimen_id
          
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
        return self.env["ir.actions.actions"]._for_xml_id(action_xml_id)

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
        if not (self.dosage_instructions or "").strip():
            missing.append("dosage instructions")
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
