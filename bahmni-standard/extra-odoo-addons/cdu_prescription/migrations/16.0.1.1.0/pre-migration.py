def migrate(cr, version):
    cr.execute(
        """
        ALTER TABLE cdu_prescription
        DROP COLUMN IF EXISTS regimen_id
        """
    )
    cr.execute("DROP TABLE IF EXISTS cdu_regimen_line")
    cr.execute("DROP TABLE IF EXISTS cdu_regimen")
