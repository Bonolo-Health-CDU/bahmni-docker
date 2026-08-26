from odoo import _, fields, models
from odoo.exceptions import AccessError


GENDER_SELECTION = [("male", "Male"), ("female", "Female"), ("other", "Other")]
PUP_TYPE_SELECTION = [
    ("e_locker", "E-locker"),
    ("retail_pharmacy", "Partner Retail Pharmacy"),
]


class CduReportReadonly(models.AbstractModel):
    _name = "cdu.report.readonly"
    _description = "CDU Read-only Report"

    def _readonly_error(self):
        raise AccessError(_("Reporting records are read-only."))

    def create(self, vals):
        self._readonly_error()

    def write(self, vals):
        self._readonly_error()

    def unlink(self):
        self._readonly_error()


class CduReportCommon(models.AbstractModel):
    _name = "cdu.report.common"
    _inherit = "cdu.report.readonly"
    _description = "CDU Common Reporting Dimensions"

    _auto = False
    _log_access = False

    prescription_id = fields.Many2one("cdu.prescription", readonly=True)
    prescription_number = fields.Char(readonly=True)
    patient_id = fields.Many2one("res.partner", readonly=True)
    patient_name = fields.Char(readonly=True)
    facility_id = fields.Many2one("cdu.facility", readonly=True)
    facility_name = fields.Char(readonly=True)
    district_name = fields.Char(string="District", readonly=True)
    programme = fields.Char(readonly=True)
    gender = fields.Selection(GENDER_SELECTION, readonly=True)
    age = fields.Integer(readonly=True)
    age_group = fields.Char(readonly=True)
    collection_point_id = fields.Many2one(
        "cdu.collection.point", string="PUP", readonly=True
    )
    collection_point_name = fields.Char(string="PUP Name", readonly=True)
    pup_type = fields.Selection(PUP_TYPE_SELECTION, string="PUP Type", readonly=True)
    pup_district = fields.Char(string="PUP District", readonly=True)
    prescription_date = fields.Date(readonly=True)
    next_collection_date = fields.Date(readonly=True)
    record_count = fields.Integer(string="Count", readonly=True, group_operator="sum")


