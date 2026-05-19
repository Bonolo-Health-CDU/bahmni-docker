from odoo import _, fields, models


class CduElmisAuthWizard(models.TransientModel):
    _name = "cdu.elmis.auth.wizard"
    _description = "Authenticate eLMIS User"

    username = fields.Char(string="eLMIS Username", required=True)
    password = fields.Char(string="eLMIS Password", required=True)
    batch_id = fields.Many2one("cdu.batch", string="Workload Batch")

    def action_authenticate(self):
        self.ensure_one()
        token_data = self.env["cdu.elmis.stock.service"].authenticate_elmis_user(
            self.username,
            self.password,
        )
        self.env.user.sudo().write(
            {
                "cdu_elmis_username": self.username,
                "cdu_elmis_access_token": token_data["access_token"],
                "cdu_elmis_token_expires_at": token_data["expires_at"],
            }
        )
        message = _("eLMIS authentication succeeded. You can continue the stock action.")
        if self.batch_id:
            message = _(
                "eLMIS authentication succeeded for %s. You can continue the stock action."
            ) % self.batch_id.display_name

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("eLMIS authenticated"),
                "message": message,
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
