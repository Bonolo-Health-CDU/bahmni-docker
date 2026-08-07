from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ResPartner(models.Model):
    _inherit = "res.partner"

    cdu_birthdate = fields.Date(string="Date of Birth")
    cdu_age = fields.Integer(string="Age", compute="_compute_cdu_age")
    cdu_gender = fields.Selection(
        [
            ("female", "Female"),
            ("male", "Male"),
            ("other", "Other"),
            ("unknown", "Unknown"),
        ],
        string="Gender",
    )
    cdu_national_id = fields.Char(string="National ID")
    cdu_facility = fields.Char(string="Facility")
    cdu_patient_status = fields.Selection(
        [
            ("active", "Active"),
            ("transferred", "Transferred"),
            ("deceased", "Deceased"),
            ("lost_to_follow_up", "Lost to Follow-up"),
        ],
        string="Patient Status",
        default="active",
    )
    cdu_program = fields.Selection(
        [
            ("art", "ART"),
            ("tb", "TB"),
            ("mch", "MCH"),
            ("general", "General"),
            ("other", "Other"),
        ],
        string="Program",
    )
    cdu_diagnosis = fields.Text(string="Diagnosis")
    cdu_allergies = fields.Text(string="Allergies")
    cdu_medication_history = fields.Text(string="Medication History")
    cdu_current_treatment = fields.Text(string="Current Treatment")
    cdu_last_dispensing_date = fields.Date(string="Last Dispensing Date")
    cdu_next_refill_date = fields.Date(string="Next Refill Date")
    cdu_preferred_pickup_facility = fields.Char(string="Preferred Pickup Facility")
    cdu_dispensing_notes = fields.Text(string="Dispensing Notes")
    cdu_next_of_kin_name = fields.Char(string="Next of Kin Name")
    cdu_next_of_kin_relationship = fields.Char(string="Relationship")
    cdu_next_of_kin_phone = fields.Char(string="Next of Kin Phone")
    cdu_next_of_kin_address = fields.Text(string="Next of Kin Address")
    cdu_emergency_contact_is_next_of_kin = fields.Boolean(
        string="Use as Emergency Contact"
    )
    cdu_sms_consent = fields.Boolean(string="Consent to SMS Reminders")
    cdu_preferred_contact_method = fields.Selection(
        [
            ("phone", "Phone"),
            ("sms", "SMS"),
            ("whatsapp", "WhatsApp"),
            ("next_of_kin", "Next of Kin"),
        ],
        string="Preferred Contact Method",
    )

    @api.depends("cdu_birthdate")
    def _compute_cdu_age(self):
        today = fields.Date.context_today(self)
        for partner in self:
            if not partner.cdu_birthdate:
                partner.cdu_age = 0
                continue

            born = partner.cdu_birthdate
            partner.cdu_age = today.year - born.year - (
                (today.month, today.day) < (born.month, born.day)
            )

    @api.constrains("cdu_birthdate")
    def _check_cdu_birthdate(self):
        today = fields.Date.context_today(self)
        for partner in self:
            if partner.cdu_birthdate and partner.cdu_birthdate > today:
                raise ValidationError("Date of birth cannot be in the future.")

    @api.constrains("cdu_last_dispensing_date", "cdu_next_refill_date")
    def _check_cdu_refill_dates(self):
        for partner in self:
            if (
                partner.cdu_last_dispensing_date
                and partner.cdu_next_refill_date
                and partner.cdu_next_refill_date < partner.cdu_last_dispensing_date
            ):
                raise ValidationError(
                    "Next refill date cannot be before last dispensing date."
                )

    @api.constrains("cdu_national_id")
    def _check_cdu_national_id_unique(self):
        for partner in self:
            if not partner.cdu_national_id:
                continue

            duplicate = self.search_count(
                [
                    ("id", "!=", partner.id),
                    ("is_company", "=", False),
                    ("cdu_national_id", "=", partner.cdu_national_id),
                ]
            )
            if duplicate:
                raise ValidationError("National ID must be unique for each patient.")
