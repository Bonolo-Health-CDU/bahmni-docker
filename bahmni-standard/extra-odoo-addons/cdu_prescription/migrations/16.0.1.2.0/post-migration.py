import logging

from odoo import SUPERUSER_ID, api


_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    prescriptions = env["cdu.prescription"].search(
        [("medicine_line_ids", "=", False)]
    )
    editable = prescriptions.filtered(lambda prescription: not prescription.medicines_locked)
    editable.with_context(
        cdu_skip_medicine_audit=True,
        cdu_skip_regimen_sync=True,
    )._sync_regimen_from_raw()
    _logger.info(
        "Generated medicine constituents for %s existing editable prescriptions; "
        "left %s confirmed historical prescriptions unchanged.",
        len(editable.filtered("medicine_line_ids")),
        len(prescriptions - editable),
    )