class CduReportEnrolment(models.Model):
    _name = "cdu.report.enrolment"
    _inherit = "cdu.report.common"
    _description = "CDU Patient Enrolment Report"
    _rec_name = "patient_name"
    _auto = False
    _log_access = False

    enrolment_date = fields.Date(readonly=True)
    new_or_repeat = fields.Selection(
        [("new", "New"), ("repeat", "Repeat"), ("restarted", "Restarted")],
        readonly=True,
    )
    new_count = fields.Integer(readonly=True, group_operator="sum")
    repeat_count = fields.Integer(readonly=True, group_operator="sum")

    def init(self):
        self._create_reporting_views()

    def _create_reporting_views(self):
        cr = self.env.cr
        report_views = [
            "cdu_report_enrolment",
            "cdu_report_verification",
            "cdu_report_validation",
            "cdu_report_dispensing",
            "cdu_report_parcel_production",
            "cdu_report_parcel_forecast",
            "cdu_reporting_prescription_fact",
        ]
        for view_name in report_views:
            cr.execute('DROP VIEW IF EXISTS "%s" CASCADE' % view_name)

        cr.execute(
            """
            CREATE VIEW cdu_reporting_prescription_fact AS
            WITH history AS (
                SELECT prescription_id,
                       MIN(changed_at) FILTER (WHERE new_status = 'awaiting_verification') AS received_at,
                       MIN(changed_at) FILTER (WHERE new_status = 'awaiting_dispensing') AS production_received_at
                  FROM cdu_prescription_status_history
                 GROUP BY prescription_id
            ),
            rejection AS (
                SELECT DISTINCT ON (prescription_id)
                       prescription_id, stage, reason_display, rejected_by,
                       rejected_at, returned_at, is_active
                  FROM cdu_prescription_rejection
                 ORDER BY prescription_id, rejected_at DESC, id DESC
            ),
            expected_items AS (
                SELECT prescription_id,
                       COUNT(DISTINCT COALESCE(NULLIF(openmrs_drug_uuid, ''), NULLIF(openmrs_drug_name, ''))) AS expected_item_count
                  FROM cdu_picking_line
                 GROUP BY prescription_id
            ),
            dispense AS (
                SELECT d.prescription_id,
                       MIN(d.id) AS dispense_id,
                       MIN(d.create_date) AS production_received_at,
                       MAX(d.confirmed_at) AS dispensing_date,
                       (ARRAY_AGG(d.confirmed_by ORDER BY d.confirmed_at DESC NULLS LAST))[1] AS dispenser_id,
                       BOOL_OR(d.state = 'confirmed') AS is_dispensed,
                       COUNT(DISTINCT s.id) FILTER (WHERE s.quantity_dispensed > 0) AS item_count,
                       COALESCE(SUM(s.dispensed_units), 0.0) AS total_quantity_dispensed,
                       STRING_AGG(DISTINCT COALESCE(s.selected_orderable_name, s.openmrs_drug_name), ', ')
                           FILTER (WHERE COALESCE(s.selected_orderable_name, s.openmrs_drug_name) IS NOT NULL) AS medicine_names,
                       BOOL_AND(COALESCE(s.quantity_dispensed, 0) > 0)
                           FILTER (WHERE s.id IS NOT NULL) AS all_lines_fulfilled
                  FROM cdu_dispense d
             LEFT JOIN cdu_dispense_stock_selection s ON s.dispense_id = d.id
                 GROUP BY d.prescription_id
            ),
            parcel AS (
                SELECT DISTINCT ON (prescription_id)
                       prescription_id, id AS parcel_id, parcel_reference,
                       confirmed_at AS production_date, bagged_by AS packager_id,
                       state AS parcel_state
                  FROM cdu_bagging_qa
                 ORDER BY prescription_id, confirmed_at DESC NULLS LAST, id DESC
            ),
            box AS (
                SELECT bl.prescription_id,
                       MAX(b.id) AS box_id,
                       MAX(b.dispatched_at) AS dispatch_date,
                       (ARRAY_AGG(b.dispatched_by ORDER BY b.dispatched_at DESC NULLS LAST))[1] AS dispatched_by,
                       BOOL_OR(b.dispatch_status = 'dispatched') AS is_dispatched,
                       MAX(b.collect_go_status) AS collect_go_status,
                       MAX(bl.collect_go_parcel_status) AS collection_status,
                       MAX(bl.collect_go_status_received_at)
                           FILTER (WHERE LOWER(COALESCE(bl.collect_go_parcel_status, '')) LIKE '%collect%') AS collection_date,
                       MAX(bl.collect_go_status_received_at)
                           FILTER (WHERE LOWER(COALESCE(bl.collect_go_parcel_status, '')) LIKE '%return%') AS return_date,
                       BOOL_OR(LOWER(COALESCE(bl.collect_go_parcel_status, '')) LIKE '%collect%') AS is_collected,
                       BOOL_OR(LOWER(COALESCE(bl.collect_go_parcel_status, '')) LIKE '%return%') AS is_returned
                  FROM cdu_box_line bl
                  JOIN cdu_box b ON b.id = bl.box_id
                 GROUP BY bl.prescription_id
            )
            SELECT p.id,
                   p.id AS prescription_id,
                   p.name AS prescription_number,
                   p.patient_id,
                   p.patient_first_name AS patient_name,
                   p.facility_id,
                   COALESCE(f.name, p.facility_name) AS facility_name,
                   COALESCE(NULLIF(cp.region_name, ''), NULLIF(p.e_locker_district, '')) AS district_name,
                   NULLIF(p.hiv_program_id, '') AS programme,
                   p.patient_gender AS gender,
                   CASE WHEN p.patient_date_of_birth IS NULL THEN NULL
                        ELSE DATE_PART('year', AGE(p.prescription_date, p.patient_date_of_birth))::integer END AS age,
                   CASE
                       WHEN p.patient_date_of_birth IS NULL THEN 'Unknown'
                       WHEN DATE_PART('year', AGE(p.prescription_date, p.patient_date_of_birth)) < 5 THEN '0-4'
                       WHEN DATE_PART('year', AGE(p.prescription_date, p.patient_date_of_birth)) < 15 THEN '5-14'
                       WHEN DATE_PART('year', AGE(p.prescription_date, p.patient_date_of_birth)) < 25 THEN '15-24'
                       WHEN DATE_PART('year', AGE(p.prescription_date, p.patient_date_of_birth)) < 35 THEN '25-34'
                       WHEN DATE_PART('year', AGE(p.prescription_date, p.patient_date_of_birth)) < 45 THEN '35-44'
                       WHEN DATE_PART('year', AGE(p.prescription_date, p.patient_date_of_birth)) < 55 THEN '45-54'
                       WHEN DATE_PART('year', AGE(p.prescription_date, p.patient_date_of_birth)) < 65 THEN '55-64'
                       ELSE '65+'
                   END AS age_group,
                   p.collection_point_id,
                   cp.name AS collection_point_name,
                   cp.point_type AS pup_type,
                   cp.region_name AS pup_district,
                   p.prescription_date,
                   p.next_drug_pickup_date AS next_collection_date,
                   p.new_or_revisit,
                   p.state AS prescription_state,
                   COALESCE(h.received_at, p.create_date) AS receipt_date,
                   p.verified_at AS verification_date,
                   p.verified_by AS verifier_id,
                   p.validated_at AS validation_date,
                   p.validated_by AS validator_id,
                   r.stage AS rejection_stage,
                   r.reason_display AS rejection_reason,
                   r.rejected_by,
                   r.rejected_at,
                   r.returned_at AS rejection_returned_at,
                   r.is_active AS rejection_is_active,
                   COALESCE(h.production_received_at, d.production_received_at) AS production_received_at,
                   d.dispense_id,
                   d.dispensing_date,
                   d.dispenser_id,
                   COALESCE(d.item_count, 0) AS item_count,
                   COALESCE(d.total_quantity_dispensed, 0.0) AS total_quantity_dispensed,
                   d.medicine_names,
                   CASE
                       WHEN d.is_dispensed AND COALESCE(d.item_count, 0) >= GREATEST(COALESCE(e.expected_item_count, 0), 1)
                            AND COALESCE(d.all_lines_fulfilled, FALSE) THEN 'fully_fulfilled'
                       WHEN d.dispense_id IS NOT NULL THEN 'partially_fulfilled'
                       ELSE 'not_dispensed'
                   END AS fulfilment_status,
                   COALESCE(d.is_dispensed, FALSE) AS is_dispensed,
                   pr.parcel_id,
                   pr.parcel_reference,
                   pr.production_date,
                   pr.packager_id,
                   pr.parcel_state,
                   b.box_id,
                   b.dispatch_date,
                   b.dispatched_by,
                   COALESCE(b.is_dispatched, FALSE) AS is_dispatched,
                   b.collect_go_status,
                   b.collection_status,
                   b.collection_date,
                   b.return_date,
                   COALESCE(b.is_collected, FALSE) AS is_collected,
                   COALESCE(b.is_returned, FALSE) AS is_returned
              FROM cdu_prescription p
         LEFT JOIN cdu_facility f ON f.id = p.facility_id
         LEFT JOIN cdu_collection_point cp ON cp.id = p.collection_point_id
         LEFT JOIN history h ON h.prescription_id = p.id
         LEFT JOIN rejection r ON r.prescription_id = p.id
         LEFT JOIN expected_items e ON e.prescription_id = p.id
         LEFT JOIN dispense d ON d.prescription_id = p.id
         LEFT JOIN parcel pr ON pr.prescription_id = p.id
         LEFT JOIN box b ON b.prescription_id = p.id
            """
        )

        cr.execute(
            """
            CREATE VIEW cdu_report_enrolment AS
            SELECT id, prescription_id, prescription_number, patient_id, patient_name,
                   facility_id, facility_name, district_name, programme, gender, age, age_group,
                   collection_point_id, collection_point_name, pup_type, pup_district,
                   prescription_date, next_collection_date,
                   prescription_date AS enrolment_date,
                   CASE WHEN new_or_revisit = 'new' THEN 'new'
                        WHEN new_or_revisit = 'restarted' THEN 'restarted'
                        ELSE 'repeat' END AS new_or_repeat,
                   1 AS record_count,
                   CASE WHEN new_or_revisit = 'new' THEN 1 ELSE 0 END AS new_count,
                   CASE WHEN COALESCE(new_or_revisit, 'revisit') <> 'new' THEN 1 ELSE 0 END AS repeat_count
              FROM cdu_reporting_prescription_fact
            """
        )
        cr.execute(
            """
            CREATE VIEW cdu_report_verification AS
            SELECT id, prescription_id, prescription_number, patient_id, patient_name,
                   facility_id, facility_name, district_name, programme, gender, age, age_group,
                   collection_point_id, collection_point_name, pup_type, pup_district,
                   prescription_date, next_collection_date,
                   receipt_date, verification_date, verifier_id, rejection_reason,
                   CASE
                       WHEN rejection_stage = 'awaiting_verification' AND COALESCE(rejection_is_active, FALSE) THEN 'rejected'
                       WHEN verification_date IS NULL THEN 'awaiting_verification'
                       WHEN prescription_state = 'awaiting_validation' THEN 'submitted_for_validation'
                       ELSE 'verified'
                   END AS verification_status,
                   1 AS record_count
              FROM cdu_reporting_prescription_fact
            """
        )
        cr.execute(
            """
            CREATE VIEW cdu_report_validation AS
            SELECT id, prescription_id, prescription_number, patient_id, patient_name,
                   facility_id, facility_name, district_name, programme, gender, age, age_group,
                   collection_point_id, collection_point_name, pup_type, pup_district,
                   prescription_date, next_collection_date,
                   verification_date AS validation_received_date,
                   validation_date, validator_id, rejection_reason,
                   CASE
                       WHEN rejection_stage = 'awaiting_validation' AND COALESCE(rejection_is_active, FALSE) THEN 'rejected'
                       WHEN verification_date IS NOT NULL AND validation_date IS NULL THEN 'awaiting_validation'
                       WHEN prescription_state = 'awaiting_batching' THEN 'released_for_batching'
                       WHEN validation_date IS NOT NULL THEN 'validated'
                       ELSE 'not_received'
                   END AS validation_status,
                   1 AS record_count
              FROM cdu_reporting_prescription_fact
             WHERE verification_date IS NOT NULL OR rejection_stage = 'awaiting_validation'
            """
        )
        cr.execute(
            """
            CREATE VIEW cdu_report_dispensing AS
            SELECT id, prescription_id, prescription_number, patient_id, patient_name,
                   facility_id, facility_name, district_name, programme, gender, age, age_group,
                   collection_point_id, collection_point_name, pup_type, pup_district,
                   prescription_date, next_collection_date,
                   production_received_at, dispensing_date, dispenser_id,
                   item_count, total_quantity_dispensed, medicine_names, fulfilment_status,
                   dispatch_date, collection_date, return_date, collect_go_status, collection_status,
                   CASE
                       WHEN is_returned THEN 'returned'
                       WHEN is_collected THEN 'collected'
                       WHEN is_dispatched THEN 'dispatched'
                       WHEN rejection_stage = 'awaiting_dispensing' AND COALESCE(rejection_is_active, FALSE) THEN 'rejected'
                       WHEN is_dispensed THEN 'dispensed'
                       ELSE 'received_for_production'
                   END AS dispensing_status,
                   1 AS record_count
              FROM cdu_reporting_prescription_fact
             WHERE production_received_at IS NOT NULL OR dispense_id IS NOT NULL
                OR rejection_stage = 'awaiting_dispensing'
            """
        )
        cr.execute(
            """
            CREATE VIEW cdu_report_parcel_production AS
            SELECT parcel_id AS id, prescription_id, prescription_number, patient_id, patient_name,
                   facility_id, facility_name, district_name, programme, gender, age, age_group,
                   collection_point_id, collection_point_name, pup_type, pup_district,
                   prescription_date, next_collection_date,
                   parcel_reference, production_date, packager_id, dispatch_date,
                   CASE WHEN is_dispatched THEN 'dispatched' ELSE 'pending_dispatch' END AS dispatch_status,
                   collect_go_status, 1 AS record_count
              FROM cdu_reporting_prescription_fact
             WHERE parcel_id IS NOT NULL AND parcel_state = 'confirmed'
            """
        )
        cr.execute(
            """
            CREATE VIEW cdu_report_parcel_forecast AS
            SELECT id, prescription_id, prescription_number, patient_id, patient_name,
                   facility_id, facility_name, district_name, programme, gender, age, age_group,
                   collection_point_id, collection_point_name, pup_type, pup_district,
                   prescription_date, next_collection_date,
                   next_collection_date AS forecast_date,
                   1 AS patients_due, 1 AS forecasted_parcels,
                   CASE WHEN parcel_id IS NOT NULL AND parcel_state = 'confirmed' THEN 1 ELSE 0 END AS actual_parcels,
                   CASE WHEN parcel_id IS NOT NULL AND parcel_state = 'confirmed' THEN 0 ELSE -1 END AS variance,
                   CASE WHEN parcel_id IS NOT NULL AND parcel_state = 'confirmed' THEN 0.0 ELSE -100.0 END AS variance_percentage,
                   1 AS record_count
              FROM cdu_reporting_prescription_fact
             WHERE next_collection_date IS NOT NULL
            """
        )

        indexes = [
            ("cdu_reporting_prescription_date_idx", "cdu_prescription", "prescription_date"),
            ("cdu_reporting_prescription_state_idx", "cdu_prescription", "state"),
            ("cdu_reporting_prescription_facility_idx", "cdu_prescription", "facility_id"),
            ("cdu_reporting_prescription_pup_idx", "cdu_prescription", "collection_point_id"),
            ("cdu_reporting_prescription_next_pickup_idx", "cdu_prescription", "next_drug_pickup_date"),
            ("cdu_reporting_prescription_verified_idx", "cdu_prescription", "verified_at"),
            ("cdu_reporting_prescription_validated_idx", "cdu_prescription", "validated_at"),
            ("cdu_reporting_dispense_confirmed_idx", "cdu_dispense", "confirmed_at"),
            ("cdu_reporting_qa_confirmed_idx", "cdu_bagging_qa", "confirmed_at"),
            ("cdu_reporting_box_dispatched_idx", "cdu_box", "dispatched_at"),
        ]
        for index_name, table_name, column_name in indexes:
            cr.execute(
                'CREATE INDEX IF NOT EXISTS "%s" ON "%s" ("%s")'
                % (index_name, table_name, column_name)
            )


