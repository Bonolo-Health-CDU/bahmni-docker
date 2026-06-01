import base64
import csv
import hashlib
import io
import json
import re
from datetime import datetime

from odoo import _, api, fields, models
from odoo.exceptions import UserError


REPORT_HEADER_ALIASES = {
    "Patient Name": "patient_name",
    "HIV Program ID": "hiv_program_id",
    "National ID": "national_id",
    "eRegeister ID": "eregister_id",
    "eRegister ID": "eregister_id",
    "HIV Diagnosis Date": "hiv_diagnosis_date",
    "New or Revisit": "new_or_revisit",
    "Prescription Date": "prescription_date",
    "Regimen Prescribed": "regimen_prescribed",
    "Next Clinical Appointment Date": "next_clinical_appointment_date",
    "Next Drug Pickup Date": "next_drug_pickup_date",
    "Drug Pickup Point": "drug_pickup_point",
    "E-locker District": "e_locker_district",
    "Latest VL Collection Date": "latest_vl_collection_date",
    "Latest VL Result": "latest_vl_result",
    "Has Allergies": "has_allergies",
    "Allergies": "allergies",
    "Prescriber Name": "prescriber_name",
    "Primary Contact": "primary_contact",
    "Secondary Contact": "secondary_contact",
    "Gender": "gender",
    "DOB": "dob",
    "Address": "address",
    "Location": "location",
}


