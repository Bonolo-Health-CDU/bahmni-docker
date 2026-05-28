import re
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError


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
    e_locker_district = fields.Char(string="E-locker District")
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
    verified_by = fields.Many2one("res.users", readonly=True)
    verified_at = fields.Datetime(readonly=True)
    validated_by = fields.Many2one("res.users", readonly=True)
    validated_at = fields.Datetime(readonly=True)
    validation_notes = fields.Text(string="Review Notes", tracking=True)

    _sql_constraints = [
        ("unique_source_key", "unique(source_key)", "This eRegister prescription has already been ingested."),
    ]

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
        match = re.match(r"([a-z0-9]+)", prefix)
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

        snapshot = {
            "patient_identifier": patient.cdu_eregister_id or patient.ref,
            "hiv_program_id": patient.cdu_hiv_program_id,
            "national_id": patient.cdu_national_id,
            "patient_first_name": patient.name,
            "patient_date_of_birth": patient.cdu_date_of_birth,
            "patient_gender": patient.cdu_gender,
            "patient_phone": patient.phone or patient.mobile,
            "secondary_contact": patient.cdu_secondary_contact,
            "patient_address": patient.street,
        }
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

    def action_mark_patient_verified(self):
        self._ensure_cdu_groups("cdu_prescription.group_cdu_data_clerk")
        self._ensure_states(("awaiting_verification",))
        self.write({
            "state": "awaiting_validation",
            "verified_by": self.env.user.id,
            "verified_at": fields.Datetime.now(),
        })

    def action_mark_medicine_validated(self):
        self._ensure_cdu_groups("cdu_prescription.group_cdu_dispensing_officer")
        self._ensure_states(("awaiting_validation",))
        self.write({
            "state": "awaiting_batching",
            "validated_by": self.env.user.id,
            "validated_at": fields.Datetime.now(),
        })

    def action_reject_to_call_center(self):
        self._ensure_cdu_groups(
            "cdu_prescription.group_cdu_data_clerk",
            "cdu_prescription.group_cdu_dispensing_officer",
        )
        self._ensure_states(("awaiting_verification", "awaiting_validation"))
        self.write({"state": "rejected_to_call_center"})

    def action_reject_to_facility(self):
        self._ensure_cdu_groups(
            "cdu_prescription.group_cdu_data_clerk",
            "cdu_prescription.group_cdu_dispensing_officer",
        )
        self._ensure_states(("awaiting_verification", "awaiting_validation"))
        self.write({"state": "rejected_to_facility"})

    def action_return_to_verification(self):
        self._ensure_cdu_groups("cdu_prescription.group_cdu_call_agent")
        self._ensure_states(("rejected_to_call_center",))
        self.write({"state": "awaiting_verification"})

    def action_return_to_validation(self):
        self._ensure_cdu_groups("cdu_prescription.group_cdu_call_agent")
        self._ensure_states(("rejected_to_call_center",))
        self.write({"state": "awaiting_validation"})

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
        if not self.next_drug_pickup_date:
            missing.append("next drug pickup date")
        return missing

    # @api.depends(
    # "next_drug_pickup_date",
    # "next_clinical_visit_date",
    # )
    # def _compute_cdu_days_supply(self):

    #     for prescription in self:

    #         if (
    #             prescription.next_drug_pickup_date
    #             and prescription.next_clinical_visit_date
    #         ):

    #             delta = (
    #                 prescription.next_clinical_visit_date
    #                 - prescription.next_drug_pickup_date
    #             )

    #             prescription.cdu_days_supply = delta.days

    #         else:
    #             prescription.cdu_days_supply = 0
    # Locate your fields declaration in cdu_prescription.py and modify/add:

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

# Replace the old @api.depends( ... ) def _compute_cdu_days_supply method with:

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
                cdu_delta = prescription.next_clinical_visit_date - prescription.prescription_date
                prescription.cdu_days_supply = max(0, cdu_delta.days)
            else:
                prescription.cdu_days_supply = 0