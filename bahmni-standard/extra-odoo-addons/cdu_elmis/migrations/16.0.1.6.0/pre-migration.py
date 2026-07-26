def migrate(cr, version):
    cr.execute(
        """
        ALTER TABLE cdu_picking_bulk_member
        DROP CONSTRAINT IF EXISTS
            cdu_picking_bulk_member_unique_bulk_resolution
        """
    )
