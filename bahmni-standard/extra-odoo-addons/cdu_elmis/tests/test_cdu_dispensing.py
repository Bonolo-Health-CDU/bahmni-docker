from datetime import timedelta
from unittest.mock import patch

from lxml import etree

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "cdu_dispensing")
class TestCduDispensingWorkflow(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = cls.env.ref("cdu_prescription.user_cdu_admin")
        cls.dispensing_officer = cls.env.ref(
            "cdu_prescription.user_cdu_dispensing_officer"
        )
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
        self.assertEqual(
            result["params"]["next"]["views"],
            [
                (
                    self.env.ref("cdu_elmis.view_cdu_dispense_form").id,
                    "form",
                )
            ],
        )
        self.assertEqual(first_dispense.state, "confirmed")
        self.assertTrue(first_dispense.labels_printed)
        self.assertEqual(first_prescription.state, "awaiting_bagging_qa")
        self.assertEqual(second_prescription.state, "awaiting_dispensing")

    def test_exhausted_batch_returns_to_dispensing_work_queue(self):
        prescription = self._create_prescription("003", 30)
        dispense = self._create_ready_dispense(prescription)
        prescription.state = "awaiting_bagging_qa"

        self.assertFalse(
            self.dispensing_officer.has_group("base.group_system")
        )
        action = dispense.with_user(
            self.dispensing_officer
        )._get_next_dispensing_action()

        expected_action = self.env.ref(
            "cdu_elmis.action_cdu_dispensing_work_queue"
        )
        self.assertEqual(action["id"], expected_action.id)
        self.assertEqual(action["res_model"], "cdu.prescription")
        self.assertEqual(action["views"], [(False, "tree"), (False, "form")])

    def test_exhausted_bagging_qa_returns_non_admin_to_work_queue(self):
        prescription = self._create_prescription("004", 40)
        dispense = self._create_ready_dispense(prescription)
        prescription.state = "awaiting_bagging_qa"
        qa_record = self.env["cdu.bagging.qa"].create(
            {
                "prescription_id": prescription.id,
                "dispense_id": dispense.id,
            }
        )

        action = qa_record.with_user(
            self.dispensing_officer
        )._get_next_bagging_qa_action()

        expected_action = self.env.ref(
            "cdu_elmis.action_cdu_bagging_qa_work_queue"
        )
        self.assertEqual(action["id"], expected_action.id)
        self.assertEqual(action["res_model"], "cdu.prescription")
        self.assertEqual(action["views"], [(False, "tree"), (False, "form")])
        self.assertEqual(
            action["domain"],
            [
                ("state", "=", "awaiting_bagging_qa"),
                ("batch_id", "=", self.batch.id),
            ],
        )

    def test_legacy_raw_regimen_remains_available_to_picking_workflow(self):
        prescription = self._create_prescription("005", 30)
        prescription.next_clinical_visit_date = self.today + timedelta(days=60)

        patient_lines, summary_lines = self.batch._prepare_picking_line_values()

        patient_line = next(
            values
            for values in patient_lines
            if values["prescription_id"] == prescription.id
        )
        summary_line = next(
            values
            for values in summary_lines
            if values["unmapped_drug_name"] == "TEST-DISPENSING-REGIMEN"
        )
        self.assertFalse(patient_line["product_id"])
        self.assertEqual(
            patient_line["drug_name"],
            prescription.regimen_prescribed_raw,
        )
        self.assertFalse(summary_line["product_id"])
        self.assertEqual(summary_line["prescription_count"], 1)
        self.assertIn("cdu.regimen", self.env.registry.models)

    def test_regimen_constituents_each_create_a_picking_requirement(self):
        prescription = self._create_prescription("008", 30)
        prescription.write({"regimen_prescribed_raw": "2k=ABC-3TC-DRV-r"})
        prescription.next_clinical_visit_date = self.today + timedelta(days=60)

        patient_lines, summary_lines = self.batch._prepare_picking_line_values()
        prescription_lines = [
            values
            for values in patient_lines
            if values["prescription_id"] == prescription.id
        ]

        self.assertEqual(
            {values["drug_name"] for values in prescription_lines},
            {"Abacavir/Lamivudine", "Darunavir/ritonavir"},
        )
        self.assertTrue(
            all(values["prescription_medicine_line_id"] for values in prescription_lines)
        )
        self.assertTrue(
            {"Abacavir/Lamivudine", "Darunavir/ritonavir"}.issubset(
                {values["unmapped_drug_name"] for values in summary_lines}
            )
        )

    def test_dispensing_dosage_is_copied_from_matching_prescription_medicine(self):
        prescription = self._create_prescription("009", 30)
        prescription.write({"regimen_prescribed_raw": "2k=ABC-3TC-DRV-r"})
        medicine_line = prescription.medicine_line_ids.filtered(
            lambda line: line.medicine_id.name == "Darunavir/ritonavir"
        )
        medicine_line.dosage_instructions = "Take with the evening meal."
        dispense = self.env["cdu.dispense"].with_context(
            cdu_skip_auto_refresh_production_stock=True
        ).create({"prescription_id": prescription.id})

        selection = self.env["cdu.dispense.stock.selection"].create(
            {
                "dispense_id": dispense.id,
                "prescription_medicine_line_id": medicine_line.id,
                "openmrs_drug_name": medicine_line.medicine_id.name,
            }
        )

        self.assertEqual(
            selection.dosage_instructions,
            "Take with the evening meal.",
        )

    def test_medicine_change_discards_unconfirmed_picking_calculations(self):
        prescription = self._create_prescription("010", 30)
        prescription.write({"regimen_prescribed_raw": "2k=ABC-3TC-DRV-r"})
        prescription.next_clinical_visit_date = self.today + timedelta(days=60)
        self.batch._generate_elmis_picking_lines()
        self.assertTrue(self.batch.elmis_picking_line_ids)
        self.assertTrue(self.batch.patient_picking_line_ids)
        additional = self.env["cdu.medicine"].create(
            {"name": "Dispensing Test Additional Medicine"}
        )

        self.env["cdu.prescription.medicine.line"].create(
            {
                "prescription_id": prescription.id,
                "medicine_id": additional.id,
                "dosage_instructions": "Take once daily.",
            }
        )

        self.assertFalse(self.batch.elmis_picking_line_ids)
        self.assertFalse(self.batch.patient_picking_line_ids)
        self.assertFalse(self.batch.picking_line_ids)

    def test_medicine_table_locks_at_confirmed_picking(self):
        prescription = self._create_prescription("011", 30)
        prescription.write({"regimen_prescribed_raw": "2k=ABC-3TC-DRV-r"})
        medicine_line = prescription.medicine_line_ids[:1]
        self.batch.picking_confirmed_at = fields.Datetime.now()

        with self.assertRaises(ValidationError):
            medicine_line.dosage_instructions = "A late clinical change."
        with self.assertRaises(ValidationError):
            prescription.regimen_option_id = self.env.ref(
                "cdu_prescription.option_2k_abc_drv_separate"
            )

    def test_dispensing_dosage_locks_at_confirmed_dispensing(self):
        prescription = self._create_prescription("012", 30)
        dispense = self.env["cdu.dispense"].with_context(
            cdu_skip_auto_refresh_production_stock=True
        ).create({"prescription_id": prescription.id})
        selection = self.env["cdu.dispense.stock.selection"].create(
            {
                "dispense_id": dispense.id,
                "openmrs_drug_name": "Test Medicine",
                "dosage_instructions": "Take once daily.",
            }
        )
        dispense.state = "confirmed"

        with self.assertRaises(ValidationError):
            selection.dosage_instructions = "Changed after confirmation."

    def test_product_dosing_instructions_are_independent(self):
        prescription = self._create_prescription("007", 30)
        dispense = self.env["cdu.dispense"].with_context(
            cdu_skip_auto_refresh_production_stock=True
        ).create({"prescription_id": prescription.id})
        first_line, second_line = self.env[
            "cdu.dispense.stock.selection"
        ].create(
            [
                {
                    "dispense_id": dispense.id,
                    "openmrs_drug_name": "First regimen component",
                    "selected_orderable_name": "First eLMIS Product",
                },
                {
                    "dispense_id": dispense.id,
                    "openmrs_drug_name": "Second regimen component",
                    "selected_orderable_name": "Second eLMIS Product",
                },
            ]
        )

        self.assertEqual(
            first_line.dosage_instructions,
            prescription.dosage_instructions,
        )
        self.assertEqual(
            second_line.dosage_instructions,
            prescription.dosage_instructions,
        )

        first_line.dosage_instructions = "Take the first product in the morning."

        self.assertEqual(
            first_line.dosage_instructions,
            "Take the first product in the morning.",
        )
        self.assertEqual(
            second_line.dosage_instructions,
            "Take one tablet daily.",
        )
        self.assertEqual(
            prescription.dosage_instructions,
            "Take one tablet daily.",
        )

    def test_next_button_is_removed_and_later_prescriptions_can_reprint(self):
        prescription = self._create_prescription("006", 40)
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