class CduReportRun(models.Model):
    _name = "cdu.report.run"
    _description = "CDU eRegister Report Run"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "ingested_at desc, create_date desc"

    name = fields.Char(default="/", copy=False, readonly=True)
    source_facility_id = fields.Many2one("cdu.facility", string="Source Facility")
    source_file = fields.Binary(string="Report File", attachment=True)
    source_file_name = fields.Char(string="File Name")
    ingestion_channel = fields.Selection(
        [("manual", "Manual"), ("onedrive", "OneDrive")],
        default="manual",
        readonly=True,
        tracking=True,
    )
    remote_file_id = fields.Char(readonly=True, copy=False, index=True)
    remote_file_etag = fields.Char(readonly=True, copy=False)
    remote_folder_name = fields.Char(readonly=True)
    remote_folder_path = fields.Char(readonly=True)
    poll_log_id = fields.Many2one("cdu.onedrive.poll.log", readonly=True)
    poll_triggered_at = fields.Datetime(readonly=True)
    file_hash = fields.Char(readonly=True, copy=False)
    report_title = fields.Char(readonly=True)
    report_period = fields.Char(readonly=True)
    report_generated_at = fields.Datetime(readonly=True)
    ingested_at = fields.Datetime(readonly=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("completed", "Completed"),
            ("completed_with_errors", "Completed With Errors"),
            ("failed", "Failed"),
            ("duplicate", "Duplicate"),
        ],
        default="draft",
        tracking=True,
    )
    total_rows = fields.Integer(readonly=True)
    imported_rows = fields.Integer(readonly=True)
    updated_rows = fields.Integer(readonly=True)
    duplicate_rows = fields.Integer(readonly=True)
    failed_rows = fields.Integer(readonly=True)
    error_message = fields.Text(readonly=True)
    row_ids = fields.One2many("cdu.report.row", "run_id", string="Rows")

    DATE_PATTERNS = [
    (re.compile(r'^\d{4}-\d{2}-\d{2}$'), "%Y-%m-%d"),          
    (re.compile(r'^\d{2}/\d{2}/\d{4}$'), "%d/%m/%Y"),          
    (re.compile(r'^\d{2}-\d{2}-\d{2}$'), "%m-%d-%y"),          
    (re.compile(r'^\d{2}-[a-zA-Z]{3}-\d{4}$'), "%d-%b-%Y"),
    (re.compile(r'^\d{2}-[a-zA-Z]+-\d{4}$'), "%d-%B-%Y"),  
    ]

    @api.model
    def create(self, vals):
        if vals.get("name", "/") == "/":
            vals["name"] = self.env["ir.sequence"].next_by_code("cdu.report.run") or "/"
        vals.setdefault("ingestion_channel", "manual")
        return super().create(vals)

    def action_import_file(self):
        for run in self:
            run._import_file()

    def _import_file(self, file_bytes=None):
        self.ensure_one()
        if not self.source_file:
            raise UserError(_("Please upload a report file before importing."))

        file_bytes = file_bytes or base64.b64decode(self.source_file)
        file_hash = hashlib.sha256(file_bytes).hexdigest()
        duplicate_run = self.search([
            ("id", "!=", self.id),
            ("file_hash", "=", file_hash),
            ("state", "in", ["completed", "completed_with_errors"]),
        ], limit=1)
        if duplicate_run:
            self.write({
                "file_hash": file_hash,
                "state": "duplicate",
                "error_message": "This file was already imported in %s." % duplicate_run.display_name,
            })
            return

        self.row_ids.unlink()
        try:
            parsed = self._parse_csv(file_bytes)
        except Exception as error:
            self.write({
                "file_hash": file_hash,
                "state": "failed",
                "error_message": str(error),
                "ingested_at": fields.Datetime.now(),
            })
            return

        self.write({
            "file_hash": file_hash,
            "report_title": parsed["title"],
            "report_period": parsed["period"],
            "report_generated_at": parsed["generated_at"],
            "ingested_at": fields.Datetime.now(),
            "error_message": False,
        })

        counts = {"imported": 0, "updated": 0, "duplicate": 0, "failed": 0}
        for parsed_row in parsed["rows"]:
            row_record = self.env["cdu.report.row"].create({
                "run_id": self.id,
                "row_number": parsed_row["row_number"],
                "raw_row_json": json.dumps(parsed_row["raw"], sort_keys=True),
                "normalized_row_json": json.dumps(parsed_row["values"], sort_keys=True),
                "row_hash": parsed_row["row_hash"],
                "patient_name": parsed_row["values"].get("patient_name"),
                "eregister_id": parsed_row["values"].get("eregister_id"),
                "prescription_date": self._parse_date(parsed_row["values"].get("prescription_date")),
                "regimen_prescribed": parsed_row["values"].get("regimen_prescribed"),
                "drug_pickup_point": parsed_row["values"].get("drug_pickup_point"),
            })
            result = row_record.process_row()
            counts[result] += 1

        state = "completed_with_errors" if counts["failed"] else "completed"
        self.write({
            "state": state,
            "total_rows": len(parsed["rows"]),
            "imported_rows": counts["imported"],
            "updated_rows": counts["updated"],
            "duplicate_rows": counts["duplicate"],
            "failed_rows": counts["failed"],
        })

    def _parse_csv(self, file_bytes):
        text = file_bytes.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(text)))
        if not rows:
            raise UserError(_("The uploaded file is empty."))

        header_index = None
        for index, row in enumerate(rows):
            if "Patient Name" in row and ("eRegeister ID" in row or "eRegister ID" in row):
                header_index = index
                break
        if header_index is None:
            raise UserError(_("Could not find the expected eRegister report header row."))

        header = rows[header_index]
        column_map = {}
        for index, label in enumerate(header):
            normalized = REPORT_HEADER_ALIASES.get(label.strip())
            if normalized:
                column_map[index] = normalized

        required_columns = {
            "patient_name",
            "eregister_id",
            "prescription_date",
            "regimen_prescribed",
            "next_drug_pickup_date",
            "drug_pickup_point",
        }
        missing = sorted(required_columns - set(column_map.values()))
        if missing:
            raise UserError(_("Missing required report columns: %s") % ", ".join(missing))

        parsed_rows = []
        for row_number, row in enumerate(rows[header_index + 1:], start=header_index + 2):
            if not any(cell.strip() for cell in row):
                continue
            values = {}
            raw = {}
            for index, field_name in column_map.items():
                value = row[index].strip() if index < len(row) else ""
                values[field_name] = value
                raw[header[index]] = value
            row_hash = hashlib.sha256(json.dumps(values, sort_keys=True).encode("utf-8")).hexdigest()
            parsed_rows.append({
                "row_number": row_number,
                "raw": raw,
                "values": values,
                "row_hash": row_hash,
            })

        if not parsed_rows:
            raise UserError(_("The report did not contain any data rows."))

        return {
            "title": self._metadata_value(rows, 1),
            "period": self._metadata_value(rows, 2),
            "generated_at": self._parse_generated_at(self._metadata_value(rows, 3)),
            "rows": parsed_rows,
        }

    def _metadata_value(self, rows, index):
        if len(rows) <= index:
            return False
        for value in rows[index]:
            if value.strip():
                return value.strip()
        return False

    def _parse_generated_at(self, value):
        if not value:
            return False
        match = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", value)
        if not match:
            return False
        return fields.Datetime.to_string(datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S"))


    # def _parse_date(self, value):
    #     if not value:
    #         return False
    #     for date_format in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"):
    #         try:
    #             return fields.Date.to_string(datetime.strptime(value, date_format).date())
    #         except ValueError:
    #             continue
    #     return False

    def _parse_date(self, value):
        if not value:
            return False
        val_str = str(value).strip()
        for pattern, date_format in self.DATE_PATTERNS:
            if pattern.match(val_str):
                try:
                    dt_obj = datetime.strptime(val_str, date_format)
                    return fields.Date.to_string(dt_obj.date())
                except ValueError:
                    continue

        return False

class CduReportRow(models.Model):
    _name = "cdu.report.row"
    _description = "CDU eRegister Report Row"
    _order = "run_id desc, row_number"

    run_id = fields.Many2one("cdu.report.run", required=True, ondelete="cascade")
    row_number = fields.Integer(required=True)
    status = fields.Selection(
        [
            ("pending", "Pending"),
            ("imported", "Imported"),
            ("updated", "Updated"),
            ("duplicate", "Duplicate"),
            ("failed", "Failed"),
        ],
        default="pending",
        required=True,
    )
    error_message = fields.Text()
    raw_row_json = fields.Text(readonly=True)
    normalized_row_json = fields.Text(readonly=True)
    row_hash = fields.Char(readonly=True, index=True)
    patient_name = fields.Char(readonly=True)
    eregister_id = fields.Char(string="eRegister ID", readonly=True, index=True)
    prescription_date = fields.Date(readonly=True)
    regimen_prescribed = fields.Char(readonly=True)
    drug_pickup_point = fields.Char(readonly=True)
    patient_id = fields.Many2one("res.partner", readonly=True)
    prescription_id = fields.Many2one("cdu.prescription", readonly=True)

    _sql_constraints = [
        ("unique_run_row", "unique(run_id, row_number)", "A report row can only be imported once per run."),
    ]

    def process_row(self):
        self.ensure_one()
        values = json.loads(self.normalized_row_json or "{}")
        missing = self._required_missing(values)
        if not self.run_id.source_facility_id and not values.get("location"):
            missing.append("location")
        if missing:
            self.write({
                "status": "failed",
                "error_message": "Missing required fields: %s" % ", ".join(missing),
            })
            return "failed"

        try:
            patient = self._upsert_patient(values)
            prescription, result = self._upsert_prescription(values, patient)
            self.write({
                "status": result,
                "patient_id": patient.id,
                "prescription_id": prescription.id,
                "error_message": False,
            })
            return result
        except Exception as error:
            self.write({
                "status": "failed",
                "error_message": str(error),
            })
            return "failed"

    def _required_missing(self, values):
        required = [
            "patient_name",
            "eregister_id",
            "prescription_date",
            "regimen_prescribed",
            "next_drug_pickup_date",
            "drug_pickup_point",
        ]
        return [field_name for field_name in required if not values.get(field_name)]

    def _upsert_patient(self, values):
        eregister_id = values["eregister_id"]
        Patient = self.env["res.partner"]
        patient = Patient.search(["|", ("cdu_eregister_id", "=", eregister_id), ("ref", "=", eregister_id)], limit=1)
        gender = self._map_gender(values.get("gender"))
        vals = {
            "name": values["patient_name"],
            "ref": eregister_id,
            "customer_rank": 1,
            "cdu_eregister_id": eregister_id,
            "cdu_hiv_program_id": values.get("hiv_program_id") or False,
            "cdu_national_id": values.get("national_id") or False,
            "cdu_gender": gender,
            "cdu_date_of_birth": self.run_id._parse_date(values.get("dob")),
            "phone": values.get("primary_contact") or False,
            "cdu_secondary_contact": values.get("secondary_contact") or False,
            "street": values.get("address") or False,
            "cdu_last_report_run_id": self.run_id.id,
        }
        facility = self._get_facility(values)
        if facility:
            vals["cdu_source_facility_id"] = facility.id
        if patient:
            patient.write(vals)
        else:
            patient = Patient.create(vals)
        return patient

    def _upsert_prescription(self, values, patient):
        source_key = self._prescription_source_key(values)
        existing = self.env["cdu.prescription"].search([("source_key", "=", source_key)], limit=1)
        vals = self._prescription_vals(values, patient, source_key)
        if existing:
            if existing.row_hash == self.row_hash:
                return existing, "duplicate"
            existing.write(vals)
            return existing, "updated"
        prescription = self.env["cdu.prescription"].create(vals)
        return prescription, "imported"

    def _prescription_vals(self, values, patient, source_key):
        facility = self._get_facility(values)
        collection_point = self._get_collection_point(values.get("drug_pickup_point"))
        status = "awaiting_verification"
        return {
            "source_key": source_key,
            "row_hash": self.row_hash,
            "report_run_id": self.run_id.id,
            "report_row_id": self.id,
            "source_file_name": self.run_id.source_file_name,
            "source_row_number": self.row_number,
            "source_report_generated_at": self.run_id.report_generated_at,
            "state": status,
            "patient_id": patient.id,
            "patient_identifier": values.get("eregister_id"),
            "patient_first_name": values.get("patient_name"),
            "patient_gender": self._map_gender(values.get("gender")),
            "patient_date_of_birth": self.run_id._parse_date(values.get("dob")),
            "patient_phone": values.get("primary_contact") or False,
            "patient_address": values.get("address") or False,
            "facility_id": facility.id if facility else False,
            "facility_name": values.get("location") or (facility.name if facility else False),
            "facility_code": facility.code if facility else False,
            "prescription_date": self.run_id._parse_date(values.get("prescription_date")),
            "regimen_prescribed_raw": values.get("regimen_prescribed"),
            "hiv_program_id": values.get("hiv_program_id") or False,
            "national_id": values.get("national_id") or False,
            "hiv_diagnosis_date": self.run_id._parse_date(values.get("hiv_diagnosis_date")),
            "new_or_revisit": self._map_new_or_revisit(values.get("new_or_revisit")),
            "next_clinical_visit_date": self.run_id._parse_date(values.get("next_clinical_appointment_date")),
            "next_drug_pickup_date": self.run_id._parse_date(values.get("next_drug_pickup_date")),
            "drug_pickup_point_raw": values.get("drug_pickup_point"),
            "collection_point_id": collection_point.id if collection_point else False,
            "e_locker_district": values.get("e_locker_district") or False,
            "latest_vl_collection_date": self.run_id._parse_date(values.get("latest_vl_collection_date")),
            "latest_vl_result": values.get("latest_vl_result") or False,
            "has_allergies": self._map_has_allergies(values.get("has_allergies")),
            "allergies": values.get("allergies") or False,
            "prescriber_name": values.get("prescriber_name") or False,
            "secondary_contact": values.get("secondary_contact") or False,
        }

    def _prescription_source_key(self, values):
        parts = [
            values.get("location") or "",
            values.get("eregister_id") or "",
            values.get("prescription_date") or "",
            values.get("regimen_prescribed") or "",
            values.get("drug_pickup_point") or "",
        ]
        return hashlib.sha256("|".join(parts).lower().encode("utf-8")).hexdigest()

    def _get_facility(self, values):
        name = values.get("location")
        if self.run_id.source_facility_id:
            return self.run_id.source_facility_id
        if not name:
            return False
        Facility = self.env["cdu.facility"]
        facility = Facility.search([("name", "=ilike", name)], limit=1)
        if facility:
            return facility
        code = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").upper()[:32] or "FACILITY"
        return Facility.create({"name": name, "code": code, "enabled": True})

    def _get_collection_point(self, name):
        if not name:
            return False
        CollectionPoint = self.env["cdu.collection.point"]
        normalized_name = self._normalize_collection_point_name(name)
        code = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").upper()[:32]
        candidates = CollectionPoint.search([("name", "=ilike", name)])
        if not candidates and code:
            candidates = CollectionPoint.search([("code", "=", code)])
        if not candidates:
            candidates = CollectionPoint.search([("external_reference", "!=", False)]).filtered(
                lambda point: self._normalize_collection_point_name(point.name) == normalized_name
            )

        valid_point = candidates.filtered(lambda point: point.external_reference)[:1]
        if valid_point:
            return valid_point

        if candidates:
            raise UserError(_(
                "Drug Pickup Point '%s' exists in CDU but does not have a Collect-and-Go Reference. "
                "Sync collection points from Collect-and-Go or map this pickup point before importing."
            ) % name)

        raise UserError(_(
            "Drug Pickup Point '%s' is not a valid Collect-and-Go collection location. "
            "Sync collection points from Collect-and-Go or correct the eRegister pickup point before importing."
        ) % name)

    def _normalize_collection_point_name(self, name):
        return re.sub(r"\s+", " ", (name or "").strip()).lower()

    def _map_gender(self, value):
        if value == "F":
            return "female"
        if value == "M":
            return "male"
        return False

    def _map_new_or_revisit(self, value):
        value = (value or "").strip().lower()
        if value == "new":
            return "new"
        if value == "revisit":
            return "revisit"
        if value == "restarted":
            return "restarted"
        return False

    def _map_has_allergies(self, value):
        value = (value or "").strip().lower()
        if value == "yes":
            return "yes"
        if value == "no":
            return "no"
        if value == "unknown":
            return "unknown"
        return False
