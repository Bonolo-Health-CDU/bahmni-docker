from datetime import timedelta
from unittest.mock import patch

from lxml import etree

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "cdu_dispensing")
class TestCduDispensingWorkflow(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = cls.env.ref("cdu_prescription.user_cdu_admin")
        cls.facility = cls.env["cdu.facility"].create(
            {
                "name": "Dispensing Test Facility",
                "code": "TEST-DISPENSING-FACILITY",
            }
        )
        cls.collection_point = cls.env["cdu.collection.point"].create(
            {
                "name": "Dispensing Test Collection Point",
                "code": "TEST-DISPENSING-COLLECTION",
                "point_type": "e_locker",
            }
        )
        cls.batch = cls.env["cdu.batch"].create({})
        cls.today = fields.Date.today()

    def _create_prescription(self, patient_suffix, pickup_days):
        patient = self.env["res.partner"].create(
            {
                "name": "Dispensing Test Patient %s" % patient_suffix,
                "customer_rank": 1,
                "cdu_eregister_id": "TEST-DISPENSING-%s" % patient_suffix,
            }
        )
        return self.env["cdu.prescription"].create(
            {
                "facility_id": self.facility.id,
                "facility_name": self.facility.name,
                "facility_code": self.facility.code,
                "prescription_date": self.today,
                "patient_id": patient.id,
                "patient_identifier": patient.cdu_eregister_id,
                "patient_first_name": patient.name,
                "regimen_prescribed_raw": "TEST-DISPENSING-REGIMEN",
                "dosage_instructions": "Take one tablet daily.",
                "drug_pickup_point_raw": self.collection_point.name,
                "collection_point_id": self.collection_point.id,
                "next_drug_pickup_date": self.today + timedelta(days=pickup_days),
                "batch_id": self.batch.id,
                "state": "awaiting_dispensing",
            }
        )

    def _create_ready_dispense(self, prescription):
        dispense = self.env["cdu.dispense"].with_context(
            cdu_skip_auto_refresh_production_stock=True
        ).create({"prescription_id": prescription.id})
        option = self.env["cdu.dispense.stock.option"].create(
            {
                "dispense_id": dispense.id,
                "facility_code": "TEST-CDU",
                "program_code": "TEST-PROGRAM",
                "orderable_code": "TEST-ORDERABLE",
                "orderable_name": "Test Medicine",
                "pack_size": 30,
                "lot": "TEST-LOT",
                "stock_on_hand": 20,
            }
        )
        dispense.stock_selection_ids.write(
            {
                "stock_option_id": option.id,
                "quantity_dispensed": 1,
                "dosage_instructions": "Take one tablet daily.",
            }
        )
        return dispense

    def test_confirm_prints_labels_and_opens_next_prescription_in_batch(self):
        first_prescription = self._create_prescription("001", 10)
        second_prescription = self._create_prescription("002", 20)
        first_dispense = self._create_ready_dispense(first_prescription)
        second_dispense = self._create_ready_dispense(second_prescription)
        expected_next = second_dispense._action_open()

        with patch.object(
            type(second_prescription),
            "action_open_dispensing",
            return_value=expected_next,
        ):
            result = first_dispense.with_user(
                self.admin
            ).action_confirm_dispensing()

        self.assertEqual(
            result["tag"],
            "cdu_print_dispensing_labels_and_continue",
        )
        self.assertEqual(result["params"]["report_action"]["type"], "ir.actions.report")
        self.assertEqual(result["params"]["next"]["res_id"], second_dispense.id)
        self.assertEqual(first_dispense.state, "confirmed")
        self.assertTrue(first_dispense.labels_printed)
        self.assertEqual(first_prescription.state, "awaiting_bagging_qa")
        self.assertEqual(second_prescription.state, "awaiting_dispensing")

    def test_exhausted_batch_returns_to_dispensing_work_queue(self):
        prescription = self._create_prescription("003", 30)
        dispense = self._create_ready_dispense(prescription)
        prescription.state = "awaiting_bagging_qa"

        action = dispense._get_next_dispensing_action()

        expected_action = self.env.ref(
            "cdu_elmis.action_cdu_dispensing_work_queue"
        )
        self.assertEqual(action["id"], expected_action.id)
        self.assertEqual(action["res_model"], "cdu.prescription")
        self.assertEqual(action["views"], [(False, "tree"), (False, "form")])

    def test_next_button_is_removed_and_later_prescriptions_can_reprint(self):
        prescription = self._create_prescription("004", 40)
        dispense = self._create_ready_dispense(prescription)
        dispense.write(
            {
                "state": "confirmed",
                "confirmed_by": self.admin.id,
                "confirmed_at": fields.Datetime.now(),
            }
        )
        prescription.state = "awaiting_boxing"

        view = self.env.ref("cdu_elmis.view_cdu_dispense_form")
        arch = etree.fromstring(view.arch_db.encode())
        self.assertFalse(
            arch.xpath(".//button[@name='action_open_next_dispensing_task']")
        )

        prescription_button_view = self.env[
            "cdu.prescription"
        ].with_user(self.admin).get_view(
            view_id=self.env.ref(
                "cdu_prescription.view_cdu_prescription_form"
            ).id,
            view_type="form",
        )
        prescription_arch = etree.fromstring(
            prescription_button_view["arch"].encode()
        )
        self.assertTrue(
            prescription_arch.xpath(
                ".//button[@name='action_reprint_dispensing_labels']"
            )
        )

        action = prescription.with_user(
            self.admin
        ).action_reprint_dispensing_labels()
        self.assertEqual(action["type"], "ir.actions.report")
