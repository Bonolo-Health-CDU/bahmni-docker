import logging

from odoo import SUPERUSER_ID, api


_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    resolutions = env["cdu.picking.patient.resolution"].with_context(
        cdu_allocation_migration=True
    ).search([])
    resolutions.invalidate_recordset(
        [
            "confirmed_supplied_days",
            "coverage_confirmed",
            "allocated_packs",
            "supplied_units",
            "distinct_product_count",
            "coverage_confirmation_required",
            "automatic_coverage_days",
            "coverage_days",
            "coverage_variance_days",
        ]
    )
    resolutions._compute_allocation_totals()
    resolutions._refresh_status()
    resolutions._sync_patient_supply_calculations()
    resolutions.flush_recordset()
    _logger.info(
        "CDU picking migration recomputed product-level coverage for %s "
        "prescription resolutions.",
        len(resolutions),
    )
