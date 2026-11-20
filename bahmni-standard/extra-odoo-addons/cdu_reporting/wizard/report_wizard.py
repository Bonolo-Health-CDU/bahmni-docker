import base64
import csv
import io
from datetime import datetime, time

import xlsxwriter

from odoo import _, fields, models
from odoo.exceptions import AccessError, ValidationError


REPORT_CONFIG = {
    "enrolment": {
        "title": "Patient Enrolment Details Report",
        "model": "cdu.report.enrolment",
        "action": "cdu_reporting.action_cdu_report_enrolment",
        "date_field": "enrolment_date",
        "columns": [
            ("patient_name", "Patient Name"), ("enrolment_date", "Enrolment Date"),
            ("facility_name", "Facility"), ("district_name", "District"),
            ("age", "Age"), ("age_group", "Age Group"), ("gender", "Gender"),
            ("programme", "Programme"), ("new_or_repeat", "New / Repeat"),
            ("collection_point_name", "PUP"), ("pup_type", "PUP Type"),
            ("pup_district", "PUP District"), ("next_collection_date", "Next Collection Date"),
        ],
        "measures": ["record_count", "new_count", "repeat_count"],
    },
    "verification": {
        "title": "Prescription Verification Details Report",
        "model": "cdu.report.verification",
        "action": "cdu_reporting.action_cdu_report_verification",
        "date_field": "receipt_date",
        "status_field": "verification_status",
        "status_wizard": "verification_status",
        "columns": [
            ("patient_name", "Patient Name"), ("prescription_number", "Prescription Number"),
            ("facility_name", "Facility"), ("district_name", "District"),
            ("receipt_date", "Receipt Date"), ("verification_status", "Verification Status"),
            ("verification_date", "Verification Date"), ("verifier_id", "Verifier"),
            ("rejection_reason", "Rejection Reason"),
        ],
        "measures": ["record_count"],
    },
    "validation": {
        "title": "Prescription Validation Details Report",
        "model": "cdu.report.validation",
        "action": "cdu_reporting.action_cdu_report_validation",
        "date_field": "validation_received_date",
        "status_field": "validation_status",
        "status_wizard": "validation_status",
        "columns": [
            ("patient_name", "Patient Name"), ("prescription_number", "Prescription Number"),
            ("facility_name", "Facility"), ("district_name", "District"),
            ("validation_received_date", "Date Received for Validation"),
            ("validation_date", "Validation Date"), ("validation_status", "Validation Status"),
            ("validator_id", "Validator"), ("rejection_reason", "Rejection Reason"),
        ],
        "measures": ["record_count"],
    },
    "dispensing": {
        "title": "Dispensing Report",
        "model": "cdu.report.dispensing",
        "action": "cdu_reporting.action_cdu_report_dispensing",
        "date_field": "production_received_at",
        "status_field": "dispensing_status",
        "status_wizard": "dispensing_status",
        "columns": [
            ("patient_name", "Patient Name"), ("prescription_number", "Prescription Number"),
            ("facility_name", "Facility"), ("district_name", "District"),
            ("production_received_at", "Received for Production"),
            ("dispensing_date", "Dispensing Date"), ("dispenser_id", "Dispenser"),
            ("item_count", "Items Dispensed"), ("medicine_names", "Medicines"),
            ("total_quantity_dispensed", "Total Quantity Dispensed"),
            ("dispensing_status", "Dispensing Status"), ("fulfilment_status", "Fulfilment"),
            ("next_collection_date", "Next Dispense Date"),
            ("collection_point_name", "PUP"), ("pup_type", "PUP Type"),
            ("collect_go_status", "Collect & Go Status"),
        ],
        "measures": ["record_count", "item_count", "total_quantity_dispensed"],
    },
    "parcel_production": {
        "title": "Parcel Production Report",
        "model": "cdu.report.parcel.production",
        "action": "cdu_reporting.action_cdu_report_parcel_production",
        "date_field": "production_date",
        "status_field": "dispatch_status",
        "status_wizard": "parcel_status",
        "columns": [
            ("parcel_reference", "Parcel Number"), ("production_date", "Production Date"),
            ("patient_name", "Patient Name"), ("prescription_number", "Prescription"),
            ("facility_name", "Facility"), ("district_name", "District"),
            ("packager_id", "Packager"), ("collection_point_name", "PUP"),
            ("pup_type", "PUP Type"), ("pup_district", "PUP Location"),
            ("dispatch_status", "Dispatch Status"),
            ("next_collection_date", "Next Collection Date"),
        ],
        "measures": ["record_count"],
    },
    "parcel_forecast": {
        "title": "Parcel Forecasting Report",
        "model": "cdu.report.parcel.forecast",
        "action": "cdu_reporting.action_cdu_report_parcel_forecast",
        "date_field": "forecast_date",
        "columns": [
            ("forecast_date", "Forecast Date"), ("collection_point_name", "PUP"),
            ("district_name", "District"), ("programme", "Programme"),
            ("patients_due", "Patients Due for Refill"),
            ("forecasted_parcels", "Forecasted Parcels"),
            ("actual_parcels", "Actual Parcels"), ("variance", "Variance"),
            ("variance_percentage", "Variance %"),
        ],
        "measures": ["patients_due", "forecasted_parcels", "actual_parcels", "variance"],
    },
}


