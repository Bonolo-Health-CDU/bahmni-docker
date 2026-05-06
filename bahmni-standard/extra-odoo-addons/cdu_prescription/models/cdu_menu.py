from odoo import models


class IrUiMenu(models.Model):
    _inherit = "ir.ui.menu"

    def _filter_visible_menus(self):
        menus = super()._filter_visible_menus()
        user = self.env.user
        is_focused_cdu_user = (
            user.has_group("cdu_prescription.group_cdu_data_clerk")
            or user.has_group("cdu_prescription.group_cdu_call_agent")
            or user.has_group("cdu_prescription.group_cdu_dispensing_officer")
        ) and not (
            user.has_group("cdu_prescription.group_cdu_admin")
        )
        if not is_focused_cdu_user:
            return menus

        hidden_root_xmlids = {
            "mail.menu_root_discuss",
            "bahmni_sale.bahmni_menu_root",
            "bahmni_reports.stock_report_menu_root",
            "spreadsheet_dashboard.spreadsheet_dashboard_menu_root",
            "base.menu_management",
        }
        menu_xmlids = menus.get_external_id()
        return menus.filtered(lambda menu: menu_xmlids.get(menu.id) not in hidden_root_xmlids)
