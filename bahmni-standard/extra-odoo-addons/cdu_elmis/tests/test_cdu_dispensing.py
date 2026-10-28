import base64
import re
from datetime import timedelta
from unittest.mock import patch

from lxml import etree, html as lxml_html

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import Form, TransactionCase, tagged


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

    def _confirm_dispense_for_bagging(self, dispense):
        dispense.write(
            {
                "state": "confirmed",
                "confirmed_by": self.admin.id,
                "confirmed_at": fields.Datetime.now(),
                "labels_printed_by": self.admin.id,
                "labels_printed_at": fields.Datetime.now(),
            }
        )
        dispense.prescription_id.state = "awaiting_bagging_qa"

    def _create_ready_bagging_qa(self, prescription, dispense):
        return self.env["cdu.bagging.qa"].create(
            {
                "prescription_id": prescription.id,
                "dispense_id": dispense.id,
                "patient_details_checked": True,
                "medicine_product_checked": True,
                "quantity_checked": True,
                "dosing_instructions_checked": True,
                "product_labels_attached": True,
                "bag_label_attached": True,
                "medicines_placed_in_bag": True,
                "bag_sealed": True,
            }
        )

    def _create_patient_picking_context(
        self,
        prescription,
        required_packs=2,
        picked_packs=2,
        pack_size=30,
        daily_dose=1,
        cdu_days=60,
    ):
        patient_line = self.env["cdu.batch.patient.line"].create(
            {
                "batch_id": self.batch.id,
                "prescription_id": prescription.id,
                "patient_id": prescription.patient_id.id,
                "drug_name": prescription.regimen_prescribed_raw,
                "cdu_days": cdu_days,
                "effective_repeat_days": cdu_days,
                "prescription_repeat_days": cdu_days,
                "repeat_days": cdu_days,
                "daily_dose": daily_dose,
                "pack_size": pack_size,
                "required_units": required_packs * pack_size,
                "required_quantity": required_packs * pack_size,
                "picked_units": picked_packs * pack_size,
                "picked_quantity": picked_packs * pack_size,
                "packs_to_pick": required_packs,
                "cdu_bottles_required": required_packs,
                "bottles_required": required_packs,
            }
        )
        picking_line = self.env["cdu.picking.line"].create(
            {
                "batch_id": self.batch.id,
                "openmrs_drug_name": prescription.regimen_prescribed_raw,
                "quantity_to_pick": required_packs,
                "quantity_picked": picked_packs,
            }
        )
        fulfilment_line = self.env["cdu.picking.fulfilment.line"].create(
            {
                "batch_id": self.batch.id,
                "picking_line_id": picking_line.id,
                "selected_pack_size": pack_size,
                "quantity_picked": picked_packs,
            }
        )
        return patient_line, picking_line, fulfilment_line

    def test_confirm_prints_labels_and_opens_next_prescription_in_batch(self):
        first_prescription = self._create_prescription("001", 10)
        second_prescription = self._create_prescription("002", 20)
        first_dispense = self._create_ready_dispense(first_prescription)
        second_dispense = self._create_ready_dispense(second_prescription)

        with patch.object(
            type(second_dispense),
            "_auto_refresh_production_stock",
            return_value=False,
        ):
            result = first_dispense.with_user(
                self.admin
            ).action_confirm_dispensing()

        self.assertEqual(
            result["tag"],
            "display_notification",
        )
        self.assertEqual(result["params"]["next"]["res_id"], second_dispense.id)
        self.assertEqual(result["params"]["next"]["views"], [(False, "form")])
        self.assertEqual(result["params"]["next"]["target"], "main")
        self.assertEqual(first_dispense.state, "confirmed")
        self.assertTrue(first_dispense.labels_printed)
        self.assertEqual(first_prescription.state, "awaiting_bagging_qa")
        self.assertEqual(first_prescription.dispensing_status, "fully_dispensed")
        self.assertFalse(first_prescription.dispensing_back_order_short)
        self.assertEqual(second_prescription.state, "awaiting_dispensing")
        print_jobs = self.env["bahmni.print.job"].search(
            [
                ("res_model", "=", "cdu.dispense"),
                ("res_id", "=", first_dispense.id),
            ],
            order="label_type",
        )
        self.assertEqual(
            sorted(print_jobs.mapped("label_type")),
            ["bag_label", "dispensing_slip", "medicine_label"],
        )
        self.assertEqual(set(print_jobs.mapped("state")), {"pending"})
        self.assertEqual(
            print_jobs.filtered(lambda job: job.label_type == "dispensing_slip").printer_key,
            "dispensing_receipt_printer",
        )
        self.assertEqual(
            print_jobs.filtered(lambda job: job.label_type == "medicine_label").printer_key,
            "product_printer",
        )
        self.assertEqual(
            print_jobs.filtered(lambda job: job.label_type == "bag_label").printer_key,
            "bagging_printer",
        )
        slip_job = print_jobs.filtered(lambda job: job.label_type == "dispensing_slip")
        self.assertEqual(slip_job.command_language, "pdf")
        self.assertEqual(slip_job.payload_encoding, "base64")
        self.assertTrue(base64.b64decode(slip_job.payload).startswith(b"%PDF"))
        medicine_job = print_jobs.filtered(lambda job: job.label_type == "medicine_label")
        self.assertEqual(medicine_job.command_language, "tspl")
        self.assertIn("Test Medicine", medicine_job.payload)
        bag_job = print_jobs.filtered(lambda job: job.label_type == "bag_label")
        self.assertEqual(bag_job.command_language, "zpl")
        self.assertIn(first_prescription.name, bag_job.payload)

    def _decode_bag_label_qr(self, payload):
        field = re.search(r"\^FH_\^FDLA,((?:_[0-9A-F]{2})+)\^FS", payload)
        self.assertIsNotNone(field, "Bag label must encode its QR field using ZPL field hex")
        return bytes.fromhex(field.group(1).replace("_", "")).decode("utf-8")

    def test_bag_barcode_reserves_the_parcel_reference_before_qa(self):
        prescription = self._create_prescription("BAG-BARCODE", 10)
        dispense = self._create_ready_dispense(prescription)
        self._confirm_dispense_for_bagging(dispense)

        job = dispense._enqueue_dispensing_print_job("bag_label")
        qa = self.env["cdu.bagging.qa"].search([
            ("prescription_id", "=", prescription.id),
        ])
        self.assertEqual(len(qa), 1)
        self.assertEqual(qa.state, "draft")
        self.assertTrue(qa.parcel_reference)
        parcel_reference = qa.parcel_reference
        barcode = re.search(r"\^BCN,70,Y,N,N\s*\^FD([^\^]+)\^FS", job.payload)
        self.assertIsNotNone(barcode)
        self.assertEqual(barcode.group(1), qa.barcode_reference)
        self.assertNotIn("/", barcode.group(1))
        self.assertEqual(
            barcode.group(1),
            self.env["cdu.collect.go.service"]._collect_go_external_reference(parcel_reference),
        )

        qa.with_user(self.admin).action_validate_label_qr(self._decode_bag_label_qr(job.payload))
        self.assertEqual(qa.state, "confirmed")
        self.assertEqual(qa.parcel_reference, parcel_reference)
        self.assertEqual(dispense.get_bag_barcode_value(), barcode.group(1))

    def test_bag_barcode_reuses_an_existing_parcel_on_reprint(self):
        prescription = self._create_prescription("BAG-REPRINT", 10)
        dispense = self._create_ready_dispense(prescription)
        qa = self.env["cdu.bagging.qa"].create({
            "prescription_id": prescription.id,
            "dispense_id": dispense.id,
            "state": "confirmed",
            "parcel_reference": "CDU/PARCEL/2026/000123",
        })

        self.assertEqual(dispense.get_bag_barcode_value(), "CDUPARCEL2026000123")
        self.assertEqual(dispense.get_bag_barcode_value(), "CDUPARCEL2026000123")
        self.assertEqual(qa.parcel_reference, "CDU/PARCEL/2026/000123")
        self.assertEqual(qa.state, "confirmed")
        self.assertEqual(self.env["cdu.bagging.qa"].search_count([
            ("prescription_id", "=", prescription.id),
        ]), 1)

    def test_bag_pdf_uses_the_same_tracking_barcode_and_qa_qr(self):
        prescription = self._create_prescription("BAG-PDF-BARCODE", 10)
        dispense = self._create_ready_dispense(prescription)
        barcode = dispense.get_bag_barcode_value()
        with patch.object(type(dispense), "get_barcode_data_uri", return_value="data:image/png;base64,") as render_barcode:
            report_html, _ = self.env["ir.actions.report"].with_context(
                cdu_label_layout="bag"
            )._render_qweb_html("cdu_elmis.report_cdu_dispense_labels", dispense.ids)

        self.assertEqual(render_barcode.call_args_list[0].args, (barcode,))
        self.assertEqual(render_barcode.call_args_list[1].args, (dispense.get_label_qr_value(), "QR", 220, 220))
        document = lxml_html.fromstring(report_html)
        caption = document.xpath("//div[@class='cdu-barcode-caption']")[0]
        self.assertEqual(caption.text_content().strip(), barcode)
        self.assertNotIn("/", barcode)

    def test_bag_label_qr_passes_qa_with_separate_patient_and_prescription(self):
        prescription = self._create_prescription("QR-LINES", 10)
        dispense = self._create_ready_dispense(prescription)
        self._confirm_dispense_for_bagging(dispense)
        qa = self.env["cdu.bagging.qa"].create({
            "prescription_id": prescription.id,
            "dispense_id": dispense.id,
        })

        job = dispense._enqueue_dispensing_print_job("bag_label")
        scanned_value = self._decode_bag_label_qr(job.payload)

        self.assertEqual(
            scanned_value.splitlines(),
            [dispense.patient_name, prescription.name],
        )
        result = qa.with_user(self.admin).action_validate_label_qr(scanned_value)

        self.assertEqual(result["params"]["title"], "Bag passed QA")
        self.assertEqual(qa.state, "confirmed")
        self.assertEqual(prescription.state, "awaiting_boxing")

    def test_bag_label_qr_preserves_special_characters_in_patient_name(self):
        prescription = self._create_prescription("QR-SPECIAL", 10)
        prescription.patient_first_name = 'Mpho_0A Mokoéna "Neo" ^QA~'
        dispense = self._create_ready_dispense(prescription)
        template = self.env.ref("cdu_elmis.template_bag_label_zpl")

        # Both existing saved templates and templates with a hex indicator work.
        for prefix in ("", "^FH_", "^FH\\"):
            with self.subTest(prefix=prefix):
                template.template_body = (
                    "^XA\n^BQN,2,4\n" + prefix + "^FDLA,{qr_value}^FS\n^XZ"
                )
                payload = template.render_command_payload(dispense._get_print_template_values())
                scanned_value = self._decode_bag_label_qr(payload)

                self.assertEqual(scanned_value, dispense.get_label_qr_value())
                self.assertEqual(scanned_value.splitlines()[0], prescription.patient_first_name)
                self.assertNotIn(prescription.patient_first_name, payload)

    def test_bag_label_qr_still_rejects_another_patients_label(self):
        prescription = self._create_prescription("QR-EXPECTED", 10)
        dispense = self._create_ready_dispense(prescription)
        self._confirm_dispense_for_bagging(dispense)
        qa = self.env["cdu.bagging.qa"].create({
            "prescription_id": prescription.id,
            "dispense_id": dispense.id,
        })
        other_prescription = self._create_prescription("QR-WRONG", 10)
        other_dispense = self._create_ready_dispense(other_prescription)
        job = other_dispense._enqueue_dispensing_print_job("bag_label")

        with self.assertRaisesRegex(UserError, "Wrong box scanned"):
            qa.with_user(self.admin).action_validate_label_qr(
                self._decode_bag_label_qr(job.payload)
            )

        self.assertEqual(qa.state, "draft")
        self.assertFalse(qa.patient_details_checked)
        self.assertEqual(prescription.state, "awaiting_bagging_qa")

    def test_print_agent_api_can_process_jobs_without_printers(self):
        prescription = self._create_prescription("AGENT", 10)
        dispense = self._create_ready_dispense(prescription)
        dispense.with_user(self.admin).action_confirm_dispensing()

        PrintJob = self.env["bahmni.print.job"]
        pending_jobs = PrintJob.api_get_pending_jobs(limit=2)

        self.assertEqual(len(pending_jobs), 2)
        self.assertEqual(pending_jobs[0]["state"], "pending")
        self.assertTrue(pending_jobs[0]["payload"])

        self.assertTrue(
            PrintJob.api_mark_done(pending_jobs[0]["id"], agent_name="test-agent")
        )
        self.assertTrue(
            PrintJob.api_mark_failed(
                pending_jobs[1]["id"],
                "Simulated printer offline",
                agent_name="test-agent",
            )
        )

        done_job = PrintJob.browse(pending_jobs[0]["id"])
        failed_job = PrintJob.browse(pending_jobs[1]["id"])
        self.assertEqual(done_job.state, "done")
        self.assertEqual(done_job.agent_name, "test-agent")
        self.assertTrue(done_job.completed_at)
        self.assertEqual(failed_job.state, "failed")
        self.assertEqual(failed_job.error_message, "Simulated printer offline")
        self.assertTrue(failed_job.failed_at)

    def test_cancel_draft_dispensing_job_returns_to_work_queue(self):
        prescription = self._create_prescription("CANCEL", 10)
        dispense = self._create_ready_dispense(prescription)

        result = dispense.with_user(self.admin).action_cancel_dispensing()
        dispense.invalidate_recordset()
        prescription.invalidate_recordset()

        self.assertEqual(dispense.state, "cancelled")
        self.assertEqual(dispense.cancelled_by, self.admin)
        self.assertTrue(dispense.cancelled_at)
        self.assertEqual(prescription.state, "awaiting_dispensing")
        self.assertEqual(prescription.dispensing_status, "cancelled")
        self.assertEqual(result["tag"], "display_notification")
        self.assertEqual(
            result["params"]["next"]["id"],
            self.env.ref("cdu_elmis.action_cdu_dispensing_work_queue").id,
        )
        self.assertEqual(result["params"]["next"]["target"], "main")

        with self.assertRaises(UserError):
            dispense.with_user(self.admin).action_confirm_dispensing()

    def test_start_dispensing_reopens_cancelled_job(self):
        prescription = self._create_prescription("RESTART-CANCELLED", 10)
        dispense = self._create_ready_dispense(prescription)
        old_selection_ids = set(dispense.stock_selection_ids.ids)
        dispense.with_user(self.admin).action_cancel_dispensing()

        with patch.object(
            type(dispense),
            "_auto_refresh_production_stock",
            return_value=False,
        ):
            action = prescription.with_user(self.admin).action_open_dispensing()
        dispense.invalidate_recordset()
        prescription.invalidate_recordset()

        self.assertEqual(action["res_id"], dispense.id)
        self.assertEqual(action["target"], "main")
        self.assertEqual(dispense.state, "draft")
        self.assertFalse(dispense.cancelled_by)
        self.assertFalse(dispense.cancelled_at)
        self.assertTrue(dispense.stock_selection_ids)
        self.assertFalse(old_selection_ids.intersection(dispense.stock_selection_ids.ids))
        self.assertEqual(prescription.dispensing_status, "not_dispensed")

    def test_confirm_dispensing_marks_partial_with_back_order_details(self):
        prescription = self._create_prescription("PARTIAL", 10)
        self._create_patient_picking_context(
            prescription,
            required_packs=2,
            picked_packs=2,
            pack_size=30,
            daily_dose=1,
            cdu_days=60,
        )
        dispense = self._create_ready_dispense(prescription)
        dispense.stock_selection_ids.quantity_dispensed = 1

        dispense.with_user(self.admin).action_confirm_dispensing()
        prescription.invalidate_recordset()

        self.assertEqual(prescription.dispensing_status, "partially_dispensed")
        self.assertEqual(prescription.dispensing_back_order_packs, 1)
        self.assertEqual(prescription.dispensing_back_order_units, 30)
        self.assertEqual(prescription.dispensing_back_order_days, 30)
        self.assertEqual(
            prescription.dispensing_back_order_short,
            "1 pack, 30 units, 30 days",
        )
        self.assertIn(
            "TEST-DISPENSING-REGIMEN: 1 pack, 30 units, 30 days",
            prescription.dispensing_back_order_summary,
        )

    def test_medicine_label_count_matches_bottles_dispensed_per_product(self):
        prescription = self._create_prescription("LABEL-COPIES", 15)
        dispense = self._create_ready_dispense(prescription)
        dispense.stock_selection_ids.quantity_dispensed = 2
        second_option = self.env["cdu.dispense.stock.option"].create(
            {
                "dispense_id": dispense.id,
                "facility_code": "TEST-CDU",
                "program_code": "TEST-PROGRAM",
                "orderable_code": "SECOND-ORDERABLE",
                "orderable_name": "Second Test Medicine",
                "pack_size": 90,
                "lot": "SECOND-LOT",
                "stock_on_hand": 20,
            }
        )
        self.env["cdu.dispense.stock.selection"].create(
            {
                "dispense_id": dispense.id,
                "openmrs_drug_name": "Second Test Medicine",
                "stock_option_id": second_option.id,
                "quantity_dispensed": 3,
                "dosage_instructions": "Take two tablets daily.",
            }
        )

        html, _report_type = self.env["ir.actions.report"].with_context(
            cdu_label_layout="medicine"
        )._render_qweb_html(
            "cdu_elmis.report_cdu_dispense_labels",
            dispense.ids,
        )
        document = lxml_html.fromstring(html)
        labels = document.xpath(
            "//div[contains(concat(' ', normalize-space(@class), ' '), "
            "' cdu-medicine-label ')]"
        )
        drug_names = [
            " ".join(
                label.xpath(
                    ".//div[contains(concat(' ', normalize-space(@class), ' '), "
                    "' cdu-drug ')]//text()"
                )
            ).strip()
            for label in labels
        ]

        self.assertEqual(len(labels), 5)
        self.assertEqual(drug_names.count("Test Medicine"), 2)
        self.assertEqual(drug_names.count("Second Test Medicine"), 3)

        bag_html, _report_type = self.env["ir.actions.report"].with_context(
            cdu_label_layout="bag"
        )._render_qweb_html(
            "cdu_elmis.report_cdu_dispense_labels",
            dispense.ids,
        )
        bag_document = lxml_html.fromstring(bag_html)
        produced_by = bag_document.xpath(
            "//div[contains(concat(' ', normalize-space(@class), ' '), "
            "' cdu-bag-section ')][contains(., 'Produced By:')]"
        )
        self.assertEqual(
            " ".join(produced_by[0].text_content().split()),
            "Produced By:Bonolo Health",
        )

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
        self.assertEqual(action["target"], "main")

    def test_bagging_confirmation_opens_next_task_with_clean_target(self):
        first_prescription = self._create_prescription("BAG-001", 10)
        second_prescription = self._create_prescription("BAG-002", 20)
        first_dispense = self._create_ready_dispense(first_prescription)
        second_dispense = self._create_ready_dispense(second_prescription)
        self._confirm_dispense_for_bagging(first_dispense)
        self._confirm_dispense_for_bagging(second_dispense)
        first_qa = self._create_ready_bagging_qa(first_prescription, first_dispense)
        second_qa = self._create_ready_bagging_qa(second_prescription, second_dispense)

        result = first_qa.with_user(self.admin).action_confirm_bagging_qa()

        self.assertEqual(result["params"]["next"]["res_id"], second_qa.id)
        self.assertEqual(result["params"]["next"]["res_model"], "cdu.bagging.qa")
        self.assertEqual(result["params"]["next"]["target"], "main")

    def test_exhausted_bagging_returns_to_work_queue_with_clean_target(self):
        prescription = self._create_prescription("BAG-QUEUE", 30)
        dispense = self._create_ready_dispense(prescription)
        self._confirm_dispense_for_bagging(dispense)
        qa = self._create_ready_bagging_qa(prescription, dispense)

        result = qa.with_user(self.admin).action_confirm_bagging_qa()

        expected_action = self.env.ref("cdu_elmis.action_cdu_bagging_qa_work_queue")
        self.assertEqual(result["params"]["next"]["id"], expected_action.id)
        self.assertEqual(result["params"]["next"]["res_model"], "cdu.prescription")
        self.assertEqual(result["params"]["next"]["views"], [(False, "tree"), (False, "form")])
        self.assertEqual(result["params"]["next"]["target"], "main")

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
        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(action["params"]["type"], "success")

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
            "orderable_name": "Tenofovir/Lamivudine/Dolutegravir 300/300/50Mg Tablets 30",
            "pack_size": 30,
            "lot": "TLD-FEFO-LOT",
            "stock_on_hand": 20,
            "expiration_date": self.today + timedelta(days=365),
        }
        self.env["product.template"]._sync_cdu_elmis_product_catalog(
            [option_values],
            facility_code="TEST-CDU-STORE",
            program_code="TEST-PROGRAM",
            complete=True,
        )
        option = self.env["cdu.elmis.stock.option"].create(option_values)
        product = self.env["product.product"].search(
            [
                (
                    "product_tmpl_id.cdu_elmis_orderable_catalog_ids.orderable_id",
                    "=",
                    "ELMIS-TLD-UUID",
                )
            ],
            limit=1,
        )
        self.assertTrue(product)
        self.assertTrue(product.product_tmpl_id.cdu_is_drug)
        self.assertEqual(product.product_tmpl_id.cdu_pack_size, 0)
        self.assertEqual(
            product.with_context(cdu_generic_catalog_label=True).display_name,
            "Tenofovir/Lamivudine/Dolutegravir 300/300/50Mg",
        )

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

    def test_catalog_merges_pack_variants_into_one_generic_medicine(self):
        templates = self.env["product.template"]._sync_cdu_elmis_product_catalog(
            [
                {
                    "orderable_id": "ABC-30",
                    "orderable_code": "ABC30",
                    "orderable_name": "Abacavir/Lamivudine 600/300Mg Tablets 30",
                    "pack_size": 30,
                },
                {
                    "orderable_id": "ABC-60",
                    "orderable_code": "ABC60",
                    "orderable_name": "Abacavir/Lamivudine 600/300Mg Tablets 60",
                    "pack_size": 60,
                },
            ],
            facility_code="TEST-CDU-STORE",
            program_code="TEST-PROGRAM",
            complete=True,
        )

        self.assertEqual(len(templates), 1)
        self.assertEqual(templates.cdu_generic_name, "Abacavir/Lamivudine")
        self.assertEqual(templates.cdu_strength, "600/300Mg")
        self.assertEqual(templates.cdu_verification_label, "Abacavir/Lamivudine 600/300Mg")
        self.assertEqual(len(templates.cdu_elmis_orderable_catalog_ids), 2)
        self.assertSetEqual(
            set(templates.cdu_elmis_orderable_catalog_ids.mapped("pack_size")),
            {30, 60},
        )

    def test_catalog_sync_is_idempotent_and_retires_missing_mappings(self):
        values = [
            {
                "orderable_id": "RETAINED",
                "orderable_name": "Stable Medicine 10Mg Tablets 30",
                "pack_size": 30,
            },
            {
                "orderable_id": "RETIRED",
                "orderable_name": "Retired Medicine 20Mg Tablets 30",
                "pack_size": 30,
            },
        ]
        Product = self.env["product.template"]
        Product._sync_cdu_elmis_product_catalog(
            values,
            facility_code="TEST-CDU-STORE",
            program_code="TEST-PROGRAM",
            complete=True,
        )
        Product._sync_cdu_elmis_product_catalog(
            values[:1],
            facility_code="TEST-CDU-STORE",
            program_code="TEST-PROGRAM",
            complete=True,
        )
        mappings = self.env["cdu.elmis.orderable.catalog"].with_context(
            active_test=False
        ).search(
            [
                ("facility_code", "=", "TEST-CDU-STORE"),
                ("program_code", "=", "TEST-PROGRAM"),
                ("orderable_id", "in", ["RETAINED", "RETIRED"]),
            ]
        )

        self.assertEqual(len(mappings), 2)
        self.assertTrue(mappings.filtered(lambda mapping: mapping.orderable_id == "RETAINED").active)
        self.assertFalse(mappings.filtered(lambda mapping: mapping.orderable_id == "RETIRED").active)

    def test_orderable_reference_lookup_is_paginated(self):
        service = self.env["cdu.elmis.stock.service"]
        pages = [
            {"content": [{"id": "PAGE-1"}], "last": False, "totalPages": 2},
            {"content": [{"id": "PAGE-2"}], "last": True, "totalPages": 2},
        ]
        with patch.object(
            type(service), "_get_reference_payload", side_effect=pages
        ) as request:
            records = service._get_all_reference_records(
                "api/orderables", {"size": 1}
            )

        self.assertEqual([record["id"] for record in records], ["PAGE-1", "PAGE-2"])
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[0].args[1]["page"], 0)
        self.assertEqual(request.call_args_list[1].args[1]["page"], 1)

    def test_catalog_refresh_includes_orderables_without_stock(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("cdu.elmis.cdu_store_facility_code", "TEST-CDU-STORE")
        params.set_param("cdu.elmis.default_program_code", "TEST-PROGRAM")
        service = self.env["cdu.elmis.stock.service"]
        summaries = [
            {"orderable": {"id": "WITH-STOCK"}, "stockOnHand": 100},
            {"orderable": {"id": "ZERO-STOCK"}, "stockOnHand": 0},
        ]
        reference = [
            {
                "id": "WITH-STOCK",
                "productCode": "WITH",
                "fullProductName": "Medicine A 10Mg Tablets 30",
                "netContent": 30,
            },
            {
                "id": "ZERO-STOCK",
                "productCode": "ZERO",
                "fullProductName": "Medicine B 20Mg Tablets 60",
                "netContent": 60,
            },
        ]

        with patch.object(
            type(service), "get_stock_card_summaries", return_value=summaries
        ) as stock_query, patch.object(
            type(service), "_get_all_reference_records", return_value=reference
        ):
            products = service.refresh_product_catalog()

        self.assertEqual(len(products), 2)
        self.assertTrue(stock_query.call_args.kwargs["non_empty_only"] is False)
        self.assertSetEqual(
            set(products.mapped("cdu_verification_label")),
            {"Medicine A 10Mg", "Medicine B 20Mg"},
        )

    def test_picking_stock_parser_excludes_zero_and_expired_lots(self):
        payload = {
            "content": [
                {
                    "canFulfillForMe": [
                        {
                            "orderable": {"id": "VALID"},
                            "orderableName": "Valid 10Mg Tablets 30",
                            "packSize": 30,
                            "stockOnHand": 60,
                            "lotCode": "VALID-LOT",
                            "lotExpirationDate": fields.Date.to_string(
                                self.today + timedelta(days=30)
                            ),
                        },
                        {
                            "orderable": {"id": "ZERO"},
                            "orderableName": "Zero 10Mg Tablets 30",
                            "packSize": 30,
                            "stockOnHand": 0,
                            "lotCode": "ZERO-LOT",
                        },
                        {
                            "orderable": {"id": "EXPIRED"},
                            "orderableName": "Expired 10Mg Tablets 30",
                            "packSize": 30,
                            "stockOnHand": 60,
                            "lotCode": "EXPIRED-LOT",
                            "lotExpirationDate": fields.Date.to_string(
                                self.today - timedelta(days=1)
                            ),
                        },
                    ]
                }
            ]
        }

        values = self.batch._stock_options_from_payload(
            payload, "TEST-CDU-STORE", "TEST-PROGRAM"
        )

        self.assertEqual(len(values), 1)
        self.assertEqual(values[0]["orderable_id"], "VALID")

    def test_generic_quantity_is_converted_to_packs_only_at_picking(self):
        medicine = self.env["product.template"]._sync_cdu_elmis_product_catalog(
            [
                {
                    "orderable_id": "PACK-30",
                    "orderable_code": "PACK30",
                    "orderable_name": "Deferred Medicine 10Mg Tablets 30",
                    "pack_size": 30,
                },
                {
                    "orderable_id": "PACK-60",
                    "orderable_code": "PACK60",
                    "orderable_name": "Deferred Medicine 10Mg Tablets 60",
                    "pack_size": 60,
                },
            ],
            facility_code="TEST-CDU-STORE",
            program_code="TEST-PROGRAM",
            complete=True,
        )
        prescription = self._create_prescription("DEFERRED-PACK", 30)
        prescription.write(
            {
                "cdu_days_supply": 30,
                "repeat_days": 30,
                "total_days_supply": 30,
            }
        )
        prescription.product_line_ids.with_context(
            cdu_allow_product_line_sync=True
        ).write(
            {
                "product_id": medicine.product_variant_id.id,
                "dosage_instructions": "Take one tablet daily.",
            }
        )

        self.batch._generate_elmis_picking_lines()
        summary = self.batch.picking_line_ids.filtered(
            lambda line: line.product_id == medicine.product_variant_id
        )
        self.assertEqual(summary.total_tablets, 30)
        self.assertEqual(summary.pack_size, 0)
        self.assertEqual(summary.total_bottles, 0)

        selected_option = self.env["cdu.elmis.stock.option"].create(
            {
                "batch_id": self.batch.id,
                "facility_code": "TEST-CDU-STORE",
                "program_code": "TEST-PROGRAM",
                "orderable_code": "PACK60",
                "orderable_id": "PACK-60",
                "orderable_name": "Deferred Medicine 10Mg Tablets 60",
                "pack_size": 60,
                "lot": "EARLIEST-USABLE",
                "stock_on_hand": 10,
                "stock_on_hand_units": 600,
                "expiration_date": self.today + timedelta(days=30),
            }
        )
        self.batch._apply_prescription_product_stock_options()

        fulfilment = self.batch.elmis_picking_line_ids.fulfilment_line_ids
        self.assertEqual(fulfilment.selected_stock_option_id, selected_option)
        self.assertEqual(fulfilment.selected_pack_size, 60)
        self.assertEqual(fulfilment.picking_line_id.quantity_to_pick, 1)

    def _create_patient_rounded_picking(self, stock_on_hand=20):
        medicine = self.env["product.template"].create(
            {
                "name": "Patient Rounded Medicine",
                "type": "product",
                "cdu_is_drug": True,
                "cdu_catalog_source": "elmis",
                "cdu_drug_code": "ROUND-PACK",
                "cdu_default_daily_dose": 1,
            }
        )
        for days in (35, 40):
            prescription = self._create_prescription("ROUND-%s" % days, days)
            prescription.write(
                {"cdu_days_supply": days, "repeat_days": days, "total_days_supply": days}
            )
            prescription.product_line_ids.with_context(
                cdu_allow_product_line_sync=True
            ).write({"product_id": medicine.product_variant_id.id})
        self.batch._generate_elmis_picking_lines()
        option = self.env["cdu.elmis.stock.option"].create(
            {
                "batch_id": self.batch.id,
                "facility_code": "TEST-CDU-STORE",
                "program_code": "TEST-PROGRAM",
                "orderable_code": "ROUND-PACK",
                "orderable_name": "Patient Rounded Medicine 30 Tablets",
                "pack_size": 30,
                "lot": "ROUND-LOT",
                "stock_on_hand": stock_on_hand,
                "stock_on_hand_units": stock_on_hand * 30,
            }
        )
        self.batch._apply_prescription_product_stock_options()
        picking = self.batch.elmis_picking_line_ids
        fulfilment = picking.fulfilment_line_ids
        self.assertEqual(fulfilment.selected_stock_option_id, option)
        return picking, fulfilment

    def test_picking_rounds_packs_per_patient_in_defaults_report_and_allocation(self):
        picking, fulfilment = self._create_patient_rounded_picking()

        self.assertEqual(picking.required_units, 75)
        self.assertEqual(picking.quantity_to_pick, 4)
        self.assertEqual(picking.packs_to_pick, 4)
        self.assertEqual(fulfilment.required_quantity, 4)
        self.assertEqual(fulfilment.quantity_picked, 4)
        self.assertEqual(picking.remaining_packs_to_pick, 0)
        self.assertEqual(picking.summary_line_id.total_bottles, 4)
        self.assertEqual(self.batch.patient_picking_line_ids.mapped("packs_to_pick"), [2, 2])
        self.assertFalse(self.batch._get_picking_readiness_errors())

        report_html, _ = self.env["ir.actions.report"]._render_qweb_html(
            "cdu_elmis.report_cdu_elmis_picking_list", self.batch.ids
        )
        document = lxml_html.fromstring(report_html)
        stock_rows = document.xpath("//table[contains(@class, 'cdu-picking-table')][1]/tbody/tr")
        self.assertEqual(stock_rows[0].xpath("string(td[8])").strip(), "4")

        params = self.env["ir.config_parameter"].sudo()
        for key, value in {
            "cdu.elmis.cdu_store_facility_code": "TEST-CDU-STORE",
            "cdu.elmis.cdu_production_floor_facility_code": "TEST-CDU-PRODUCTION",
            "cdu.elmis.default_program_code": "TEST-PROGRAM",
            "cdu.elmis.picking_debit_reason_name": "Test debit",
            "cdu.elmis.picking_credit_reason_name": "Test credit",
        }.items():
            params.set_param(key, value)
        service_class = type(self.env["cdu.elmis.stock.service"])
        with patch.object(service_class, "has_valid_current_user_elmis_token", return_value=True), \
                patch.object(service_class, "post_internal_stock_event") as post_event, \
                patch.object(service_class, "invalidate_stock_cache"):
            self.batch.with_user(self.admin).action_confirm_elmis_picking()
        self.assertTrue(self.batch.picking_confirmed_at)
        self.assertEqual(post_event.call_count, 2)
        for call in post_event.call_args_list:
            self.assertEqual(call.kwargs["items"][0]["quantity"], 120)
        self.assertEqual(self.batch.patient_picking_line_ids.mapped("picked_units"), [60, 60])
        self.assertEqual(self.batch.patient_picking_line_ids.mapped("back_order_days"), [0, 0])

    def test_picking_recalculates_each_patient_when_pack_size_changes(self):
        picking, fulfilment = self._create_patient_rounded_picking()
        first_option = fulfilment.selected_stock_option_id
        larger_option = first_option.copy(
            {"pack_size": 90, "stock_on_hand_units": 1800, "lot": "LARGE-PACK"}
        )
        for option, expected_packs in ((larger_option, 2), (first_option, 4)):
            with self.subTest(pack_size=option.pack_size):
                fulfilment.selected_stock_option_id = option
                self.assertEqual(picking.packs_to_pick, expected_packs)
                self.assertEqual(picking.quantity_to_pick, expected_packs)
                self.assertEqual(fulfilment.quantity_picked, expected_packs)
                self.assertEqual(picking.summary_line_id.total_bottles, expected_packs)
                self.assertEqual(
                    sum(self.batch.patient_picking_line_ids.mapped("packs_to_pick")),
                    expected_packs,
                )
                self.assertFalse(self.batch._get_picking_readiness_errors())

    def test_patient_rounded_picking_can_be_split_between_lots(self):
        picking, first = self._create_patient_rounded_picking()
        other_option = first.selected_stock_option_id.copy({"lot": "SECOND-LOT"})
        second = self.env["cdu.picking.fulfilment.line"].create(
            {
                "batch_id": self.batch.id,
                "picking_line_id": picking.id,
                "selected_stock_option_id": other_option.id,
            }
        )
        (first | second).write({"quantity_picked": 2})
        self.assertEqual(picking.packs_to_pick, 4)
        self.assertEqual(picking.total_quantity_picked, 4)
        self.assertEqual(picking.remaining_packs_to_pick, 0)
        self.assertFalse(self.batch._get_picking_readiness_errors())

    def test_picking_wizard_updates_patient_total_before_saving_pack_choice(self):
        picking, fulfilment = self._create_patient_rounded_picking()
        larger_option = fulfilment.selected_stock_option_id.copy(
            {"pack_size": 90, "stock_on_hand_units": 1800, "lot": "LARGE-PACK"}
        )
        wizard = self.env["cdu.elmis.picking.wizard"].create({"batch_id": self.batch.id})
        with Form(wizard, view="cdu_elmis.view_cdu_elmis_picking_wizard_form") as form:
            with form.elmis_picking_fulfilment_line_ids.edit(0) as row:
                row.selected_stock_option_id = larger_option
                self.assertEqual(row.quantity_picked, 2)
                self.assertEqual(row.required_quantity, 2)
        self.assertEqual(picking.quantity_to_pick, 2)

    def test_patient_rounded_picking_still_requires_enough_stock(self):
        picking, fulfilment = self._create_patient_rounded_picking(stock_on_hand=3)
        self.assertEqual(picking.packs_to_pick, 4)
        self.assertEqual(fulfilment.quantity_picked, 3)
        self.assertTrue(
            any("short by 1.00" in error for error in self.batch._get_picking_readiness_errors())
        )
        with self.assertRaisesRegex(ValidationError, "exceed available packs"), self.cr.savepoint():
            fulfilment.quantity_picked = 4

    def test_picking_generation_links_same_named_products_by_summary_id(self):
        product_name = "Same Named Multi-line Medicine"
        first_template = self.env["product.template"].create(
            {
                "name": product_name,
                "type": "product",
                "cdu_is_drug": True,
                "cdu_pack_size": 30,
                "cdu_default_daily_dose": 1,
                "cdu_drug_code": "SAME-NAME-ONE",
            }
        )
        second_template = self.env["product.template"].create(
            {
                "name": product_name,
                "type": "product",
                "cdu_is_drug": True,
                "cdu_pack_size": 60,
                "cdu_default_daily_dose": 2,
                "cdu_drug_code": "SAME-NAME-TWO",
            }
        )
        prescription = self._create_prescription("SAME-NAME-PRODUCTS", 30)
        prescription.write(
            {
                "cdu_days_supply": 30,
                "repeat_days": 30,
                "total_days_supply": 30,
            }
        )
        product_lines = prescription.product_line_ids.with_context(
            cdu_allow_product_line_sync=True
        )
        product_lines.write({"product_id": first_template.product_variant_id.id})
        self.env["cdu.prescription.product.line"].with_context(
            cdu_allow_product_line_sync=True
        ).create(
            {
                "prescription_id": prescription.id,
                "sequence": 20,
                "product_id": second_template.product_variant_id.id,
                "source": "manual",
            }
        )

        self.batch._generate_elmis_picking_lines()

        self.assertEqual(len(self.batch.picking_line_ids), 2)
        self.assertEqual(len(self.batch.elmis_picking_line_ids), 2)
        self.assertSetEqual(
            set(self.batch.elmis_picking_line_ids.mapped("summary_line_id").ids),
            set(self.batch.picking_line_ids.ids),
        )
        self.assertSetEqual(
            set(
                self.batch.elmis_picking_line_ids.mapped(
                    "summary_line_id.product_id"
                ).ids
            ),
            {
                first_template.product_variant_id.id,
                second_template.product_variant_id.id,
            },
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
        self.assertTrue(arch.xpath(".//button[@name='action_cancel_dispensing']"))
        self.assertIn(
            "cancelled",
            arch.xpath(".//field[@name='state']")[0].get("statusbar_visible"),
        )
        self.assertIn(
            'name="cancelled"',
            self.env.ref("cdu_elmis.view_cdu_dispense_search").arch_db,
        )

        prescription_tree = self.env["cdu.prescription"].with_user(self.admin).get_view(
            view_id=self.env.ref("cdu_prescription.view_cdu_prescription_tree").id,
            view_type="tree",
        )
        prescription_tree_arch = etree.fromstring(
            prescription_tree["arch"].encode()
        )
        dispensing_status_field = prescription_tree_arch.xpath(
            ".//field[@name='dispensing_status']"
        )[0]
        self.assertEqual(dispensing_status_field.get("widget"), "badge")
        self.assertIn(
            "partially_dispensed",
            dispensing_status_field.get("decoration-warning"),
        )
        self.assertTrue(
            prescription_tree_arch.xpath(
                ".//field[@name='dispensing_back_order_short']"
            )
        )

        prescription_form = self.env["cdu.prescription"].with_user(self.admin).get_view(
            view_id=self.env.ref("cdu_prescription.view_cdu_prescription_form").id,
            view_type="form",
        )
        prescription_form_arch = etree.fromstring(prescription_form["arch"].encode())
        self.assertTrue(
            prescription_form_arch.xpath(
                ".//field[@name='dispensing_back_order_summary']"
            )
        )

        prescription_search = self.env["cdu.prescription"].with_user(
            self.admin
        ).get_view(
            view_id=self.env.ref("cdu_prescription.view_cdu_prescription_search").id,
            view_type="search",
        )
        prescription_search_arch = etree.fromstring(
            prescription_search["arch"].encode()
        )
        self.assertTrue(
            prescription_search_arch.xpath(
                ".//filter[@name='dispensing_partially_dispensed']"
            )
        )
        self.assertTrue(
            prescription_search_arch.xpath(
                ".//filter[@name='group_dispensing_status']"
            )
        )
