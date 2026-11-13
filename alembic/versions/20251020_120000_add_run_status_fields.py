from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20251020_add_run_status"
down_revision = "20250825_init"
branch_labels = None
depends_on = None


def upgrade():
    # Add new columns to runs table
    op.add_column("runs", sa.Column("status", sa.String(length=32), nullable=True))
    op.add_column("runs", sa.Column("error", sa.Text(), nullable=True))
    op.add_column("runs", sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("runs", sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("runs", sa.Column("artifacts_dir", sa.Text(), nullable=True))

    # Index on status for fast filtering
    op.create_index(op.f("ix_runs_status"), "runs", ["status"], unique=False)


def downgrade():
    # Drop index and columns in reverse order
    op.drop_index(op.f("ix_runs_status"), table_name="runs")
    op.drop_column("runs", "artifacts_dir")
    op.drop_column("runs", "finished_at")
    op.drop_column("runs", "started_at")
    op.drop_column("runs", "error")
    op.drop_column("runs", "status")
