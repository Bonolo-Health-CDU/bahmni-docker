import base64
import csv
from datetime import date
from io import BytesIO, StringIO

from openpyxl import Workbook

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "cdu_report_import")
class TestCduReportImport(TransactionCase):
    HEADERS = (
        "Patient Name",
        "eRegister ID",
        "Prescription Date",
        "Regimen Prescribed",
        "Next Drug Pickup Date",
        "Drug Pickup Point",
        "Location",
        "Primary Contact",
        "Dosage Instuctions",
        "Qty to order",
    )

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.data_clerk = cls.env.ref("cdu_prescription.user_cdu_data_clerk")
        cls.point = cls.env["cdu.collection.point"].create({
            "name": "Report Test Pickup",
            "code": "REPORT-TEST-PICKUP",
            "external_reference": "report-test-pickup",
        })

    def _row(self, identifier, pickup_date="18/11/2026", pickup_point=None):
        return (
            "Synthetic Patient",
            identifier,
            date(2026, 10, 5),
            "TEST-REGIMEN",
            pickup_date,
            pickup_point if pickup_point is not None else self.point.name,
            "Report Test Facility",
            "00123456",
            "Take one tablet daily.",
            999,
        )

    def _xlsx(self, *rows, headers=None):
        workbook = Workbook()
        workbook.active.title = "Notes"
        sheet = workbook.create_sheet("READY")
        sheet.append([])
        for title in ("Prescription report", "October 2026", "Report Generated On: 2026-10-01 10:00:00"):
            sheet.append([title])
            sheet.merge_cells(start_row=sheet.max_row, start_column=1, end_row=sheet.max_row, end_column=10)
        sheet.append([])
        sheet.append(headers if headers is not None else self.HEADERS)
        for row in rows:
            sheet.append(row)
        if rows:
            sheet["B7"].number_format = "000000"
        output = BytesIO()
        workbook.save(output)
        workbook.close()
        return output.getvalue()

    def _csv(self, *rows):
        output = StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(self.HEADERS)
        writer.writerows(rows)
        return output.getvalue().encode()

    def _import(self, filename, contents):
        run = self.env["cdu.report.run"].with_user(self.data_clerk).create({
            "source_file_name": filename,
            "source_file": base64.b64encode(contents),
        })
        run.action_import_file()
        return run

    def test_import_excel_template(self):
        run = self._import("report.XLSX", self._xlsx(self._row(123)))
        self.assertEqual(run.state, "completed", run.error_message)
        self.assertEqual(run.report_title, "Prescription report")
        self.assertEqual(run.report_period, "October 2026")
        self.assertEqual(run.row_ids.row_number, 7)
        prescription = run.row_ids.prescription_id
        self.assertEqual(prescription.patient_identifier, "000123")
        self.assertEqual(prescription.patient_phone, "00123456")
        self.assertEqual(prescription.prescription_date, date(2026, 10, 5))
        self.assertEqual(prescription.next_drug_pickup_date, date(2026, 11, 18))
        self.assertEqual(prescription.dosage_instructions, "Take one tablet daily.")
        self.assertNotIn("Qty to order", run.row_ids.raw_row_json)

    def test_legacy_csv_and_excel_match_existing_prescription(self):
        excel_row = self._row("TEST-LEGACY")
        csv_row = (*excel_row[:2], "05-Oct-2026", *excel_row[3:])
        csv_run = self._import("report.csv", self._csv(csv_row))
        self.assertEqual(csv_run.state, "completed", csv_run.error_message)
        source_row = csv_run.row_ids
        prescription = source_row.prescription_id
        legacy_key = source_row._prescription_source_key({
            "location": csv_row[6],
            "eregister_id": csv_row[1],
            "regimen_prescribed": csv_row[3],
            "drug_pickup_point": csv_row[5],
        }, csv_row[2])
        prescription.sudo().source_key = legacy_key

        excel_run = self._import("report.xlsx", self._xlsx(excel_row))
        self.assertEqual(excel_run.state, "completed", excel_run.error_message)
        self.assertEqual(excel_run.duplicate_rows, 1)
        self.assertEqual(excel_run.row_ids.prescription_id, prescription)
        self.assertEqual(prescription.source_key, legacy_key)

    def test_row_errors_include_source_rows_and_preserve_successful_rows(self):
        run = self._import("row-errors.xlsx", self._xlsx(
            self._row("TEST-GOOD"),
            (),
            self._row(None, pickup_date="31/11/2026"),
            self._row("TEST-BAD-PICKUP", pickup_point="Unknown Report Pickup"),
        ))
        self.assertEqual(run.state, "completed_with_errors", run.error_message)
        self.assertIn("2 of 3 rows", run.error_message)
        self.assertEqual(run.imported_rows, 1)
        rows = {row.row_number: row for row in run.row_ids}
        self.assertEqual(set(rows), {7, 9, 10})
        self.assertIn("Missing required values: eRegister ID", rows[9].error_message)
        self.assertIn("Next Drug Pickup Date is not a valid date", rows[9].error_message)
        self.assertIn("Unknown Report Pickup", rows[10].error_message)
        self.assertFalse(self.env["res.partner"].search([("cdu_eregister_id", "=", "TEST-BAD-PICKUP")]))

    def test_missing_column_and_missing_row_value_have_distinct_reasons(self):
        headers = list(self.HEADERS)
        headers[1] = "Unrecognized ID"
        missing_column = self._import("missing-column.xlsx", self._xlsx(self._row("TEST-MISSING"), headers=headers))
        self.assertEqual(missing_column.state, "failed")
        self.assertIn("header row 6: eRegister ID", missing_column.error_message)
        self.assertFalse(missing_column.row_ids)

        missing_value = self._import("missing-value.xlsx", self._xlsx(self._row(None)))
        self.assertEqual(missing_value.state, "failed")
        self.assertIn("1 of 1 rows", missing_value.error_message)
        self.assertIn("Missing required values: eRegister ID", missing_value.row_ids.error_message)

    def test_excel_error_cell_explains_which_value_to_correct(self):
        run = self._import("cell-error.xlsx", self._xlsx(self._row("#REF!")))
        self.assertEqual(run.state, "failed")
        self.assertEqual(run.row_ids.row_number, 7)
        self.assertIn("eRegister ID contains an Excel error (#REF!)", run.row_ids.error_message)

    def test_bulk_reports_each_file_with_its_own_reason(self):
        attachments = self.env["ir.attachment"].with_user(self.data_clerk).create([
            {"name": "report.csv", "datas": base64.b64encode(self._csv(self._row("TEST-BULK-CSV")))},
            {"name": "report.xlsx", "datas": base64.b64encode(self._xlsx(self._row("TEST-BULK-XLSX")))},
            {"name": "broken.xlsx", "datas": base64.b64encode(b"not a workbook")},
            {"name": "old-report.xls", "datas": base64.b64encode(b"unsupported file")},
        ])
        wizard = self.env["cdu.report.bulk.import.wizard"].with_user(self.data_clerk).create({
            "file_ids": [(6, 0, attachments.ids)],
        })
        wizard.action_import_reports()
        results = {line.file_name: line for line in wizard.result_line_ids}
        self.assertEqual(wizard.completed_count, 2)
        self.assertEqual(wizard.failed_count, 2)
        self.assertIn("save a new .xlsx copy", results["broken.xlsx"].error_message)
        self.assertIn("Unsupported file type .xls", results["old-report.xls"].error_message)
