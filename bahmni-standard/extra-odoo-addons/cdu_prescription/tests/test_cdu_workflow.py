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
        self.assertEqual(verification_action["target"], "main")

        validation_action = prescription.with_user(
            self.dispensing_officer
        ).action_mark_medicine_validated()
        prescription.invalidate_recordset()

        self.assertEqual(prescription.state, "awaiting_batching")
        self.assertEqual(prescription.validated_by, self.dispensing_officer)
        self.assertEqual(validation_action["res_model"], "cdu.prescription")
        self.assertEqual(validation_action["target"], "main")

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

    def test_patient_dashboard_excludes_ordinary_odoo_customers(self):
        dashboard_model = self.env["res.users"]
        definition = dashboard_model._cdu_dashboard_card_definitions()["patients"]
        initial_count = dashboard_model._prepare_cdu_dashboard_card(definition)["count"]

        self.env["res.partner"].create(
            {
                "name": "Ordinary Odoo Customer",
                "customer_rank": 1,
            }
        )
        cdu_patient = self.env["res.partner"].create(
            {
                "name": "Additional CDU Patient",
                "customer_rank": 1,
                "cdu_eregister_id": "TEST-EREGISTER-DASHBOARD",
            }
        )

        card = dashboard_model._prepare_cdu_dashboard_card(definition)
        self.assertEqual(card["count"], initial_count + 1)
        self.assertEqual(
            self.env["res.partner"].search_count(definition["domain"]),
            card["count"],
        )
        self.assertIn(cdu_patient, self.env["res.partner"].search(definition["domain"]))

    def test_validation_is_part_of_intake_and_review(self):
        definitions = self.env["res.users"]._cdu_dashboard_card_definitions()

        self.assertEqual(definitions["awaiting_validation"]["section"], "intake")
        self.assertEqual(definitions["awaiting_batching"]["section"], "production")

    def test_prescription_queues_have_clear_empty_state_guidance(self):
        queue_actions = (
            "action_cdu_prescription_awaiting_verification",
            "action_cdu_prescription_awaiting_validation",
            "action_cdu_prescription_awaiting_batching",
            "action_cdu_prescription_awaiting_dispensing",
            "action_cdu_prescription_rejected_to_call_center",
            "action_cdu_prescription_rejected_to_facility",
        )
        for action_name in queue_actions:
            help_text = self.env.ref(
                "cdu_prescription.%s" % action_name
            ).help
            self.assertTrue(help_text)
            self.assertNotIn("Create new document", help_text)

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
