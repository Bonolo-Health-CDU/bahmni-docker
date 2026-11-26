from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    template = env.ref("cdu_elmis.template_box_label_pdf", raise_if_not_found=False)
    old_heading = "^FDCollection Date:^FS"
    if template and template.command_language == "zpl" and old_heading in (template.template_body or ""):
        template.template_body = template.template_body.replace(
            old_heading, "^FDParcels Collection Date Range:^FS"
        )