class CduReportWizard(models.TransientModel):
    _name = "cdu.report.wizard"
    _description = "Generate CDU Report"

    report_type = fields.Selection(
        [
            ("enrolment", "Patient Enrolment"),
            ("verification", "Prescription Verification"),
            ("validation", "Prescription Validation"),
            ("dispensing", "Dispensing"),
            ("parcel_production", "Parcel Production"),
            ("parcel_forecast", "Parcel Forecasting"),
        ],
        required=True,
        default="dispensing",
    )
    date_from = fields.Date(required=True, default=lambda self: fields.Date.today().replace(day=1))
    date_to = fields.Date(required=True, default=fields.Date.today)
    facility_ids = fields.Many2many("cdu.facility", string="Facilities")
    collection_point_ids = fields.Many2many("cdu.collection.point", string="PUPs")
    district_name = fields.Char(string="District contains")
    programme = fields.Char(string="Programme contains")
    pup_type = fields.Selection(
        [("e_locker", "E-locker"), ("retail_pharmacy", "Partner Retail Pharmacy")],
        string="PUP Type",
    )
    gender = fields.Selection(
        [("male", "Male"), ("female", "Female"), ("other", "Other")]
    )
    age_from = fields.Integer()
    age_to = fields.Integer()
    verification_status = fields.Selection(
        [
            ("awaiting_verification", "Awaiting Verification"),
            ("verified", "Verified"),
            ("submitted_for_validation", "Submitted for Validation"),
            ("rejected", "Rejected"),
        ]
    )
    validation_status = fields.Selection(
        [
            ("awaiting_validation", "Awaiting Validation"),
            ("validated", "Validated"),
            ("released_for_batching", "Released for Batching"),
            ("rejected", "Rejected"),
        ]
    )
    dispensing_status = fields.Selection(
        [
            ("received_for_production", "Received for Production"),
            ("dispensed", "Dispensed"), ("rejected", "Rejected"),
            ("dispatched", "Dispatched"), ("collected", "Collected"),
            ("returned", "Returned"),
        ]
    )
    parcel_status = fields.Selection(
        [("pending_dispatch", "Pending Dispatch"), ("dispatched", "Dispatched")]
    )
    export_file = fields.Binary(readonly=True)
    export_filename = fields.Char(readonly=True)

    def _ensure_reporting_access(self):
        if self.env.su:
            return
        if not (
            self.env.user.has_group("cdu_reporting.group_cdu_reporting_user")
            or self.env.user.has_group("cdu_reporting.group_cdu_reporting_manager")
        ):
            raise AccessError(_("You do not have permission to generate CDU reports."))

    def _configuration(self):
        self.ensure_one()
        return REPORT_CONFIG[self.report_type]

    def _validate_period(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValidationError(_("The reporting start date must not be after the end date."))
        if self.age_from < 0 or self.age_to < 0:
            raise ValidationError(_("Age filters cannot be negative."))
        if self.age_from and self.age_to and self.age_from > self.age_to:
            raise ValidationError(_("Minimum age must not exceed maximum age."))

    def _build_domain(self):
        self.ensure_one()
        self._validate_period()
        config = self._configuration()
        model = self.env[config["model"]]
        date_field = config["date_field"]
        domain = []
        if self.date_from:
            value = self.date_from
            if model._fields[date_field].type == "datetime":
                value = fields.Datetime.to_string(datetime.combine(value, time.min))
            domain.append((date_field, ">=", value))
        if self.date_to:
            value = self.date_to
            if model._fields[date_field].type == "datetime":
                value = fields.Datetime.to_string(datetime.combine(value, time.max))
            domain.append((date_field, "<=", value))
        if self.facility_ids and "facility_id" in model._fields:
            domain.append(("facility_id", "in", self.facility_ids.ids))
        if self.collection_point_ids and "collection_point_id" in model._fields:
            domain.append(("collection_point_id", "in", self.collection_point_ids.ids))
        for wizard_field, report_field in (
            ("district_name", "district_name"),
            ("programme", "programme"),
        ):
            value = self[wizard_field]
            if value and report_field in model._fields:
                domain.append((report_field, "ilike", value))
        for field_name in ("pup_type", "gender"):
            if self[field_name] and field_name in model._fields:
                domain.append((field_name, "=", self[field_name]))
        if self.age_from and "age" in model._fields:
            domain.append(("age", ">=", self.age_from))
        if self.age_to and "age" in model._fields:
            domain.append(("age", "<=", self.age_to))
        status_wizard = config.get("status_wizard")
        if status_wizard and self[status_wizard]:
            domain.append((config["status_field"], "=", self[status_wizard]))
        return domain

    def _records(self):
        self._ensure_reporting_access()
        config = self._configuration()
        return self.env[config["model"]].search(
            self._build_domain(), order="%s asc, id asc" % config["date_field"]
        )

    def action_view_report(self):
        self._ensure_reporting_access()
        config = self._configuration()
        action = self.env["ir.actions.actions"]._for_xml_id(config["action"])
        action["domain"] = self._build_domain()
        return action

    def _display_value(self, record, field_name):
        field = record._fields[field_name]
        value = record[field_name]
        if field.type == "many2one":
            return value.display_name if value else ""
        if field.type == "selection":
            return dict(field._description_selection(self.env)).get(value, value or "")
        if field.type in ("date", "datetime"):
            return str(value or "")
        return value if value not in (False, None) else ""

    def _summary(self, records=None):
        config = self._configuration()
        model = self.env[config["model"]]
        domain = self._build_domain()
        summary = [("Total Records", model.search_count(domain))]
        status_field = config.get("status_field")
        if status_field:
            labels = dict(model._fields[status_field]._description_selection(self.env))
            groups = model.read_group(domain, ["record_count:sum"], [status_field], lazy=False)
            status_counts = {
                group.get(status_field): group.get("record_count", 0)
                for group in groups if group.get(status_field)
            }
            summary.extend(
                (labels.get(group[status_field], group[status_field]), group.get("record_count", 0))
                for group in groups if group.get(status_field)
            )
            total = sum(status_counts.values())
            if self.report_type == "verification":
                completed = status_counts.get("verified", 0) + status_counts.get(
                    "submitted_for_validation", 0
                )
                summary.extend(
                    [
                        ("Verification Rate", self._percentage(completed, total)),
                        ("Rejection Rate", self._percentage(status_counts.get("rejected", 0), total)),
                    ]
                )
            elif self.report_type == "validation":
                completed = status_counts.get("validated", 0) + status_counts.get(
                    "released_for_batching", 0
                )
                summary.extend(
                    [
                        ("Validation Rate", self._percentage(completed, total)),
                        ("Rejection Rate", self._percentage(status_counts.get("rejected", 0), total)),
                    ]
                )
            elif self.report_type == "dispensing":
                dispensed = sum(
                    status_counts.get(status, 0)
                    for status in ("dispensed", "dispatched", "collected", "returned")
                )
                dispatched = sum(
                    status_counts.get(status, 0)
                    for status in ("dispatched", "collected", "returned")
                )
                summary.extend(
                    [
                        ("Dispensing Rate", self._percentage(dispensed, total)),
                        (
                            "Collection Rate",
                            self._percentage(status_counts.get("collected", 0), dispatched),
                        ),
                    ]
                )
        if self.report_type == "dispensing":
            aggregates = model.read_group(
                domain, ["item_count:sum", "total_quantity_dispensed:sum"], [], lazy=False
            )[0]
            summary.extend(
                [
                    ("Items Dispensed", aggregates.get("item_count", 0)),
                    ("Total Quantity Dispensed", aggregates.get("total_quantity_dispensed", 0)),
                ]
            )
        elif self.report_type == "enrolment":
            aggregates = model.read_group(
                domain, ["new_count:sum", "repeat_count:sum"], [], lazy=False
            )[0]
            summary.extend(
                [
                    ("New Enrolments", aggregates.get("new_count", 0)),
                    ("Repeat Enrolments", aggregates.get("repeat_count", 0)),
                ]
            )
        elif self.report_type == "parcel_forecast":
            aggregates = model.read_group(
                domain,
                [
                    "patients_due:sum",
                    "forecasted_parcels:sum",
                    "actual_parcels:sum",
                    "variance:sum",
                ],
                [],
                lazy=False,
            )[0]
            summary.extend(
                [
                    ("Patients Due", aggregates.get("patients_due", 0)),
                    ("Forecasted Parcels", aggregates.get("forecasted_parcels", 0)),
                    ("Actual Parcels", aggregates.get("actual_parcels", 0)),
                    ("Variance", aggregates.get("variance", 0)),
                ]
            )
        return summary

    @staticmethod
    def _percentage(numerator, denominator):
        return round((float(numerator) / denominator) * 100.0, 2) if denominator else 0.0

    def _group_summary(self, group_field):
        config = self._configuration()
        model = self.env[config["model"]]
        if group_field not in model._fields:
            return []
        groups = model.read_group(
            self._build_domain(), ["record_count:sum"], [group_field], lazy=False
        )
        rows = []
        for group in groups:
            value = group.get(group_field)
            if isinstance(value, tuple):
                value = value[1]
            rows.append((value or _("Unspecified"), group.get("record_count", 0)))
        return rows

    def action_export_xlsx(self):
        records = self._records()
        config = self._configuration()
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {"in_memory": True})
        title_format = workbook.add_format(
            {"bold": True, "font_size": 14, "font_color": "#16324F"}
        )
        header_format = workbook.add_format(
            {"bold": True, "bg_color": "#DCE6F1", "border": 1}
        )
        date_format = workbook.add_format({"num_format": "yyyy-mm-dd"})

        summary_sheet = workbook.add_worksheet("Summary")
        summary_sheet.write(0, 0, config["title"], title_format)
        summary_sheet.write(1, 0, "Reporting Period")
        summary_sheet.write(1, 1, "%s to %s" % (self.date_from, self.date_to))
        for row_index, (label, value) in enumerate(self._summary(records), start=3):
            summary_sheet.write(row_index, 0, label)
            summary_sheet.write(row_index, 1, value)
        summary_sheet.set_column(0, 0, 30)
        summary_sheet.set_column(1, 1, 20)

        detail_sheet = workbook.add_worksheet("Detailed Records")
        for column_index, (_field_name, label) in enumerate(config["columns"]):
            detail_sheet.write(0, column_index, label, header_format)
        for row_index, record in enumerate(records, start=1):
            for column_index, (field_name, _label) in enumerate(config["columns"]):
                value = record[field_name]
                if record._fields[field_name].type == "date" and value:
                    detail_sheet.write_datetime(
                        row_index, column_index,
                        datetime.combine(value, time.min), date_format,
                    )
                else:
                    detail_sheet.write(row_index, column_index, self._display_value(record, field_name))
        detail_sheet.freeze_panes(1, 0)
        detail_sheet.autofilter(0, 0, max(len(records), 1), len(config["columns"]) - 1)
        detail_sheet.set_column(0, len(config["columns"]) - 1, 20)

        for sheet_name, group_field in (
            ("By Facility", "facility_id"),
            ("By District", "district_name"),
            ("By PUP", "collection_point_id"),
        ):
            sheet = workbook.add_worksheet(sheet_name)
            sheet.write_row(0, 0, [sheet_name[3:], "Records"], header_format)
            for row_index, row in enumerate(self._group_summary(group_field), start=1):
                sheet.write_row(row_index, 0, row)
            sheet.set_column(0, 0, 35)
            sheet.set_column(1, 1, 15)
        workbook.close()
        return self._download(output.getvalue(), "xlsx")

    def action_export_csv(self):
        records = self._records()
        config = self._configuration()
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow([label for _field_name, label in config["columns"]])
        for record in records:
            writer.writerow(
                [self._display_value(record, field_name) for field_name, _label in config["columns"]]
            )
        return self._download(output.getvalue().encode("utf-8-sig"), "csv")

    def _download(self, content, extension):
        filename = "cdu_%s_%s_%s.%s" % (
            self.report_type, self.date_from, self.date_to, extension
        )
        self.write(
            {"export_file": base64.b64encode(content), "export_filename": filename}
        )
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/cdu.report.wizard/%s/export_file/%s?download=true"
            % (self.id, filename),
            "target": "self",
        }

    def get_report_payload(self):
        self._ensure_reporting_access()
        config = self._configuration()
        records = self._records()
        return {
            "title": config["title"],
            "date_from": self.date_from,
            "date_to": self.date_to,
            "summary": self._summary(records),
            "by_district": self._group_summary("district_name"),
            "by_facility": self._group_summary("facility_id"),
            "record_count": len(records),
        }

    def action_export_pdf(self):
        self._ensure_reporting_access()
        return self.env.ref("cdu_reporting.action_report_cdu_summary").report_action(self)