class CduReportVerification(models.Model):
    _name = "cdu.report.verification"
    _inherit = "cdu.report.common"
    _description = "CDU Prescription Verification Report"
    _rec_name = "prescription_number"
    _auto = False
    _log_access = False

    receipt_date = fields.Datetime(readonly=True)
    verification_date = fields.Datetime(readonly=True)
    verification_status = fields.Selection(
        [
            ("awaiting_verification", "Awaiting Verification"),
            ("verified", "Verified"),
            ("submitted_for_validation", "Submitted for Validation"),
            ("rejected", "Rejected"),
        ],
        readonly=True,
    )
    verifier_id = fields.Many2one("res.users", string="Verifier", readonly=True)
    rejection_reason = fields.Char(readonly=True)


class CduReportValidation(models.Model):
    _name = "cdu.report.validation"
    _inherit = "cdu.report.common"
    _description = "CDU Prescription Validation Report"
    _rec_name = "prescription_number"
    _auto = False
    _log_access = False

    validation_received_date = fields.Datetime(readonly=True)
    validation_date = fields.Datetime(readonly=True)
    validation_status = fields.Selection(
        [
            ("not_received", "Not Received"),
            ("awaiting_validation", "Awaiting Validation"),
            ("validated", "Validated"),
            ("released_for_batching", "Released for Batching"),
            ("rejected", "Rejected"),
        ],
        readonly=True,
    )
    validator_id = fields.Many2one("res.users", string="Validator", readonly=True)
    rejection_reason = fields.Char(readonly=True)


