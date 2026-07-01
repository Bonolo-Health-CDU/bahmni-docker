import json

from odoo import _, fields, models
from odoo.exceptions import UserError


class CduReportBulkImportWizard(models.TransientModel):
    _name = "cdu.report.bulk.import.wizard"
    _description = "Bulk eRegister Report Import"

    state = fields.Selection(
        [("upload", "Upload"), ("results", "Results")],
        default="upload",
        required=True,
    )
    file_ids = fields.Many2many(
        "ir.attachment",
        "cdu_report_bulk_import_wizard_attachment_rel",
        "wizard_id",
        "attachment_id",
        string="Report Files",
    )
    result_run_ids = fields.Many2many(
        "cdu.report.run",
        "cdu_report_bulk_import_wizard_run_rel",
        "wizard_id",
        "run_id",
        string="Imported Reports",
        readonly=True,
    )
    result_line_ids = fields.One2many(
        "cdu.report.bulk.import.wizard.line",
        "wizard_id",
        string="File Results",
        readonly=True,
    )
    selected_file_count = fields.Integer(compute="_compute_selected_file_count")
    total_files = fields.Integer(readonly=True)
    processed_count = fields.Integer(readonly=True)
    completed_count = fields.Integer(readonly=True)
    completed_with_errors_count = fields.Integer(readonly=True)
    duplicate_count = fields.Integer(readonly=True)
    failed_count = fields.Integer(readonly=True)
    attention_count = fields.Integer(readonly=True)
    total_rows = fields.Integer(readonly=True)
    imported_rows = fields.Integer(readonly=True)
    updated_rows = fields.Integer(readonly=True)
    duplicate_rows = fields.Integer(readonly=True)
    failed_rows = fields.Integer(readonly=True)
    progress_percent = fields.Integer(readonly=True)

    def _compute_selected_file_count(self):
        for wizard in self:
            wizard.selected_file_count = len(wizard.file_ids)

    def action_import_reports(self):
        self.ensure_one()
        if not self.file_ids:
            raise UserError(_("Please upload at least one eRegister CSV report."))

        run_ids = []
        result_lines = []
        counts = {
            "completed": 0,
            "completed_with_errors": 0,
            "duplicate": 0,
            "failed": 0,
        }
        row_totals = {
            "total_rows": 0,
            "imported_rows": 0,
            "updated_rows": 0,
            "duplicate_rows": 0,
            "failed_rows": 0,
        }
        ReportRun = self.env["cdu.report.run"]

        self.result_line_ids.unlink()
        for sequence, attachment in enumerate(self.file_ids, start=1):
            file_name = attachment.name or attachment.display_name or _("Unnamed file")
            run = ReportRun.create({
                "source_file": attachment.datas,
                "source_file_name": file_name,
                "ingestion_channel": "manual",
            })
            run_ids.append(run.id)

            if not self._is_csv_file(file_name):
                run.write({
                    "state": "failed",
                    "ingested_at": fields.Datetime.now(),
                    "error_message": _("Only CSV files can be imported with this wizard."),
                })
            elif not attachment.datas:
                run.write({
                    "state": "failed",
                    "ingested_at": fields.Datetime.now(),
                    "error_message": _("The uploaded file is empty or could not be read."),
                })
            else:
                try:
                    run._import_file()
                except Exception as error:
                    run.write({
                        "state": "failed",
                        "ingested_at": fields.Datetime.now(),
                        "error_message": str(error),
                    })

            counts[run.state] = counts.get(run.state, 0) + 1
            for field_name in row_totals:
                row_totals[field_name] += run[field_name]
            result_lines.append((0, 0, self._result_line_values(sequence, file_name, run)))

        processed_count = len(run_ids)
        self.write({
            "state": "results",
            "result_run_ids": [(6, 0, run_ids)],
            "result_line_ids": result_lines,
            "total_files": processed_count,
            "processed_count": processed_count,
            "completed_count": counts["completed"],
            "completed_with_errors_count": counts["completed_with_errors"],
            "duplicate_count": counts["duplicate"],
            "failed_count": counts["failed"],
            "attention_count": counts["completed_with_errors"] + counts["failed"],
            "total_rows": row_totals["total_rows"],
            "imported_rows": row_totals["imported_rows"],
            "updated_rows": row_totals["updated_rows"],
            "duplicate_rows": row_totals["duplicate_rows"],
            "failed_rows": row_totals["failed_rows"],
            "progress_percent": 100 if processed_count else 0,
        })
        return self._action_open_self()

    def action_view_report_imports(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Bulk Report Import Results"),
            "res_model": "cdu.report.run",
            "view_mode": "tree,form",
            "domain": [("id", "in", self.result_run_ids.ids)],
            "context": {"create": False},
        }

    def action_view_attention_reports(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Imports Needing Attention"),
            "res_model": "cdu.report.run",
            "view_mode": "tree,form",
            "domain": [
                ("id", "in", self.result_run_ids.ids),
                ("state", "in", ["completed_with_errors", "failed"]),
            ],
            "context": {"create": False},
        }

    def action_view_completed_reports(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Completed Bulk Imports"),
            "res_model": "cdu.report.run",
            "view_mode": "tree,form",
            "domain": [
                ("id", "in", self.result_run_ids.ids),
                ("state", "=", "completed"),
            ],
            "context": {"create": False},
        }

    def _action_open_self(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Import Multiple Reports"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def _is_csv_file(self, file_name):
        return (file_name or "").lower().endswith(".csv")

    def _result_line_values(self, sequence, file_name, run):
        return {
            "sequence": sequence,
            "file_name": file_name,
            "report_run_id": run.id,
            "state": run.state,
            "location_summary": self._location_summary(run),
            "total_rows": run.total_rows,
            "imported_rows": run.imported_rows,
            "updated_rows": run.updated_rows,
            "duplicate_rows": run.duplicate_rows,
            "failed_rows": run.failed_rows,
            "error_message": run.error_message,
        }

    def _location_summary(self, run):
        if run.source_facility_id:
            return run.source_facility_id.display_name
        locations = []
        for row in run.row_ids:
            try:
                values = json.loads(row.normalized_row_json or "{}")
            except ValueError:
                values = {}
            location = (values.get("location") or "").strip()
            if location and location not in locations:
                locations.append(location)
            if len(locations) > 3:
                break
        if not locations:
            return False
        if len(locations) > 3:
            return _("%s and more") % ", ".join(locations[:3])
        return ", ".join(locations)


class CduReportBulkImportWizardLine(models.TransientModel):
    _name = "cdu.report.bulk.import.wizard.line"
    _description = "Bulk eRegister Report Import Result"
    _order = "sequence, id"

    wizard_id = fields.Many2one(
        "cdu.report.bulk.import.wizard",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(readonly=True)
    file_name = fields.Char(readonly=True)
    report_run_id = fields.Many2one("cdu.report.run", string="Report Import", readonly=True)
    state = fields.Selection(
        [
            ("completed", "Completed"),
            ("completed_with_errors", "Needs Review"),
            ("duplicate", "Duplicate"),
            ("failed", "Failed"),
        ],
        readonly=True,
    )
    location_summary = fields.Char(string="Facility / Location", readonly=True)
    total_rows = fields.Integer(readonly=True)
    imported_rows = fields.Integer(readonly=True)
    updated_rows = fields.Integer(readonly=True)
    duplicate_rows = fields.Integer(readonly=True)
    failed_rows = fields.Integer(readonly=True)
    error_message = fields.Text(readonly=True)
