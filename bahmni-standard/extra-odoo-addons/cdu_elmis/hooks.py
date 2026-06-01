def post_init_hook(cr, registry):
    cr.execute(
        """
        ALTER TABLE cdu_box_line
        DROP CONSTRAINT IF EXISTS cdu_box_line_unique_bagging_qa_box_line
        """
    )
    cr.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                  FROM pg_constraint
                 WHERE conname = 'cdu_box_line_unique_box_bagging_qa_line'
            ) THEN
                ALTER TABLE cdu_box_line
                ADD CONSTRAINT cdu_box_line_unique_box_bagging_qa_line
                UNIQUE (box_id, bagging_qa_id);
            END IF;
        END $$;
        """
    )
