"""Prevent post-refund item edits from breaking financial invariants.

Revision ID: 0002_guard_item_updates
Revises: 0001_business
"""

from alembic import op

revision = "0002_guard_item_updates"
down_revision = "0001_business"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION business.guard_item_update() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE refunded bigint;
        BEGIN
            IF NEW.item_id <> OLD.item_id OR NEW.order_id <> OLD.order_id THEN
                RAISE EXCEPTION 'item identity and order cannot change';
            END IF;
            SELECT COALESCE(SUM(amount_cents), 0) INTO refunded
            FROM business.refunds WHERE item_id = NEW.item_id AND status = 'succeeded';
            IF NEW.paid_cents < refunded THEN
                RAISE EXCEPTION 'item paid amount below existing successful refunds';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER item_update_guard BEFORE UPDATE ON business.order_items
        FOR EACH ROW EXECUTE FUNCTION business.guard_item_update()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER item_update_guard ON business.order_items")
    op.execute("DROP FUNCTION business.guard_item_update()")
