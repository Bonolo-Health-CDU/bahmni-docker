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

    def test_regimen_products_have_independent_dosage_and_stable_order(self):
        prescription = self._create_prescription("005", 30)
        dispense = self.env["cdu.dispense"].with_context(
            cdu_skip_auto_refresh_production_stock=True
        ).create({"prescription_id": prescription.id})
        first_line = dispense.stock_selection_ids
        second_line = self.env["cdu.dispense.stock.selection"].create(
            {
                "dispense_id": dispense.id,
                "openmrs_drug_name": "AAA Second Product",
                "dosage_instructions": "Take two tablets at night.",
            }
        )

        self.assertEqual(
            first_line.dosage_instructions,
            prescription.dosage_instructions,
        )
        first_line.dosage_instructions = "Take one tablet each morning."

        self.assertEqual(
            second_line.dosage_instructions,
            "Take two tablets at night.",
        )
        self.assertEqual(
            prescription.dosage_instructions,
            "Take one tablet daily.",
        )
        self.assertEqual(dispense.stock_selection_ids, first_line | second_line)

    def test_product_selection_supplies_the_regimen_product_name(self):
        prescription = self._create_prescription("006", 30)
        dispense = self.env["cdu.dispense"].with_context(
            cdu_skip_auto_refresh_production_stock=True
        ).create({"prescription_id": prescription.id})
        option = self.env["cdu.dispense.stock.option"].create(
            {
                "dispense_id": dispense.id,
                "facility_code": "TEST-CDU",
                "program_code": "TEST-PROGRAM",
                "orderable_code": "DYNAMIC-PRODUCT",
                "orderable_name": "Dynamic Product",
                "pack_size": 30,
                "lot": "DYNAMIC-LOT",
                "stock_on_hand": 20,
            }
        )

        line = self.env["cdu.dispense.stock.selection"].create(
            {
                "dispense_id": dispense.id,
                "stock_option_id": option.id,
            }
        )

        self.assertEqual(line.openmrs_drug_name, "Dynamic Product")
        self.assertEqual(
            line.dosage_instructions,
            prescription.dosage_instructions,
        )

    def test_elmis_stock_products_feed_verification_and_propagate_to_dispensing(self):
        option_values = {
            "batch_id": self.batch.id,
            "facility_code": "TEST-CDU-STORE",
            "program_code": "TEST-PROGRAM",
            "orderable_code": "ELMIS-TLD-30",
            "orderable_id": "ELMIS-TLD-UUID",
            "orderable_name": "Tenofovir Lamivudine Dolutegravir Tablets 30",
            "pack_size": 30,
            "lot": "TLD-FEFO-LOT",
            "stock_on_hand": 20,
            "expiration_date": self.today + timedelta(days=365),
        }
        option = self.env["cdu.elmis.stock.option"].create(option_values)
        product = self.env["product.product"].search(
            [("product_tmpl_id.cdu_elmis_orderable_id", "=", "ELMIS-TLD-UUID")],
            limit=1,
        )
        self.assertTrue(product)
        self.assertTrue(product.product_tmpl_id.cdu_is_drug)
        self.assertEqual(product.product_tmpl_id.cdu_pack_size, 30)

        prescription = self._create_prescription("ELMIS-PROPAGATION", 30)
        prescription.write(
            {
                "next_clinical_visit_date": self.today + timedelta(days=60),
                "repeat_days": 30,
            }
        )
        prescription.product_line_ids.with_context(
            cdu_allow_product_line_sync=True
        ).write(
            {
                "product_id": product.id,
                "dosage_instructions": "Take one tablet every morning.",
            }
        )
        self.batch._generate_elmis_picking_lines()
        self.batch._apply_prescription_product_stock_options()

        picking_line = self.batch.elmis_picking_line_ids.filtered(
            lambda line: line.summary_line_id.product_id == product
        )
        self.assertTrue(picking_line)
        self.assertEqual(
            picking_line.fulfilment_line_ids.selected_stock_option_id,
            option,
        )

        dispense = self.env["cdu.dispense"].with_context(
            cdu_skip_auto_refresh_production_stock=True
        ).create({"prescription_id": prescription.id})
        dispense_line = dispense.stock_selection_ids.filtered(
            lambda line: line.selected_orderable_id == "ELMIS-TLD-UUID"
        )
        self.assertTrue(dispense_line)
        self.assertEqual(
            dispense_line.dosage_instructions,
            "Take one tablet every morning.",
        )

    def test_regimen_products_are_locked_after_confirmation(self):
        prescription = self._create_prescription("007", 30)
        dispense = self.env["cdu.dispense"].with_context(
            cdu_skip_auto_refresh_production_stock=True
        ).create({"prescription_id": prescription.id})
        line = dispense.stock_selection_ids
        dispense.state = "confirmed"

        with self.assertRaises(ValidationError):
            line.write({"dosage_instructions": "Changed after confirmation"})
        with self.assertRaises(ValidationError):
            line.unlink()
        with self.assertRaises(ValidationError):
            self.env["cdu.dispense.stock.selection"].create(
                {
                    "dispense_id": dispense.id,
                    "openmrs_drug_name": "Late Product",
                }
            )

    def test_regimen_view_uses_an_inline_dynamic_product_list(self):
        view = self.env.ref("cdu_elmis.view_cdu_dispense_form")
        arch = etree.fromstring(view.arch_db.encode())
        product_list = arch.xpath(".//field[@name='stock_selection_ids']")[0]
        tree = product_list.xpath("./tree")[0]
        field_names = [field.get("name") for field in tree.xpath("./field")]

        self.assertEqual(tree.get("editable"), "bottom")
        self.assertEqual(
            tree.xpath("./control/create")[0].get("string"),
            "Add Product",
        )
        self.assertIn("state", product_list.get("attrs"))
        self.assertLess(
            field_names.index("stock_option_id"),
            field_names.index("dosage_instructions"),
        )
        self.assertTrue(arch.xpath(".//field[@name='duration_days']"))
        self.assertTrue(
            arch.xpath(
                ".//field[@name='next_drug_pickup_date'][@string='Next Refill Date']"
            )
        )
