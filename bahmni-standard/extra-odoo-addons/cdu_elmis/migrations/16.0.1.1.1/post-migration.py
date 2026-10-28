from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    template = env.ref(
        "cdu_elmis.template_box_manifest_pdf", raise_if_not_found=False
    )
    # The bundled templates are noupdate records: an ordinary module upgrade
    # cannot repair a saved manifest template containing label commands.
    if template and (
        template.command_language != "pdf"
        or template.printer_key != "document_printer"
    ):
        template.write(
            {
                "command_language": "pdf",
                "printer_key": "document_printer",
                "report_name": "cdu_elmis.report_cdu_box_manifest",
                "template_body": False,
                "width_mm": 210,
                "height_mm": 297,
                "gap_mm": 0,
            }
        )
