STOCK_OPTION_TABLES = {
    "cdu_elmis_stock_option",
    "cdu_dispense_stock_option",
}


def table_exists(cr, table_name):
    cr.execute("SELECT to_regclass(%s)", (table_name,))
    return bool(cr.fetchone()[0])


def column_exists(cr, table_name, column_name):
    cr.execute(
        """
        SELECT 1
          FROM information_schema.columns
         WHERE table_name = %s
           AND column_name = %s
        """,
        (table_name, column_name),
    )
    return bool(cr.fetchone())


def ensure_stock_on_hand_units_column(cr, table_name):
    if table_name not in STOCK_OPTION_TABLES:
        raise ValueError("Unsupported stock option table: %s" % table_name)
    if not table_exists(cr, table_name):
        return False
    if not column_exists(cr, table_name, "stock_on_hand_units"):
        cr.execute("ALTER TABLE %s ADD COLUMN stock_on_hand_units integer" % table_name)
    return True


def backfill_stock_on_hand_units(cr, table_name):
    if not ensure_stock_on_hand_units_column(cr, table_name):
        return
    cr.execute(
        """
        UPDATE %s
           SET stock_on_hand_units = stock_on_hand,
               stock_on_hand = FLOOR(
                   stock_on_hand::numeric / COALESCE(NULLIF(pack_size, 0), 30)
               )::integer
         WHERE stock_on_hand_units IS NULL
           AND stock_on_hand IS NOT NULL
        """
        % table_name
    )
