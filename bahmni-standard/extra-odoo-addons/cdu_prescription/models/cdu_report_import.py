import base64
import binascii
import csv
import hashlib
import io
import json
import logging
import re
from datetime import date, datetime
from pathlib import PurePath
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .constants import normalize_e_locker_district


_logger = logging.getLogger(__name__)

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
    "Dosage Instructions": "dosage_instructions",
    "Dosage Instuctions": "dosage_instructions",
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
    "Remaining Days": "remaining_days",
}

REQUIRED_REPORT_FIELDS = {
    "patient_name": "Patient Name",
    "eregister_id": "eRegister ID",
    "prescription_date": "Prescription Date",
    "regimen_prescribed": "Regimen Prescribed",
    "next_drug_pickup_date": "Next Drug Pickup Date",
    "drug_pickup_point": "Drug Pickup Point",
}
REPORT_DATE_FIELDS = (
    "hiv_diagnosis_date",
    "prescription_date",
    "next_clinical_appointment_date",
    "next_drug_pickup_date",
    "latest_vl_collection_date",
    "dob",
)


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
        (re.compile(r"^\d{4}-\d{2}-\d{2}$"), "%Y-%m-%d"),
        (re.compile(r"^\d{2}/\d{2}/\d{4}$"), "%d/%m/%Y"),
        (re.compile(r"^\d{2}-\d{2}-\d{2}$"), "%m-%d-%y"),
        (re.compile(r"^\d{2}-[a-zA-Z]{3}-\d{4}$"), "%d-%b-%Y"),
        (re.compile(r"^\d{2}-[a-zA-Z]+-\d{4}$"), "%d-%B-%Y"),
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
        file_hash = False
        try:
            if file_bytes is None:
                try:
                    file_bytes = base64.b64decode(self.source_file or b"", validate=True)
                except binascii.Error as error:
                    raise UserError(_("The uploaded file could not be read. Upload the report again.")) from error
            if not file_bytes:
                raise UserError(_("The report file is missing or empty. Upload a CSV or Excel (.xlsx) report."))

            file_hash = hashlib.sha256(file_bytes).hexdigest()
            with self.env.cr.savepoint():
                self._import_report(file_bytes, file_hash)
        except UserError as error:
            reason = str(error)
        except Exception:
            _logger.exception("Unexpected error importing CDU report run %s", self.id)
            reason = _("An unexpected error stopped the import. Retry the upload or give the report import reference to your administrator.")
        else:
            return
        self.write({
            "file_hash": file_hash,
            "state": "failed",
            "error_message": reason,
            "ingested_at": fields.Datetime.now(),
        })

    def _import_report(self, file_bytes, file_hash):
        # Allow retries for completed_with_errors runs; reference-data fixes may let failed rows import.
        duplicate_run = self.search([
            ("id", "!=", self.id),
            ("file_hash", "=", file_hash),
            ("state", "=", "completed"),
        ], limit=1)
        if duplicate_run:
            self.write({
                "file_hash": file_hash,
                "state": "duplicate",
                "error_message": _("This file was already imported successfully in %s.") % duplicate_run.display_name,
            })
            return

        suffix = PurePath(self.source_file_name or "").suffix.lower()
        if suffix == ".xlsx":
            parsed = self._parse_xlsx(file_bytes)
        elif suffix in ("", ".csv"):
            parsed = self._parse_csv(file_bytes)
        else:
            raise UserError(_("Unsupported file type %s. Upload a CSV or Excel (.xlsx) eRegister report.") % suffix)

        self.row_ids.unlink()
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
                "dosage_instructions": parsed_row["values"].get("dosage_instructions"),
                "drug_pickup_point": parsed_row["values"].get("drug_pickup_point"),
            })
            result = row_record.process_row(parsed_row["cell_errors"])
            counts[result] += 1

        total = len(parsed["rows"])
        state = "completed"
        reason = False
        if counts["failed"]:
            state = "failed" if counts["failed"] == total else "completed_with_errors"
            reason = _(
                "%(failed)s of %(total)s rows could not be imported. Open the Rows tab to see each "
                "reason, then correct the indicated source rows and upload the report again.",
                failed=counts["failed"],
                total=total,
            )
        self.write({
            "state": state,
            "error_message": reason,
            "total_rows": total,
            "imported_rows": counts["imported"],
            "updated_rows": counts["updated"],
            "duplicate_rows": counts["duplicate"],
            "failed_rows": counts["failed"],
        })

    def _parse_csv(self, file_bytes):
        try:
            text = file_bytes.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise UserError(_("This file is not a readable UTF-8 CSV. Export it again as CSV UTF-8 or Excel (.xlsx).")) from error
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        try:
            return self._parse_report_rows((row, {}) for row in reader)
        except csv.Error as error:
            raise UserError(_("The CSV is malformed near line %s. Check the separators and quotation marks, or export it again.") % reader.line_num) from error

    def _parse_xlsx(self, file_bytes):
        try:
            workbook = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True, keep_links=False)
            try:
                sheets = [sheet for sheet in workbook.worksheets if sheet.sheet_state == "visible"]
                sheet = next((sheet for sheet in sheets if sheet.title.casefold() == "ready"), None)
                if sheet is None:
                    if len(sheets) != 1:
                        raise UserError(_("Name the prescription worksheet READY, or upload a workbook with one visible worksheet."))
                    sheet = sheets[0]
                # Some eRegister exports omit the used range or include formatted blank rows.
                sheet.reset_dimensions()
                return self._parse_report_rows(self._xlsx_rows(sheet))
            finally:
                workbook.close()
        except (BadZipFile, InvalidFileException, KeyError, OSError, SyntaxError, ValueError) as error:
            raise UserError(_(
                "This file could not be read as an Excel (.xlsx) workbook. Open it in Excel and "
                "save a new .xlsx copy without password protection, or export it again."
            )) from error

    def _xlsx_rows(self, sheet):
        for row in sheet.iter_rows():
            values = []
            for cell in row:
                value = cell.value
                if (
                    cell.data_type == "n"
                    and value is not None
                    and value >= 0
                    and int(value) == value
                    and re.fullmatch(r"0{2,}", cell.number_format or "")
                ):
                    value = str(int(value)).zfill(len(cell.number_format))
                values.append(value)
            cell_errors = {
                index: cell.value for index, cell in enumerate(row) if cell.data_type == "e"
            }
            yield values, cell_errors

    def _parse_report_rows(self, rows):
        numbered_rows = enumerate(rows, start=1)
        preamble = {}
        for row_number, (row, _cell_errors) in numbered_rows:
            header = [self._cell_text(value) for value in row]
            column_map = {
                index: REPORT_HEADER_ALIASES[label]
                for index, label in enumerate(header)
                if label in REPORT_HEADER_ALIASES
            }
            if "patient_name" in column_map.values() or len(column_map) >= 2:
                break
            if row_number in (2, 3, 4):
                preamble[row_number] = next((value for value in header if value), False)
        else:
            raise UserError(_(
                "The eRegister column headers could not be found. Use the prescription report "
                "template and check that it includes Patient Name and eRegister ID."
            ))

        column_names = set(column_map.values())
        missing = [label for field_name, label in REQUIRED_REPORT_FIELDS.items() if field_name not in column_names]
        if not self.source_facility_id and "location" not in column_names:
            missing.append("Location")
        if missing:
            raise UserError(_(
                "Required columns missing from header row %(row)s: %(columns)s. "
                "Check the column names and upload the report again.",
                row=row_number,
                columns=", ".join(missing),
            ))

        parsed_rows = []
        for row_number, (row, cell_errors) in numbered_rows:
            values = {}
            raw = {}
            for index, field_name in column_map.items():
                value = self._cell_text(row[index]) if index < len(row) else ""
                values[field_name] = value
                raw[header[index]] = value
            if not any(raw.values()):
                continue
            row_hash = hashlib.sha256(json.dumps(values, sort_keys=True).encode("utf-8")).hexdigest()
            parsed_rows.append({
                "row_number": row_number,
                "raw": raw,
                "values": values,
                "row_hash": row_hash,
                "cell_errors": [
                    _(
                        "%(column)s contains an Excel error (%(error)s). Replace it with the correct value.",
                        column=header[index],
                        error=cell_errors[index],
                    )
                    for index in column_map
                    if index in cell_errors
                ],
            })

        if not parsed_rows:
            raise UserError(_("The report has column headers but no prescription rows. Upload a report containing prescriptions."))

        return {
            "title": preamble.get(2, False),
            "period": preamble.get(3, False),
            "generated_at": self._parse_generated_at(preamble.get(4)),
            "rows": parsed_rows,
        }

    def _cell_text(self, value):
        if value is None:
            return ""
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value).strip()

    def _parse_generated_at(self, value):
        if not value:
            return False
        match = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", value)
        if not match:
            return False
        try:
            return fields.Datetime.to_string(datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S"))
        except ValueError as error:
            raise UserError(_("The report generation timestamp is invalid. Check the report metadata or export the report again.")) from error

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
    dosage_instructions = fields.Char(readonly=True)
    drug_pickup_point = fields.Char(readonly=True)
    patient_id = fields.Many2one("res.partner", readonly=True)
    prescription_id = fields.Many2one("cdu.prescription", readonly=True)

    _sql_constraints = [
        ("unique_run_row", "unique(run_id, row_number)", "A report row can only be imported once per run."),
    ]

    def process_row(self, cell_errors=None):
        self.ensure_one()
        values = json.loads(self.normalized_row_json or "{}")
        errors = list(cell_errors or [])
        missing = [label for name, label in REQUIRED_REPORT_FIELDS.items() if not values.get(name)]
        if not self.run_id.source_facility_id and not values.get("location"):
            missing.append("Location")
        if missing:
            errors.append(_("Missing required values: %s.") % ", ".join(missing))
        for name in ("prescription_date", "next_drug_pickup_date"):
            if values.get(name) and not self.run_id._parse_date(values[name]):
                errors.append(_(
                    "%s is not a valid date. Use a calendar date in YYYY-MM-DD or DD/MM/YYYY format."
                ) % REQUIRED_REPORT_FIELDS[name])

        if not errors:
            try:
                with self.env.cr.savepoint():
                    patient = self._upsert_patient(values)
                    prescription, result = self._upsert_prescription(values, patient)
                    self.write({
                        "status": result,
                        "patient_id": patient.id,
                        "prescription_id": prescription.id,
                        "error_message": False,
                    })
            except UserError as error:
                errors.append(str(error))
            except Exception:
                _logger.exception("Unexpected error importing CDU report run %s, row %s", self.run_id.id, self.row_number)
                errors.append(_("This row could not be saved. Give the report import reference and source row number to your administrator."))
            else:
                return result

        self.write({"status": "failed", "error_message": "\n".join(errors)})
        return "failed"

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
        Prescription = self.env["cdu.prescription"]
        prescription_date = self.run_id._parse_date(values["prescription_date"])
        source_key = self._prescription_source_key(values, prescription_date)
        # Earlier imports hashed the date as written. Look up those keys before inserting an ISO-date key.
        date_value = date.fromisoformat(prescription_date)
        source_keys = {source_key, self._prescription_source_key(values, values["prescription_date"])}
        for _pattern, date_format in self.run_id.DATE_PATTERNS:
            legacy_date = date_value.strftime(date_format)
            if self.run_id._parse_date(legacy_date) == prescription_date:
                source_keys.add(self._prescription_source_key(values, legacy_date))
        existing = Prescription.search([("source_key", "in", list(source_keys))], limit=2)
        if len(existing) > 1:
            raise UserError(_("This row matches more than one existing prescription. Ask an administrator to review the matches before importing it."))
        if existing:
            if existing.row_hash == self.row_hash or self._same_report_values(existing.report_row_id, values):
                return existing, "duplicate"
            existing.write(self._prescription_vals(values, patient, existing.source_key))
            return existing, "updated"
        prescription = Prescription.create(self._prescription_vals(values, patient, source_key))
        return prescription, "imported"

    def _same_report_values(self, previous_row, values):
        if not previous_row.normalized_row_json:
            return False
        previous = json.loads(previous_row.normalized_row_json)
        for field_name in previous.keys() | values.keys():
            old_value = previous.get(field_name) or ""
            new_value = values.get(field_name) or ""
            if field_name in REPORT_DATE_FIELDS:
                old_value = self.run_id._parse_date(old_value) or old_value
                new_value = self.run_id._parse_date(new_value) or new_value
            if old_value != new_value:
                return False
        return True

    def _prescription_vals(self, values, patient, source_key):
        facility = self._get_facility(values)
        collection_point = self._get_collection_point(values.get("drug_pickup_point"))
        status = "awaiting_verification"
        vals = {
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
            "dosage_instructions": values.get("dosage_instructions") or False,
            "hiv_program_id": values.get("hiv_program_id") or False,
            "national_id": values.get("national_id") or False,
            "hiv_diagnosis_date": self.run_id._parse_date(values.get("hiv_diagnosis_date")),
            "new_or_revisit": self._map_new_or_revisit(values.get("new_or_revisit")),
            "next_clinical_visit_date": self.run_id._parse_date(values.get("next_clinical_appointment_date")),
            "next_drug_pickup_date": self.run_id._parse_date(values.get("next_drug_pickup_date")),
            "drug_pickup_point_raw": values.get("drug_pickup_point"),
            "collection_point_id": collection_point.id if collection_point else False,
            "e_locker_district": normalize_e_locker_district(
                values.get("e_locker_district")
            ),
            "latest_vl_collection_date": self.run_id._parse_date(values.get("latest_vl_collection_date")),
            "latest_vl_result": values.get("latest_vl_result") or False,
            "has_allergies": self._map_has_allergies(values.get("has_allergies")),
            "allergies": values.get("allergies") or False,
            "prescriber_name": values.get("prescriber_name") or False,
            "secondary_contact": values.get("secondary_contact") or False,
        }
        if "remaining_days" in values:
            vals["remaining_days_supply"] = self._parse_integer(values.get("remaining_days"))
        return vals

    def _prescription_source_key(self, values, prescription_date):
        parts = [
            values.get("location") or "",
            values.get("eregister_id") or "",
            prescription_date,
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

    def _parse_integer(self, value):
        if value in (None, False, ""):
            return 0
        match = re.search(r"-?\d+", str(value))
        return int(match.group(0)) if match else 0
