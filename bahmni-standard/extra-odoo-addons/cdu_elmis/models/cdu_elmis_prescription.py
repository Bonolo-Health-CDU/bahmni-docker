from odoo import _, models
from odoo.exceptions import UserError


class CduPrescription(models.Model):
    _inherit = "cdu.prescription"

    _POST_DISPENSING_STATES = frozenset(
        (
            "awaiting_bagging_qa",
            "awaiting_boxing",
            "awaiting_dispatch",
            "dispatched",
        )
    )

    def action_refresh_elmis_product_catalog(self):
        self.ensure_one()
        self._ensure_cdu_groups(
            "cdu_prescription.group_cdu_data_clerk",
            "cdu_prescription.group_cdu_dispensing_officer",
        )
        self._ensure_states(
            (
                "awaiting_verification",
                "awaiting_validation",
                "rejected_to_call_center",
                "rejected_to_facility",
            )
        )
        products = self.env["cdu.elmis.stock.service"].refresh_product_catalog()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("eLMIS products refreshed"),
                "message": _("%s available medicines are ready for selection.")
                % len(products),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def action_open_dispensing(self):
        self.ensure_one()
        self._ensure_cdu_groups(
            "cdu_prescription.group_cdu_dispensing_officer",
            "cdu_prescription.group_cdu_admin",
        )
        self._ensure_states(("awaiting_dispensing",))
        dispense = self.env["cdu.dispense"].search(
            [("prescription_id", "=", self.id)],
            limit=1,
        )
        if not dispense:
            dispense = self.env["cdu.dispense"].create({"prescription_id": self.id})
        auth_action = dispense._auto_refresh_production_stock(silent=False)
        if auth_action:
            return auth_action
        return {
            "type": "ir.actions.act_window",
            "name": _("Dispense Prescription"),
            "res_model": "cdu.dispense",
            "res_id": dispense.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_open_bagging_qa(self):
        self.ensure_one()
        self._ensure_cdu_groups(
            "cdu_prescription.group_cdu_dispensing_officer",
            "cdu_prescription.group_cdu_admin",
        )
        self._ensure_states(("awaiting_bagging_qa",))
        dispense = self.env["cdu.dispense"].search(
            [("prescription_id", "=", self.id)],
            limit=1,
        )
        if not dispense:
            raise UserError(_("This prescription has no dispense record yet."))
        if dispense.state != "confirmed":
            raise UserError(_("Dispensing must be confirmed before Bagging / QA."))
        qa = self.env["cdu.bagging.qa"].search(
            [("prescription_id", "=", self.id)],
            limit=1,
        )
        if not qa:
            qa = self.env["cdu.bagging.qa"].create(
                {
                    "prescription_id": self.id,
                    "dispense_id": dispense.id,
                }
            )
        return qa._action_open()

    def action_reprint_dispensing_labels(self):
        self.ensure_one()
        self._ensure_cdu_groups(
            "cdu_prescription.group_cdu_dispensing_officer",
            "cdu_prescription.group_cdu_admin",
        )
        if self.state not in self._POST_DISPENSING_STATES:
            raise UserError(
                _("Labels can only be reprinted after dispensing has been confirmed.")
            )
        dispense = self.env["cdu.dispense"].search(
            [("prescription_id", "=", self.id)],
            limit=1,
        )
        if not dispense or dispense.state != "confirmed":
            raise UserError(_("This prescription has no confirmed dispensing record."))
        return dispense.action_print_labels()
