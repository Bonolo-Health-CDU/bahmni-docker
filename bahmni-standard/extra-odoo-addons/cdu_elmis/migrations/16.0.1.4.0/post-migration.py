import logging


_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # Carry the prescription-level value introduced in 16.0.1.3.0 onto every
    # existing product allocation. New product selections default independently.
    cr.execute(
        """
        UPDATE cdu_picking_patient_allocation allocation
           SET daily_units = CASE
                   WHEN resolution.daily_dose > 0
                   THEN resolution.daily_dose
                   ELSE 1
               END,
               original_daily_units = CASE
                   WHEN resolution.daily_dose > 0
                   THEN resolution.daily_dose
                   ELSE 1
               END
          FROM cdu_picking_patient_resolution resolution
         WHERE allocation.resolution_id = resolution.id
        """
    )
    cr.execute(
        """
        UPDATE cdu_picking_bulk_member member
           SET daily_units = CASE
                   WHEN resolution.daily_dose > 0
                   THEN resolution.daily_dose
                   ELSE 1
               END,
               original_daily_units = CASE
                   WHEN resolution.daily_dose > 0
                   THEN resolution.daily_dose
                   ELSE 1
               END
          FROM cdu_picking_patient_resolution resolution
         WHERE member.resolution_id = resolution.id
        """
    )

    # Confirmed historical mixed-product picks cannot be revisited. Preserve
    # their previously calculated total supplied days and mark the value as a
    # legacy confirmation without posting or changing any stock event.
    cr.execute(
        """
        WITH mixed AS (
            SELECT
                resolution.id AS resolution_id,
                GREATEST(
                    FLOOR(
                        SUM(
                            allocation.quantity_packs
                            * allocation.selected_pack_size
                        )::numeric
                        / GREATEST(resolution.daily_dose, 1)
                    )::integer,
                    1
                ) AS supplied_days
              FROM cdu_picking_patient_resolution resolution
              JOIN cdu_batch batch
                ON batch.id = resolution.batch_id
              JOIN cdu_picking_patient_allocation allocation
                ON allocation.resolution_id = resolution.id
             WHERE batch.picking_confirmed_at IS NOT NULL
             GROUP BY resolution.id
            HAVING COUNT(
                       DISTINCT COALESCE(
                           NULLIF(allocation.selected_orderable_id, ''),
                           allocation.selected_orderable_code
                       )
                   ) > 1
        )
        UPDATE cdu_picking_patient_resolution resolution
           SET confirmed_supplied_days = mixed.supplied_days,
               coverage_confirmed = TRUE,
               coverage_confirmed_by = resolution.write_uid,
               coverage_confirmed_at = batch.picking_confirmed_at
          FROM mixed, cdu_batch batch
         WHERE resolution.id = mixed.resolution_id
           AND batch.id = resolution.batch_id
        """
    )
    _logger.info(
        "CDU picking migration moved daily units to eLMIS product allocations "
        "and preserved confirmed historical mixed-product coverage."
    )
