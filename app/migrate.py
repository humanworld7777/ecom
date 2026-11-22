from sqlalchemy import text

# create_all() only creates tables that do not exist yet. These statements add
# the new "size" columns to the tables you already have. Each one can safely
# run on every start: running it a second time changes nothing.
POSTGRES_STEPS = [
    "ALTER TABLE cart_items ADD COLUMN IF NOT EXISTS size VARCHAR(20) NOT NULL DEFAULT ''",
    "ALTER TABLE order_items ADD COLUMN IF NOT EXISTS size VARCHAR(20) NOT NULL DEFAULT ''",
    "ALTER TABLE cart_items DROP CONSTRAINT IF EXISTS uq_cart_user_product",
    """
    DO $$
    BEGIN
        ALTER TABLE cart_items
            ADD CONSTRAINT uq_cart_user_product_size UNIQUE (user_id, product_id, size);
    EXCEPTION WHEN duplicate_object OR duplicate_table THEN
        NULL;
    END
    $$
    """,
]


def run_migrations(engine):
    # A fresh SQLite database (local use and tests) is built from the models,
    # so only the PostgreSQL database needs these steps.
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        for statement in POSTGRES_STEPS:
            connection.execute(text(statement))
