import logging

from odoo import SUPERUSER_ID, api


_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    PickingLine = env["cdu.picking.line"].with_context(
        cdu_allocation_migration=True,
        cdu_skip_auto_bulk_recalculation=True,
    )

    confirmed_lines = PickingLine.search(
        [
            ("allocation_mode", "=", "bulk"),
            ("batch_id.picking_confirmed_at", "!=", False),
        ]
    )
    confirmed_lines.write({"regimen_component_mode": False})

    editable_lines = PickingLine.search(
        [
            ("allocation_mode", "=", "bulk"),
            ("batch_id.picking_confirmed_at", "=", False),
        ]
    )
    editable_lines.write({"regimen_component_mode": True})
    for picking_line in editable_lines:
        for index, group in enumerate(
            picking_line.bulk_group_ids.sorted(
                lambda candidate: (candidate.sequence, candidate.id)
            ),
            start=1,
        ):
            if group.name == "Bulk Group":
                group.name = "Component %s" % index
    editable_lines.mapped("bulk_group_ids")._populate_prescriptions()

    resolutions = (confirmed_lines | editable_lines).mapped("resolution_ids")
    resolutions.invalidate_recordset(
        [
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

    editable_lines.mapped("bulk_group_ids").with_context(
        cdu_skip_auto_bulk_recalculation=False
    )._auto_recalculate_distribution()
    _logger.info(
        "Enabled regimen-component allocation for %s editable picking lines; "
        "preserved %s confirmed historical picking lines.",
        len(editable_lines),
        len(confirmed_lines),
    )
