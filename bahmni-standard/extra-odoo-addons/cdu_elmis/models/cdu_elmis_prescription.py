from odoo import _, models
from odoo.exceptions import UserError


class CduPrescription(models.Model):
    _inherit = "cdu.prescription"

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
