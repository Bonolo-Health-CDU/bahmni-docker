import base64
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "cdu_reporting")
class TestCduReporting(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.today()
        cls.now = fields.Datetime.now()
        cls.admin = cls.env.ref("base.user_admin")
        cls.facility = cls.env["cdu.facility"].create(
            {"name": "Maseru Test Facility", "code": "RPT-MAS"}
        )
        cls.other_facility = cls.env["cdu.facility"].create(
            {"name": "Leribe Test Facility", "code": "RPT-LER"}
        )
        cls.pup = cls.env["cdu.collection.point"].create(
            {
                "name": "Maseru eLocker",
                "code": "RPT-PUP",
                "point_type": "e_locker",
                "region_name": "Maseru",
            }
        )
        cls.patient = cls.env["res.partner"].create(
            {
                "name": "Reporting Patient",
                "customer_rank": 1,
                "cdu_eregister_id": "RPT-PATIENT",
                "cdu_gender": "female",
                "cdu_date_of_birth": cls.today.replace(year=cls.today.year - 30),
            }
        )
        cls.product = cls.env["product.template"].create(
            {
                "name": "Reporting TDF/3TC/DTG",
                "type": "product",
                "cdu_is_drug": True,
            }
        ).product_variant_id
        cls.batch = cls.env["cdu.batch"].create({})

        cls.completed = cls._create_prescription("COMPLETED", "new", cls.facility)
        cls.completed.write(
            {"state": "awaiting_validation", "verified_at": cls.now, "verified_by": cls.admin.id}
        )
        cls.completed.write(
            {"state": "awaiting_batching", "validated_at": cls.now, "validated_by": cls.admin.id}
        )
        cls.completed.write({"batch_id": cls.batch.id, "state": "awaiting_dispensing"})
        for index, drug_name in enumerate(("TDF/3TC/DTG", "Cotrimoxazole"), start=1):
            cls.env["cdu.picking.line"].create(
                {
                    "batch_id": cls.batch.id,
                    "prescription_id": cls.completed.id,
                    "openmrs_drug_name": drug_name,
                    "openmrs_drug_uuid": "RPT-DRUG-%s" % index,
                    "quantity_to_pick": 1,
                }
            )
        cls.dispense = cls.env["cdu.dispense"].with_context(
            cdu_skip_auto_refresh_production_stock=True,
            cdu_allow_dispense_line_sync=True,
        ).create(
            {
                "prescription_id": cls.completed.id,
                "state": "confirmed",
                "confirmed_by": cls.admin.id,
                "confirmed_at": cls.now,
            }
        )
        cls.dispense.with_context(
            cdu_allow_dispense_line_sync=True
        ).stock_selection_ids.unlink()
        cls.env["cdu.dispense.stock.selection"].with_context(
            cdu_allow_dispense_line_sync=True
        ).create(
            [
                {
                    "dispense_id": cls.dispense.id,
                    "openmrs_drug_name": "TDF/3TC/DTG",
                    "selected_orderable_name": "TLD",
                    "selected_pack_size": 30,
                    "quantity_dispensed": 2,
                },
                {
                    "dispense_id": cls.dispense.id,
                    "openmrs_drug_name": "Cotrimoxazole",
                    "selected_orderable_name": "CTX",
                    "selected_pack_size": 30,
                    "quantity_dispensed": 1,
                },
            ]
        )
        cls.completed.write({"state": "awaiting_bagging_qa"})
        cls.qa = cls.env["cdu.bagging.qa"].create(
            {
                "prescription_id": cls.completed.id,
                "dispense_id": cls.dispense.id,
                "parcel_reference": "RPT-PARCEL-1",
                "state": "confirmed",
                "bagged_by": cls.admin.id,
                "bagged_at": cls.now,
                "confirmed_by": cls.admin.id,
                "confirmed_at": cls.now,
            }
        )
        cls.completed.write({"state": "awaiting_boxing"})
        cls.box = cls.env["cdu.box"].create(
            {"name": "RPT-BOX-1", "collection_point_id": cls.pup.id}
        )
        cls.box_line = cls.env["cdu.box.line"].create(
            {"box_id": cls.box.id, "bagging_qa_id": cls.qa.id}
        )
        cls.box.write(
            {
                "state": "confirmed",
                "confirmed_by": cls.admin.id,
                "confirmed_at": cls.now,
                "dispatch_status": "dispatched",
                "dispatched_by": cls.admin.id,
                "dispatched_at": cls.now,
                "collect_go_status": "processed",
            }
        )
        cls.box_line.write(
            {
                "collect_go_parcel_status": "Collected",
                "collect_go_status_received_at": cls.now,
            }
        )
        cls.completed.write({"state": "awaiting_dispatch"})
        cls.completed.write({"state": "dispatched"})

        cls.awaiting = cls._create_prescription("AWAITING", "revisit", cls.other_facility)
        cls.submitted = cls._create_prescription("SUBMITTED", "revisit", cls.facility)
        cls.submitted.write(
            {"state": "awaiting_validation", "verified_at": cls.now, "verified_by": cls.admin.id}
        )
        cls.released = cls._create_prescription("RELEASED", "restarted", cls.facility)
        cls.released.write(
            {"state": "awaiting_validation", "verified_at": cls.now, "verified_by": cls.admin.id}
        )
        cls.released.write(
            {"state": "awaiting_batching", "validated_at": cls.now, "validated_by": cls.admin.id}
        )
        cls.verification_rejected = cls._create_prescription(
            "VER-REJECT", "revisit", cls.other_facility
        )
        cls._reject(cls.verification_rejected, "awaiting_verification")
        cls.validation_rejected = cls._create_prescription(
            "VAL-REJECT", "revisit", cls.other_facility
        )
        cls.validation_rejected.write(
            {"state": "awaiting_validation", "verified_at": cls.now, "verified_by": cls.admin.id}
        )
        cls._reject(cls.validation_rejected, "awaiting_validation")

    @classmethod
    def _create_prescription(cls, suffix, new_or_revisit, facility):
        prescription = cls.env["cdu.prescription"].create(
            {
                "source_key": "RPT-%s" % suffix,
                "facility_id": facility.id,
                "prescription_date": cls.today,
                "patient_id": cls.patient.id,
                "hiv_program_id": "ART",
                "new_or_revisit": new_or_revisit,
                "next_drug_pickup_date": cls.today + timedelta(days=30),
                "collection_point_id": cls.pup.id,
                "regimen_prescribed_raw": "TDF/3TC/DTG",
                "dosage_instructions": "Take one tablet daily.",
            }
        )
        prescription.product_line_ids.product_id = cls.product.id
        return prescription

    @classmethod
    def _reject(cls, prescription, stage):
        reason = cls.env["cdu.rejection.reason"].search([], limit=1)
        rejection = cls.env["cdu.prescription.rejection"].create(
            {
                "prescription_id": prescription.id,
                "stage": stage,
                "destination": "facility",
                "reason_ids": [(6, 0, reason.ids)],
                "rejected_by": cls.admin.id,
                "rejected_at": cls.now,
            }
        )
        prescription.write(
            {
                "state": "rejected_to_facility",
                "rejected_from_state": stage,
                "active_rejection_id": rejection.id,
            }
        )

    def test_enrolment_filters_and_dimensions(self):
        reports = self.env["cdu.report.enrolment"].search(
            [("enrolment_date", "=", self.today)]
        )
        self.assertEqual(len(reports), 6)
        self.assertEqual(
            len(reports.filtered(lambda row: row.facility_id == self.other_facility)), 3
        )
        completed = reports.filtered(lambda row: row.prescription_id == self.completed)
        self.assertEqual(completed.new_or_repeat, "new")
        self.assertEqual(completed.age_group, "25-34")
        self.assertEqual(completed.district_name, "Maseru")

    def test_verification_and_validation_statuses(self):
        verification = self.env["cdu.report.verification"].search([])
        statuses = {
            row.prescription_id.id: row.verification_status for row in verification
        }
        self.assertEqual(statuses[self.awaiting.id], "awaiting_verification")
        self.assertEqual(statuses[self.submitted.id], "submitted_for_validation")
        self.assertEqual(statuses[self.completed.id], "verified")
        self.assertEqual(statuses[self.verification_rejected.id], "rejected")

        validation = self.env["cdu.report.validation"].search([])
        statuses = {row.prescription_id.id: row.validation_status for row in validation}
        self.assertEqual(statuses[self.submitted.id], "awaiting_validation")
        self.assertEqual(statuses[self.completed.id], "validated")
        self.assertEqual(statuses[self.released.id], "released_for_batching")
        self.assertEqual(statuses[self.validation_rejected.id], "rejected")

    def test_dispensing_reconciles_multiple_medicines_once(self):
        report = self.env["cdu.report.dispensing"].search(
            [("prescription_id", "=", self.completed.id)]
        )
        self.assertEqual(len(report), 1)
        self.assertEqual(report.item_count, 2)
        self.assertEqual(report.total_quantity_dispensed, 90)
        self.assertEqual(report.fulfilment_status, "fully_fulfilled")
        self.assertEqual(report.dispensing_status, "collected")

    def test_parcel_production_and_forecast(self):
        parcel = self.env["cdu.report.parcel.production"].search(
            [("prescription_id", "=", self.completed.id)]
        )
        self.assertEqual(len(parcel), 1)
        self.assertEqual(parcel.parcel_reference, "RPT-PARCEL-1")
        self.assertEqual(parcel.dispatch_status, "dispatched")
        forecast = self.env["cdu.report.parcel.forecast"].search(
            [("prescription_id", "=", self.completed.id)]
        )
        self.assertEqual(forecast.forecasted_parcels, 1)
        self.assertEqual(forecast.actual_parcels, 1)
        self.assertEqual(forecast.variance, 0)

    def test_lifecycle_history_is_complete_and_immutable(self):
        states = self.completed.status_history_ids.mapped("new_status")
        for state in (
            "awaiting_verification", "awaiting_validation", "awaiting_batching",
            "awaiting_dispensing", "awaiting_bagging_qa", "awaiting_boxing",
            "awaiting_dispatch", "dispatched",
        ):
            self.assertIn(state, states)
        with self.assertRaises(AccessError):
            self.completed.status_history_ids[:1].write({"reason": "Changed"})

    def test_operational_user_transition_creates_system_managed_history(self):
        data_clerk = self.env.ref("cdu_prescription.user_cdu_data_clerk")
        prescription = self._create_prescription(
            "DATA-CLERK-HISTORY",
            "new",
            self.facility,
        )

        prescription.with_user(data_clerk).action_mark_patient_verified()
        history = prescription.status_history_ids.filtered(
            lambda event: event.new_status == "awaiting_validation"
        )

        self.assertTrue(history)
        self.assertEqual(history[:1].changed_by, data_clerk)

    def test_wizard_filters_and_exports_same_dataset(self):
        wizard = self.env["cdu.report.wizard"].create(
            {
                "report_type": "enrolment",
                "date_from": self.today,
                "date_to": self.today,
                "facility_ids": [(6, 0, self.facility.ids)],
                "gender": "female",
            }
        )
        records = wizard._records()
        self.assertEqual(len(records), 3)
        wizard.action_export_xlsx()
        self.assertTrue(base64.b64decode(wizard.export_file).startswith(b"PK"))
        wizard.action_export_csv()
        csv_content = base64.b64decode(wizard.export_file).decode("utf-8-sig")
        self.assertEqual(csv_content.count("\n") - 1, len(records))
        self.assertIn("Patient Name", csv_content)
        pdf_content, output_type = self.env["ir.actions.report"].with_context(
            force_report_rendering=True
        )._render_qweb_pdf("cdu_reporting.report_cdu_summary", res_ids=wizard.ids)
        self.assertEqual(output_type, "pdf")
        self.assertTrue(pdf_content.startswith(b"%PDF"))
