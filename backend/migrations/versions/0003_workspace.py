"""Persistent anonymous sessions, runs and node events."""

from alembic import op

revision = "0003_workspace"
down_revision = "0002_guard_item_updates"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE app_state.sessions (
            owner_id text PRIMARY KEY,
            token_hash text UNIQUE NOT NULL,
            csrf_token text NOT NULL,
            expires_at timestamptz NOT NULL
        );
        CREATE TABLE app_state.runs (
            run_id text PRIMARY KEY,
            owner_id text NOT NULL REFERENCES app_state.sessions(owner_id),
            question text NOT NULL,
            status text NOT NULL CHECK (status IN
                ('running','awaiting_clarification','succeeded','failed','rejected','interrupted')),
            revision integer NOT NULL DEFAULT 0,
            snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
            clarification_expires_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX runs_owner_created ON app_state.runs(owner_id, created_at DESC);
        CREATE TABLE app_state.events (
            run_id text NOT NULL REFERENCES app_state.runs(run_id) ON DELETE CASCADE,
            sequence integer NOT NULL,
            event jsonb NOT NULL,
            PRIMARY KEY(run_id, sequence)
        );
        REVOKE ALL ON ALL TABLES IN SCHEMA app_state FROM PUBLIC;
    """)


def downgrade():
    op.execute("DROP TABLE app_state.events, app_state.runs, app_state.sessions")
