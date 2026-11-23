from sqlalchemy import text

# create_all() only creates tables that do not exist yet. These statements bring
# the tables you already have up to date (the new size and description columns,
# and the move to several photos per product). Each one can safely run on every
# start: running it a second time changes nothing.
POSTGRES_STEPS = [
    "ALTER TABLE cart_items ADD COLUMN IF NOT EXISTS size VARCHAR(20) NOT NULL DEFAULT ''",
    "ALTER TABLE order_items ADD COLUMN IF NOT EXISTS size VARCHAR(20) NOT NULL DEFAULT ''",
    "ALTER TABLE cart_items DROP CONSTRAINT IF EXISTS uq_cart_user_product",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT ''",
    # The first version kept one photo per product in "product_images". Move
    # those photos into the new "product_photos" table, then drop the old one.
    """
    DO $$
    BEGIN
        IF to_regclass('product_images') IS NOT NULL THEN
            INSERT INTO product_photos (product_id, content_type, data, position, created_at)
            SELECT i.product_id, i.content_type, i.data, 0, i.updated_at
            FROM product_images i
            WHERE NOT EXISTS (
                SELECT 1 FROM product_photos p WHERE p.product_id = i.product_id
            );
            DROP TABLE product_images;
        END IF;
    END
    $$
    """,
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
