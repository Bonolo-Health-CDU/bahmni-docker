from datetime import timedelta

from lxml import etree

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "cdu_boxing")
class TestCduBoxingWorkflow(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = cls.env.ref("cdu_prescription.user_cdu_admin")
        cls.facility = cls.env["cdu.facility"].create(
            {
                "name": "Boxing Test Facility",
                "code": "TEST-BOXING-FACILITY",
            }
        )
        cls.collection_point = cls.env["cdu.collection.point"].create(
            {
                "name": "Boxing Test Collection Point",
                "code": "TEST-BOXING-COLLECTION",
                "point_type": "e_locker",
            }
        )
        cls.patient = cls.env["res.partner"].create(
            {
                "name": "Boxing Test Patient",
                "customer_rank": 1,
                "cdu_eregister_id": "TEST-BOXING-EREGISTER",
            }
        )
        today = fields.Date.today()
        cls.prescription = cls.env["cdu.prescription"].create(
            {
                "facility_id": cls.facility.id,
                "facility_name": cls.facility.name,
                "facility_code": cls.facility.code,
                "prescription_date": today,
                "patient_id": cls.patient.id,
                "patient_identifier": cls.patient.cdu_eregister_id,
                "patient_first_name": cls.patient.name,
                "regimen_prescribed_raw": "TEST-BOXING-REGIMEN",
                "dosage_instructions": "Take one tablet daily.",
                "drug_pickup_point_raw": cls.collection_point.name,
                "collection_point_id": cls.collection_point.id,
                "next_drug_pickup_date": today + timedelta(days=30),
                "state": "awaiting_boxing",
            }
        )
        cls.dispense = cls.env["cdu.dispense"].with_context(
            cdu_skip_auto_refresh_production_stock=True
        ).create(
            {
                "prescription_id": cls.prescription.id,
                "state": "confirmed",
            }
        )
        cls.parcel = cls.env["cdu.bagging.qa"].create(
            {
                "prescription_id": cls.prescription.id,
                "dispense_id": cls.dispense.id,
                "state": "confirmed",
                "parcel_reference": "TEST-PARCEL-001",
            }
        )

    def test_barcode_scan_adds_one_parcel_and_reports_capacity(self):
        box = self.env["cdu.box"].with_user(self.admin).create(
            {"max_parcels": 20}
        )

        result = box.action_scan_bag_label(self.prescription.name)
        box.invalidate_recordset()

        self.assertEqual(box.parcel_count, 1)
        self.assertEqual(box.line_ids.bagging_qa_id, self.parcel)
        self.assertEqual(box.collection_point_id, self.collection_point)
        self.assertEqual(
            result["params"]["className"],
            "o_cdu_scan_notification o_cdu_scan_notification_success",
        )
        self.assertIn("1 of 20", result["params"]["message"])

    def test_duplicate_barcode_scan_is_blocked(self):
        box = self.env["cdu.box"].with_user(self.admin).create({})
        box.action_scan_bag_label(self.prescription.name)

        with self.assertRaises(UserError):
            box.action_scan_bag_label(self.prescription.name)

    def test_unknown_barcode_is_rejected(self):
        box = self.env["cdu.box"].with_user(self.admin).create({})

        with self.assertRaises(UserError):
            box.action_scan_bag_label("NOT-A-CDU-BAG-LABEL")

    def test_boxing_filters_are_registered(self):
        for view_xmlid in (
            "cdu_elmis.view_cdu_dispense_search",
            "cdu_elmis.view_cdu_bagging_qa_search",
            "cdu_elmis.view_cdu_box_search",
        ):
            search_view = self.env.ref(view_xmlid)
            for filter_name in (
                "created_today",
                "pickup_next_seven_days",
                "group_created_day",
            ):
                self.assertIn('name="%s"' % filter_name, search_view.arch_db)

    def test_dispensed_units_are_computed_and_packs_are_hidden_by_default(self):
        selection = self.env["cdu.dispense.stock.selection"].create(
            {
                "dispense_id": self.dispense.id,
                "openmrs_drug_name": "TEST-BOXING-REGIMEN",
                "selected_pack_size": 30,
                "quantity_dispensed": 3,
            }
        )
        self.assertEqual(selection.dispensed_units, 90)

        selection.quantity_dispensed = 2.5
        self.assertEqual(selection.dispensed_units, 75)

        view = self.env.ref("cdu_elmis.view_cdu_dispense_form")
        arch = etree.fromstring(view.arch_db.encode())
        packs_field = arch.xpath(
            ".//field[@name='stock_selection_ids']/tree"
            "/field[@name='quantity_dispensed']"
        )
        units_field = arch.xpath(
            ".//field[@name='stock_selection_ids']/tree"
            "/field[@name='dispensed_units']"
        )
        self.assertEqual(packs_field[0].get("optional"), "hide")
        self.assertEqual(
            packs_field[0].get("widget"),
            "cdu_no_trailing_zeros_float",
        )
        self.assertTrue(units_field)
        self.assertEqual(
            units_field[0].get("widget"),
            "cdu_no_trailing_zeros_float",
        )
