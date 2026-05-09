from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    cdu_role = fields.Selection(
        [
            ("none", "No CDU Role"),
            ("data_clerk", "CDU Data Clerk"),
            ("call_agent", "CDU Call Agent"),
            ("dispensing_officer", "CDU Dispensing Officer"),
            ("admin", "CDU Admin"),
        ],
        string="CDU Role",
        compute="_compute_cdu_role",
        inverse="_inverse_cdu_role",
        store=False,
    )

    def _cdu_role_groups(self):
        return {
            "data_clerk": self.env.ref("cdu_prescription.group_cdu_data_clerk"),
            "call_agent": self.env.ref("cdu_prescription.group_cdu_call_agent"),
            "dispensing_officer": self.env.ref("cdu_prescription.group_cdu_dispensing_officer"),
            "admin": self.env.ref("cdu_prescription.group_cdu_admin"),
        }

    def _compute_cdu_role(self):
        role_groups = self._cdu_role_groups()
        for user in self:
            if role_groups["admin"] in user.groups_id:
                user.cdu_role = "admin"
            elif role_groups["dispensing_officer"] in user.groups_id:
                user.cdu_role = "dispensing_officer"
            elif role_groups["call_agent"] in user.groups_id:
                user.cdu_role = "call_agent"
            elif role_groups["data_clerk"] in user.groups_id:
                user.cdu_role = "data_clerk"
            else:
                user.cdu_role = "none"

    def _inverse_cdu_role(self):
        role_groups = self._cdu_role_groups()
        cdu_group_ids = [group.id for group in role_groups.values()]
        for user in self:
            commands = [(3, group_id) for group_id in cdu_group_ids]
            if user.cdu_role and user.cdu_role != "none":
                commands.append((4, role_groups[user.cdu_role].id))
            user.groups_id = commands
