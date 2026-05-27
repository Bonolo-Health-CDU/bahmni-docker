from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    cdu_elmis_username = fields.Char(string="eLMIS Username")
    cdu_elmis_access_token = fields.Text(
        string="eLMIS Access Token",
        copy=False,
        groups="cdu_prescription.group_cdu_dispensing_officer,cdu_prescription.group_cdu_admin",
    )
    cdu_elmis_token_expires_at = fields.Datetime(
        string="eLMIS Token Expires At",
        copy=False,
        groups="cdu_prescription.group_cdu_dispensing_officer,cdu_prescription.group_cdu_admin",
    )