class CduReportDispensing(models.Model):
    _name = "cdu.report.dispensing"
    _inherit = "cdu.report.common"
    _description = "CDU Dispensing Report"
    _rec_name = "prescription_number"
    _auto = False
    _log_access = False

    production_received_at = fields.Datetime(readonly=True)
    dispensing_date = fields.Datetime(readonly=True)
    dispenser_id = fields.Many2one("res.users", string="Dispenser", readonly=True)
    item_count = fields.Integer(string="Items Dispensed", readonly=True, group_operator="sum")
    total_quantity_dispensed = fields.Float(readonly=True, group_operator="sum")
    medicine_names = fields.Char(readonly=True)
    fulfilment_status = fields.Selection(
        [
            ("fully_fulfilled", "Fully Fulfilled"),
            ("partially_fulfilled", "Partially Fulfilled"),
            ("not_dispensed", "Not Dispensed"),
        ],
        readonly=True,
    )
    dispensing_status = fields.Selection(
        [
            ("received_for_production", "Received for Production"),
            ("dispensed", "Dispensed"),
            ("rejected", "Rejected"),
            ("dispatched", "Dispatched"),
            ("collected", "Collected"),
            ("returned", "Returned"),
        ],
        readonly=True,
    )
    dispatch_date = fields.Datetime(readonly=True)
    collection_date = fields.Datetime(readonly=True)
    return_date = fields.Datetime(readonly=True)
    collect_go_status = fields.Char(string="Collect & Go Status", readonly=True)
    collection_status = fields.Char(readonly=True)


