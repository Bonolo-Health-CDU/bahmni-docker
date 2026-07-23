from odoo import api, fields, models


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

    @api.model_create_multi
    def create(self, vals_list):
        users = super().create(vals_list)
        users._sync_cdu_home_action()
        return users

    def write(self, vals):
        result = super().write(vals)
        if "groups_id" in vals and not self.env.context.get("skip_cdu_home_action_sync"):
            self._sync_cdu_home_action()
        return result

    def _sync_cdu_home_action(self):
        """Keep the CDU dashboard as the login home action for every CDU role."""
        role_groups = [
            self.env.ref(xml_id, raise_if_not_found=False)
            for xml_id in (
                "cdu_prescription.group_cdu_data_clerk",
                "cdu_prescription.group_cdu_call_agent",
                "cdu_prescription.group_cdu_dispensing_officer",
                "cdu_prescription.group_cdu_admin",
            )
        ]
        role_groups = [group for group in role_groups if group]
        dashboard_action = self.env.ref(
            "cdu_prescription.action_cdu_dashboard",
            raise_if_not_found=False,
        )
        report_import_action = self.env.ref(
            "cdu_prescription.action_cdu_report_run",
            raise_if_not_found=False,
        )
        if not role_groups or not dashboard_action:
            return

        managed_action_ids = {
            action.id
            for action in (dashboard_action, report_import_action)
            if action
        }
        for user in self:
            has_cdu_role = any(group in user.groups_id for group in role_groups)
            if has_cdu_role and user.action_id.id != dashboard_action.id:
                user.with_context(skip_cdu_home_action_sync=True).sudo().write(
                    {"action_id": dashboard_action.id}
                )
            elif not has_cdu_role and user.action_id.id in managed_action_ids:
                user.with_context(skip_cdu_home_action_sync=True).sudo().write(
                    {"action_id": False}
                )

    @api.model
    def _sync_all_cdu_home_actions(self):
        """Apply the dashboard home action to existing users during upgrades."""
        role_groups = [
            self.env.ref(xml_id)
            for xml_id in (
                "cdu_prescription.group_cdu_data_clerk",
                "cdu_prescription.group_cdu_call_agent",
                "cdu_prescription.group_cdu_dispensing_officer",
                "cdu_prescription.group_cdu_admin",
            )
        ]
        users = self.with_context(active_test=False).search(
            [("groups_id", "in", [group.id for group in role_groups])]
        )
        users._sync_cdu_home_action()
