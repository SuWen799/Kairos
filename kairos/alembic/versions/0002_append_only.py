"""0002: run_events / audit_events 追加不可变触发器（需求 12.4 / 9.5）。

Revision ID: 0002_append_only
Revises: fee213fa3337
Create Date: 2026-11-01
"""
from alembic import op

revision = "0002_append_only"
down_revision = "fee213fa3337"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_run_events_no_update
        BEFORE UPDATE ON run_events
        BEGIN SELECT RAISE(ABORT, 'run_events is append-only'); END
    """)
    op.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_run_events_no_delete
        BEFORE DELETE ON run_events
        BEGIN SELECT RAISE(ABORT, 'run_events is append-only'); END
    """)
    op.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_audit_events_no_update
        BEFORE UPDATE ON audit_events
        BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END
    """)
    op.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_audit_events_no_delete
        BEFORE DELETE ON audit_events
        BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_run_events_no_update")
    op.execute("DROP TRIGGER IF EXISTS trg_run_events_no_delete")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_events_no_update")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_events_no_delete")
