from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "cdu_workflow")
class TestCduPrescriptionWorkflow(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.data_clerk = cls.env.ref("cdu_prescription.user_cdu_data_clerk")
        cls.call_agent = cls.env.ref("cdu_prescription.user_cdu_call_agent")
        cls.dispensing_officer = cls.env.ref(
            "cdu_prescription.user_cdu_dispensing_officer"
        )

        cls.facility = cls.env["cdu.facility"].create(
            {
                "name": "Workflow Test Facility",
                "code": "TEST-WORKFLOW-FACILITY",
            }
        )
        cls.collection_point = cls.env["cdu.collection.point"].create(
            {
                "name": "Workflow Test Collection Point",
                "code": "TEST-WORKFLOW-COLLECTION",
                "point_type": "e_locker",
            }
        )
        cls.patient = cls.env["res.partner"].create(
            {
                "name": "Workflow Test Patient",
                "customer_rank": 1,
                "cdu_eregister_id": "TEST-EREGISTER-001",
            }
        )

    def _create_prescription(self, **overrides):
        today = fields.Date.today()
        values = {
            "facility_id": self.facility.id,
            "facility_name": self.facility.name,
            "facility_code": self.facility.code,
            "prescription_date": today,
            "patient_id": self.patient.id,
            "patient_identifier": self.patient.cdu_eregister_id,
            "patient_first_name": self.patient.name,
            "regimen_prescribed_raw": "TEST-REGIMEN",
            "dosage_instructions": "Take one tablet daily.",
            "drug_pickup_point_raw": self.collection_point.name,
            "collection_point_id": self.collection_point.id,
            "next_drug_pickup_date": today + timedelta(days=30),
        }
        values.update(overrides)
        return self.env["cdu.prescription"].create(values)

    def test_verification_and_validation_move_to_expected_queues(self):
        prescription = self._create_prescription()

        verification_action = prescription.with_user(
            self.data_clerk
        ).action_mark_patient_verified()
        prescription.invalidate_recordset()

        self.assertEqual(prescription.state, "awaiting_validation")
        self.assertEqual(prescription.verified_by, self.data_clerk)
        self.assertEqual(
            verification_action["res_model"],
            "cdu.prescription",
        )

        validation_action = prescription.with_user(
            self.dispensing_officer
        ).action_mark_medicine_validated()
        prescription.invalidate_recordset()

        self.assertEqual(prescription.state, "awaiting_batching")
        self.assertEqual(prescription.validated_by, self.dispensing_officer)
        self.assertEqual(validation_action["res_model"], "cdu.prescription")

    def test_rejection_history_preserves_multiple_reasons_and_origin(self):
        prescription = self._create_prescription(state="awaiting_validation")
        reason_codes = [
            "missing_medicine_information",
            "missing_collection_information",
        ]

        prescription.with_user(self.dispensing_officer)._perform_rejection(
            "facility",
            reason_codes,
        )
        prescription.invalidate_recordset()

        self.assertEqual(prescription.state, "rejected_to_facility")
        self.assertEqual(prescription.rejected_from_state, "awaiting_validation")
        self.assertEqual(len(prescription.rejection_history_ids), 1)
        rejection = prescription.rejection_history_ids
        self.assertEqual(set(rejection.reason_ids.mapped("code")), set(reason_codes))
        self.assertTrue(rejection.is_active)

        prescription.with_user(self.data_clerk).action_return_to_previous_stage()
        prescription.invalidate_recordset()
        rejection.invalidate_recordset()

        self.assertEqual(prescription.state, "awaiting_validation")
        self.assertFalse(prescription.active_rejection_id)
        self.assertFalse(rejection.is_active)
        self.assertEqual(rejection.returned_by, self.data_clerk)
        self.assertTrue(rejection.returned_at)

    def test_call_agent_cannot_validate_prescription(self):
        prescription = self._create_prescription(state="awaiting_validation")

        with self.assertRaises(AccessError):
            prescription.with_user(
                self.call_agent
            ).action_mark_medicine_validated()

    def test_role_dashboards_expose_only_configured_cards(self):
        expectations = (
            (self.data_clerk, "data_clerk", 4),
            (self.call_agent, "call_agent", 2),
            (self.dispensing_officer, "dispensing_officer", 7),
        )
        for user, expected_role, expected_cards in expectations:
            dashboard = self.env["res.users"].with_user(
                user
            ).get_cdu_dashboard_data()
            cards = [
                card
                for section in dashboard["sections"]
                for card in section["cards"]
            ]
            self.assertEqual(dashboard["role"], expected_role)
            self.assertEqual(len(cards), expected_cards)
            self.assertTrue(all(card["action"] for card in cards))

    def test_validation_is_review_and_batching_starts_production(self):
        dashboard = self.env["res.users"].with_user(
            self.dispensing_officer
        ).get_cdu_dashboard_data()
        sections = {
            section["key"]: section["cards"]
            for section in dashboard["sections"]
        }

        self.assertIn(
            "Awaiting Validation",
            [card["title"] for card in sections["intake"]],
        )
        self.assertNotIn(
            "Awaiting Validation",
            [card["title"] for card in sections["production"]],
        )
        self.assertEqual(
            sections["production"][0]["title"],
            "Awaiting Batching",
        )

    def test_operational_prescription_filters_are_registered(self):
        search_view = self.env.ref("cdu_prescription.view_cdu_prescription_search")
        for filter_name in (
            "created_today",
            "waiting_two_days",
            "pickup_next_seven_days",
            "needs_attention",
            "group_created_day",
        ):
            self.assertIn('name="%s"' % filter_name, search_view.arch_db)

        report_search_view = self.env.ref(
            "cdu_prescription.view_cdu_report_run_search"
        )
        for filter_name in (
            "created_today",
            "needs_attention",
            "failed",
            "group_created_day",
        ):
            self.assertIn('name="%s"' % filter_name, report_search_view.arch_db)

    def test_batch_manifest_order_is_frozen_and_completion_order_flows_downstream(self):
        pickup_date = fields.Date.today() + timedelta(days=30)
        prescriptions = self.env["cdu.prescription"]
        for index in range(3):
            prescriptions |= self._create_prescription(
                patient_identifier="TEST-ORDER-%s" % index,
                patient_first_name="Order Patient %s" % index,
                next_drug_pickup_date=pickup_date,
                state="awaiting_batching",
            )

        batch = self.env["cdu.batch"].create(
            {
                "filter_next_drug_pickup_date_from": pickup_date,
                "filter_next_drug_pickup_date_to": pickup_date,
                "prescription_ids": [(6, 0, prescriptions.ids)],
            }
        )
        prescriptions[0].batch_sequence = 30
        prescriptions[1].batch_sequence = 10
        prescriptions[2].batch_sequence = 20

        batch.with_user(self.dispensing_officer).action_confirm_batch()
        prescriptions.invalidate_recordset()

        self.assertEqual(prescriptions[1].batch_sequence, 1)
        self.assertEqual(prescriptions[2].batch_sequence, 2)
        self.assertEqual(prescriptions[0].batch_sequence, 3)

        prescriptions[0].with_user(
            self.dispensing_officer
        )._assign_production_completion_sequence("dispensing")
        prescriptions[2].with_user(
            self.dispensing_officer
        )._assign_production_completion_sequence("dispensing")
        self.assertEqual(prescriptions[0].dispensing_sequence, 1)
        self.assertEqual(prescriptions[2].dispensing_sequence, 2)

        prescriptions[1].write({"state": "awaiting_dispensing"})
        prescriptions[1].with_user(self.dispensing_officer)._defer_production_stage(
            "dispensing",
            "Medicine tote is temporarily unavailable.",
        )
        self.assertTrue(prescriptions[1].dispensing_deferred)
        self.assertTrue(prescriptions[1].production_skip_history_ids.active)

        prescriptions[1].with_user(
            self.dispensing_officer
        )._assign_production_completion_sequence("dispensing")
        prescriptions[1].invalidate_recordset()
        self.assertEqual(prescriptions[1].dispensing_sequence, 3)
        self.assertFalse(prescriptions[1].dispensing_deferred)
        self.assertFalse(prescriptions[1].production_skip_history_ids.active)
        self.assertIn("planned #1", prescriptions[1].production_position_display)
