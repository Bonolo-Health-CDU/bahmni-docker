import logging

from odoo import SUPERUSER_ID, api


_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    # Draft aggregate selections did not post an eLMIS event and cannot be
    # safely attributed to individual prescriptions, so start them cleanly.
    cr.execute(
        """
        DELETE FROM cdu_picking_fulfilment_line fulfilment
         USING cdu_batch batch
         WHERE fulfilment.batch_id = batch.id
           AND batch.picking_confirmed_at IS NULL
        """
    )

    confirmed = env["cdu.batch"].search(
        [
            ("picking_confirmed_at", "!=", False),
            ("elmis_picking_line_ids", "!=", False),
        ]
    )
    confirmed._migrate_legacy_picking_allocations()
    _logger.info(
        "CDU picking migration reset draft aggregates and backfilled %s "
        "confirmed batches without posting eLMIS stock events.",
        len(confirmed),
    )
