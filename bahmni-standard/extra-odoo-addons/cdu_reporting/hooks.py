from odoo import SUPERUSER_ID, api


def post_init_hook(cr, registry):
    """Backfill only lifecycle events recoverable from immutable transactional dates."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    history = env["cdu.prescription.status.history"].with_context(
        cdu_reporting_history_internal=True
    )

    cr.execute(
        """
        SELECT p.id, p.state, p.create_date, p.create_uid
          FROM cdu_prescription p
         WHERE NOT EXISTS (
                   SELECT 1 FROM cdu_prescription_status_history h
                    WHERE h.prescription_id = p.id
               )
        """
    )
    initial_values = [
        {
            "prescription_id": prescription_id,
            "new_status": "awaiting_verification",
            "changed_at": create_date,
            "changed_by": create_uid or SUPERUSER_ID,
            "reason": "Backfilled initial receipt",
        }
        for prescription_id, _state, create_date, create_uid in cr.fetchall()
        if create_date
    ]
    if initial_values:
        history.create(initial_values)

    event_queries = [
        (
            "awaiting_validation",
            """SELECT id, verified_at, verified_by FROM cdu_prescription
                 WHERE verified_at IS NOT NULL""",
            "Backfilled verification",
        ),
        (
            "awaiting_batching",
            """SELECT id, validated_at, validated_by FROM cdu_prescription
                 WHERE validated_at IS NOT NULL""",
            "Backfilled validation",
        ),
        (
            "awaiting_dispensing",
            """SELECT p.id, b.picking_confirmed_at, b.write_uid
                  FROM cdu_prescription p JOIN cdu_batch b ON b.id = p.batch_id
                 WHERE b.picking_confirmed_at IS NOT NULL""",
            "Backfilled picking completion",
        ),
        (
            "awaiting_bagging_qa",
            """SELECT prescription_id, confirmed_at, confirmed_by FROM cdu_dispense
                 WHERE confirmed_at IS NOT NULL""",
            "Backfilled dispensing",
        ),
        (
            "awaiting_boxing",
            """SELECT prescription_id, confirmed_at, confirmed_by FROM cdu_bagging_qa
                 WHERE confirmed_at IS NOT NULL""",
            "Backfilled bagging and QA",
        ),
        (
            "awaiting_dispatch",
            """SELECT bl.prescription_id, b.confirmed_at, b.confirmed_by
                  FROM cdu_box_line bl JOIN cdu_box b ON b.id = bl.box_id
                 WHERE b.confirmed_at IS NOT NULL""",
            "Backfilled boxing",
        ),
        (
            "dispatched",
            """SELECT bl.prescription_id, b.dispatched_at, b.dispatched_by
                  FROM cdu_box_line bl JOIN cdu_box b ON b.id = bl.box_id
                 WHERE b.dispatched_at IS NOT NULL""",
            "Backfilled dispatch",
        ),
    ]
    for new_status, query, reason in event_queries:
        cr.execute(query)
        values = []
        for prescription_id, changed_at, changed_by in cr.fetchall():
            if not changed_at:
                continue
            cr.execute(
                """
                SELECT 1 FROM cdu_prescription_status_history
                 WHERE prescription_id = %s AND new_status = %s AND changed_at = %s
                 LIMIT 1
                """,
                (prescription_id, new_status, changed_at),
            )
            if cr.fetchone():
                continue
            values.append(
                {
                    "prescription_id": prescription_id,
                    "new_status": new_status,
                    "changed_at": changed_at,
                    "changed_by": changed_by or SUPERUSER_ID,
                    "reason": reason,
                }
            )
        if values:
            history.create(values)