class CduReportParcelProduction(models.Model):
    _name = "cdu.report.parcel.production"
    _inherit = "cdu.report.common"
    _description = "CDU Parcel Production Report"
    _rec_name = "parcel_reference"
    _auto = False
    _log_access = False

    parcel_reference = fields.Char(string="Parcel Number", readonly=True)
    production_date = fields.Datetime(readonly=True)
    packager_id = fields.Many2one("res.users", string="Packager", readonly=True)
    dispatch_date = fields.Datetime(readonly=True)
    dispatch_status = fields.Selection(
        [("pending_dispatch", "Pending Dispatch"), ("dispatched", "Dispatched")],
        readonly=True,
    )
    collect_go_status = fields.Char(string="Collect & Go Status", readonly=True)


class CduReportParcelForecast(models.Model):
    _name = "cdu.report.parcel.forecast"
    _inherit = "cdu.report.common"
    _description = "CDU Parcel Forecast Report"
    _rec_name = "patient_name"
    _auto = False
    _log_access = False

    forecast_date = fields.Date(readonly=True)
    patients_due = fields.Integer(readonly=True, group_operator="sum")
    forecasted_parcels = fields.Integer(readonly=True, group_operator="sum")
    actual_parcels = fields.Integer(readonly=True, group_operator="sum")
    variance = fields.Integer(readonly=True, group_operator="sum")
    variance_percentage = fields.Float(readonly=True, group_operator="avg")
