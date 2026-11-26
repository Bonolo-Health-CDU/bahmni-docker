from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    # These templates are noupdate records. Update the bundled date fields
    # in place so any changes elsewhere in the saved templates are preserved.
    replacements = {
        "cdu_elmis.template_bag_label_zpl": (
            (
                "^FO24,325^FDNext: {next_collection_date}^FS\n"
                "^FO360,325^FDCollected: {collection_date}^FS",
                "^FO24,325^FDCurrent Pickup Date: {current_pickup_date}^FS\n"
                "^FO24,360^FD{next_appointment_label}: {next_appointment_date}^FS",
            ),
            (
                "^FO32,476^FDNext Collection: {next_collection_date}^FS\n"
                "^FO32,536^FDCollection Date: {collection_date}^FS",
                "^FO32,476^FDCurrent Pickup Date: {current_pickup_date}^FS\n"
                "^FO32,536^FD{next_appointment_label}: {next_appointment_date}^FS",
            ),
        ),
        "cdu_elmis.template_box_label_pdf": (
            ("{collection_date} to {collection_date}", "{collection_date_range}"),
        ),
    }
    for xml_id, date_fields in replacements.items():
        template = env.ref(xml_id, raise_if_not_found=False)
        if not template or template.command_language != "zpl":
            continue
        original_body = template.template_body or ""
        body = original_body
        for old, new in date_fields:
            body = body.replace(old, new)
        if body != original_body:
            template.template_body = body
