"""Four business tables and published analytics views.

Revision ID: 0001_business
Revises:
"""

from alembic import op

revision = "0001_business"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS business")
    op.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    op.execute("CREATE SCHEMA IF NOT EXISTS app_state")
    op.execute("""
        CREATE TABLE business.products (
            product_id bigint PRIMARY KEY, name text NOT NULL, category text NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE business.orders (
            order_id bigint PRIMARY KEY,
            channel text NOT NULL,
            created_at timestamptz NOT NULL,
            paid_at timestamptz,
            status text NOT NULL CHECK (status IN ('paid', 'cancelled')),
            paid_cents bigint NOT NULL CHECK (paid_cents >= 0),
            CHECK ((status = 'paid' AND paid_at IS NOT NULL AND paid_cents > 0)
                OR (status = 'cancelled' AND paid_at IS NULL AND paid_cents = 0)),
            CHECK (paid_at IS NULL OR paid_at >= created_at)
        )
    """)
    op.execute("""
        CREATE TABLE business.order_items (
            item_id bigint PRIMARY KEY,
            order_id bigint NOT NULL REFERENCES business.orders(order_id),
            product_id bigint NOT NULL REFERENCES business.products(product_id),
            quantity integer NOT NULL CHECK (quantity > 0),
            paid_cents bigint NOT NULL CHECK (paid_cents > 0)
        )
    """)
    op.execute("""
        CREATE TABLE business.refunds (
            refund_id bigint PRIMARY KEY,
            item_id bigint NOT NULL REFERENCES business.order_items(item_id),
            amount_cents bigint NOT NULL CHECK (amount_cents > 0),
            completed_at timestamptz,
            status text NOT NULL CHECK (status IN ('pending', 'succeeded', 'failed')),
            CHECK ((status = 'succeeded' AND completed_at IS NOT NULL)
                OR (status <> 'succeeded' AND completed_at IS NULL))
        )
    """)
    op.execute("CREATE INDEX ON business.orders(paid_at) WHERE status = 'paid'")
    op.execute("CREATE INDEX ON business.refunds(completed_at) WHERE status = 'succeeded'")
    op.execute("CREATE INDEX ON business.order_items(order_id)")
    op.execute("CREATE INDEX ON business.refunds(item_id)")
    op.execute("""
        CREATE FUNCTION business.check_order_total() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE oid bigint; expected bigint; actual bigint;
        BEGIN
            oid := COALESCE(NEW.order_id, OLD.order_id);
            SELECT paid_cents INTO expected FROM business.orders WHERE order_id = oid;
            IF NOT FOUND THEN RETURN NULL; END IF;
            SELECT COALESCE(SUM(paid_cents), 0) INTO actual FROM business.order_items WHERE order_id = oid;
            IF expected <> actual THEN
                RAISE EXCEPTION 'order % total % differs from items %', oid, expected, actual;
            END IF;
            RETURN NULL;
        END $$
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER order_total_on_order
        AFTER INSERT OR UPDATE ON business.orders DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION business.check_order_total()
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER order_total_on_item
        AFTER INSERT OR UPDATE OR DELETE ON business.order_items DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION business.check_order_total()
    """)
    op.execute("""
        CREATE FUNCTION business.check_refund_total() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE cap bigint; total bigint;
        BEGIN
            SELECT paid_cents INTO cap FROM business.order_items WHERE item_id = NEW.item_id FOR UPDATE;
            SELECT COALESCE(SUM(amount_cents), 0) INTO total FROM business.refunds
                WHERE item_id = NEW.item_id AND status = 'succeeded';
            IF total > cap THEN RAISE EXCEPTION 'refund exceeds item paid amount'; END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER refund_total_guard AFTER INSERT OR UPDATE ON business.refunds
        FOR EACH ROW EXECUTE FUNCTION business.check_refund_total()
    """)
    op.execute("""
        CREATE VIEW analytics.sales_lines AS
        SELECT i.item_id, o.order_id, o.channel, p.category,
               o.paid_at, i.paid_cents, i.quantity
        FROM business.order_items i
        JOIN business.orders o ON o.order_id = i.order_id
        JOIN business.products p ON p.product_id = i.product_id
        WHERE o.status = 'paid'
    """)
    op.execute("""
        CREATE VIEW analytics.refund_events AS
        SELECT r.refund_id, r.item_id, o.order_id, o.channel, p.category,
               r.completed_at, r.amount_cents
        FROM business.refunds r
        JOIN business.order_items i ON i.item_id = r.item_id
        JOIN business.orders o ON o.order_id = i.order_id
        JOIN business.products p ON p.product_id = i.product_id
        WHERE r.status = 'succeeded' AND o.status = 'paid'
    """)
    op.execute("""
        CREATE VIEW analytics.cash_events AS
        SELECT 'payment'::text AS event_type, item_id AS event_id, order_id,
               channel, category, paid_at AS occurred_at, paid_cents AS amount_cents
        FROM analytics.sales_lines
        UNION ALL
        SELECT 'refund', refund_id, order_id, channel, category, completed_at,
               -amount_cents FROM analytics.refund_events
    """)
    op.execute("REVOKE ALL ON SCHEMA business FROM PUBLIC")
    op.execute("REVOKE ALL ON SCHEMA analytics FROM PUBLIC")
    op.execute("REVOKE ALL ON SCHEMA app_state FROM PUBLIC")
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA business FROM PUBLIC")
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA analytics FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP SCHEMA analytics CASCADE")
    op.execute("DROP SCHEMA business CASCADE")
    op.execute("DROP SCHEMA app_state CASCADE")
