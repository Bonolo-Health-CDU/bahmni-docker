from datetime import timedelta

from odoo import _, api, fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.model
    def get_cdu_dashboard_data(self):
        user = self.env.user
        role = self._get_cdu_dashboard_role(user)
        if not role:
            return {
                "role": False,
                "role_label": _("No CDU Role"),
                "sections": [],
                "warnings": [],
            }

        card_definitions = self._cdu_dashboard_card_definitions()
        role_cards = {
            "data_clerk": [
                "report_imports",
                "awaiting_verification",
                "rejected_to_facility",
                "patients",
            ],
            "call_agent": [
                "rejected_to_call_center",
                "patients",
            ],
            "dispensing_officer": [
                "awaiting_validation",
                "awaiting_batching",
                "workload_batches",
                "awaiting_dispensing",
                "awaiting_bagging_qa",
                "awaiting_boxing",
                "awaiting_dispatch",
            ],
            "admin": list(card_definitions),
        }

        sections = []
        for section_key, section_title in (
            ("intake", _("Intake & Review")),
            ("production", _("Production")),
            ("dispatch", _("Dispatch")),
        ):
            cards = [
                self._prepare_cdu_dashboard_card(card_definitions[key])
                for key in role_cards[role]
                if card_definitions[key]["section"] == section_key
            ]
            if cards:
                sections.append(
                    {
                        "key": section_key,
                        "title": section_title,
                        "cards": cards,
                    }
                )

        return {
            "role": role,
            "role_label": dict(self._fields["cdu_role"].selection).get(role),
            "user_name": user.name,
            "sections": sections,
            "warnings": self._get_cdu_integration_warnings() if role == "admin" else [],
        }

    def _get_cdu_dashboard_role(self, user):
        if user.has_group("cdu_prescription.group_cdu_admin"):
            return "admin"
        if user.has_group("cdu_prescription.group_cdu_dispensing_officer"):
            return "dispensing_officer"
        if user.has_group("cdu_prescription.group_cdu_call_agent"):
            return "call_agent"
        if user.has_group("cdu_prescription.group_cdu_data_clerk"):
            return "data_clerk"
        return False

    def _cdu_dashboard_card_definitions(self):
        return {
            "report_imports": {
                "section": "intake",
                "title": _("Report Imports"),
                "description": _("Upload eRegister reports and review import results."),
                "model": "cdu.report.run",
                "domain": [],
                "action": "cdu_prescription.action_cdu_report_run",
                "icon": "fa-cloud-upload",
                "tone": "blue",
            },
            "awaiting_verification": {
                "section": "intake",
                "title": _("Awaiting Verification"),
                "description": _("Review patient and prescription information."),
                "model": "cdu.prescription",
                "domain": [("state", "=", "awaiting_verification")],
                "action": "cdu_prescription.action_cdu_prescription_awaiting_verification",
                "icon": "fa-user",
                "tone": "teal",
            },
            "rejected_to_call_center": {
                "section": "intake",
                "title": _("Rejected to Call Center"),
                "description": _("Resolve prescriptions that need patient follow-up."),
                "model": "cdu.prescription",
                "domain": [("state", "=", "rejected_to_call_center")],
                "action": "cdu_prescription.action_cdu_prescription_rejected_to_call_center",
                "icon": "fa-phone",
                "tone": "orange",
            },
            "rejected_to_facility": {
                "section": "intake",
                "title": _("Rejected to Facility"),
                "description": _("Resolve prescriptions sent back to the facility."),
                "model": "cdu.prescription",
                "domain": [("state", "=", "rejected_to_facility")],
                "action": "cdu_prescription.action_cdu_prescription_rejected_to_facility",
                "icon": "fa-hospital-o",
                "tone": "red",
            },
            "patients": {
                "section": "intake",
                "title": _("Patients"),
                "description": _("Find CDU patients and prescription history."),
                "model": "res.partner",
                "domain": [
                    "|",
                    "|",
                    ("cdu_eregister_id", "!=", False),
                    ("cdu_hiv_program_id", "!=", False),
                    ("cdu_last_report_run_id", "!=", False),
                ],
                "action": "cdu_prescription.action_cdu_patient",
                "icon": "fa-users",
                "tone": "purple",
            },
            "awaiting_validation": {
                "section": "intake",
                "title": _("Awaiting Validation"),
                "description": _("Complete the clinical and medicine review."),
                "model": "cdu.prescription",
                "domain": [("state", "=", "awaiting_validation")],
                "action": "cdu_prescription.action_cdu_prescription_awaiting_validation",
                "icon": "fa-check-square-o",
                "tone": "teal",
            },
            "awaiting_batching": {
                "section": "production",
                "title": _("Awaiting Batching"),
                "description": _("Validated prescriptions ready for batching."),
                "model": "cdu.prescription",
                "domain": [("state", "=", "awaiting_batching")],
                "action": "cdu_prescription.action_cdu_prescription_awaiting_batching",
                "icon": "fa-clone",
                "tone": "blue",
            },
            "workload_batches": {
                "section": "production",
                "title": _("Workload Batches"),
                "description": _("Prepare and monitor active workload batches."),
                "model": "cdu.batch",
                "domain": [("state", "!=", "done")],
                "action": "cdu_prescription.action_cdu_batch",
                "icon": "fa-tasks",
                "tone": "purple",
            },
            "awaiting_dispensing": {
                "section": "production",
                "title": _("Awaiting Dispensing"),
                "description": _("Dispense medicines and prepare labels."),
                "model": "cdu.prescription",
                "domain": [("state", "=", "awaiting_dispensing")],
                "action": "cdu_elmis.action_cdu_dispensing_work_queue",
                "icon": "fa-medkit",
                "tone": "blue",
            },
            "awaiting_bagging_qa": {
                "section": "production",
                "title": _("Bagging / QA"),
                "description": _("Check, label, seal, and approve parcels."),
                "model": "cdu.prescription",
                "domain": [("state", "=", "awaiting_bagging_qa")],
                "action": "cdu_elmis.action_cdu_bagging_qa_work_queue",
                "icon": "fa-shield",
                "tone": "teal",
            },
            "awaiting_boxing": {
                "section": "production",
                "title": _("Boxing"),
                "description": _("Scan approved bags into delivery boxes."),
                "model": "cdu.prescription",
                "domain": [("state", "=", "awaiting_boxing")],
                "action": "cdu_elmis.action_cdu_boxing",
                "icon": "fa-cube",
                "tone": "orange",
            },
            "awaiting_dispatch": {
                "section": "dispatch",
                "title": _("Awaiting Dispatch"),
                "description": _("Review boxes ready for dispatch handover."),
                "model": "cdu.prescription",
                "domain": [("state", "=", "awaiting_dispatch")],
                "action": "cdu_elmis.action_cdu_awaiting_dispatch",
                "icon": "fa-truck",
                "tone": "green",
            },
        }

    def _prepare_cdu_dashboard_card(self, definition):
        model_name = definition["model"]
        action = definition["action"]
        model_available = model_name in self.env
        action_available = bool(self.env.ref(action, raise_if_not_found=False))
        return {
            "title": definition["title"],
            "description": definition["description"],
            "count": (
                self.env[model_name].search_count(definition["domain"])
                if model_available
                else 0
            ),
            "action": action if action_available else False,
            "icon": definition["icon"],
            "tone": definition["tone"],
        }

    def _get_cdu_integration_warnings(self):
        since = fields.Datetime.now() - timedelta(days=7)
        warnings = []
        warning_sources = [
            {
                "model": "cdu.report.run",
                "domain": [("state", "=", "failed"), ("ingested_at", ">=", since)],
                "date_field": "ingested_at",
                "title": _("Failed report imports"),
                "action": "cdu_prescription.action_cdu_report_run",
                "icon": "fa-file-text-o",
            },
            {
                "model": "cdu.elmis.api.log",
                "domain": [("success", "=", False), ("timestamp", ">=", since)],
                "date_field": "timestamp",
                "title": _("eLMIS errors"),
                "action": "cdu_elmis.action_cdu_elmis_api_log",
                "icon": "fa-exchange",
            },
            {
                "model": "cdu.collect.go.api.log",
                "domain": [("success", "=", False), ("timestamp", ">=", since)],
                "date_field": "timestamp",
                "title": _("Collect-and-Go errors"),
                "action": "cdu_elmis.action_cdu_collect_go_api_log",
                "icon": "fa-truck",
            },
        ]
        for source in warning_sources:
            if source["model"] not in self.env:
                continue
            count = self.env[source["model"]].search_count(source["domain"])
            if not count:
                continue
            latest = self.env[source["model"]].search(
                source["domain"],
                order="%s desc, id desc" % source["date_field"],
                limit=1,
            )
            warnings.append(
                {
                    "title": source["title"],
                    "count": count,
                    "latest_at": fields.Datetime.to_string(latest[source["date_field"]]),
                    "action": (
                        source["action"]
                        if self.env.ref(source["action"], raise_if_not_found=False)
                        else False
                    ),
                    "icon": source["icon"],
                }
            )
        return warnings
