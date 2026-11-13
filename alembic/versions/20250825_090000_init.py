from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import os

# revision identifiers, used by Alembic.
revision = "20250825_init"
down_revision = None
branch_labels = None
depends_on = None


def _timescale_enabled() -> bool:
    """Return True if Timescale features should be applied."""
    disable = os.getenv("DISABLE_TIMESCALE_FEATURES", "false").lower()
    return disable not in {"1", "true", "yes"}


def upgrade():
    ts_enabled = _timescale_enabled()

    if ts_enabled:
        # Enable TimescaleDB extension if allowed
        op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")

    # runs table
    op.create_table(
        "runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org", sa.String(length=256), nullable=True),
        sa.Column("project", sa.String(length=256), nullable=True),
        sa.Column("repo", sa.String(length=256), nullable=True),
        sa.Column("branch", sa.String(length=256), nullable=True),
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.Column("executed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("environment", sa.String(length=64), nullable=True),
        sa.Column("run_label", sa.String(length=256), nullable=True),
        sa.Column("tool_version", sa.String(length=64), nullable=True),
        sa.Column("thresholds_version", sa.String(length=64), nullable=True),
        sa.Column("config_hash", sa.String(length=128), nullable=True),
        sa.Column("calculation_version", sa.String(length=64), nullable=True),
    )

    # basic indexes for runs
    op.execute("CREATE INDEX IF NOT EXISTS ix_runs_org ON runs(org);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_runs_project ON runs(project);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_runs_repo ON runs(repo);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_runs_branch ON runs(branch);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_runs_commit_sha ON runs(commit_sha);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_runs_executed_at ON runs(executed_at);")

    # Deduplication partial unique indexes
    # Unique for runs with commit_sha present
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_run_with_commit
        ON runs(org, project, repo, branch, commit_sha)
        WHERE commit_sha IS NOT NULL;
        """
    )
    # Note: Unique constraint for runs without commit_sha (date + label) removed
    # PostgreSQL doesn't allow date functions in unique index expressions
    # Application logic will handle deduplication for commitless runs

    # raw_metrics table (composite PK with collected_at for TimescaleDB partitioning)
    op.create_table(
        "raw_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org", sa.String(length=256), nullable=True),
        sa.Column("project", sa.String(length=256), nullable=True),
        sa.Column("repo", sa.String(length=256), nullable=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("source_key", sa.String(length=256), nullable=False),
        sa.Column("raw_data", postgresql.JSONB(), nullable=False),
        sa.Column("collected_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", "collected_at"),
    )

    # indexes for raw_metrics
    op.execute("CREATE INDEX IF NOT EXISTS ix_raw_metrics_org ON raw_metrics(org);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_raw_metrics_project ON raw_metrics(project);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_raw_metrics_repo ON raw_metrics(repo);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_raw_metrics_run_id ON raw_metrics(run_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_raw_metrics_source_system ON raw_metrics(source_system);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_raw_metrics_source_key ON raw_metrics(source_key);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_raw_metrics_collected_at ON raw_metrics(collected_at);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_raw_metrics_org_project_repo ON raw_metrics(org, project, repo);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_raw_metrics_source_time ON raw_metrics(source_key, collected_at DESC);")

    # metrics table (composite PK with observed_at for TimescaleDB partitioning)
    op.create_table(
        "metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org", sa.String(length=256), nullable=True),
        sa.Column("project", sa.String(length=256), nullable=True),
        sa.Column("repo", sa.String(length=256), nullable=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope", sa.String(length=256), nullable=True),
        sa.Column("metric_key", sa.String(length=256), nullable=False),
        sa.Column("metric_value", sa.NUMERIC(18, 6), nullable=True),
        sa.Column("metric_json", postgresql.JSONB(), nullable=True),
        sa.Column("score", sa.NUMERIC(5, 4), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=True),
        sa.Column("calculation_version", sa.String(length=64), nullable=True),
        sa.Column("observed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", "observed_at"),
        sa.UniqueConstraint("run_id", "scope", "metric_key", "observed_at", name="uq_metric_per_run"),
    )

    # indexes for metrics
    op.execute("CREATE INDEX IF NOT EXISTS ix_metrics_org ON metrics(org);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_metrics_project ON metrics(project);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_metrics_repo ON metrics(repo);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_metrics_run_id ON metrics(run_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_metrics_scope ON metrics(scope);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_metrics_metric_key ON metrics(metric_key);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_metrics_observed_at ON metrics(observed_at);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_metrics_org_project_repo ON metrics(org, project, repo);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_metrics_key_time ON metrics(metric_key, observed_at DESC);")

    # Optional Timescale hypertables and policies
    if ts_enabled:
        # Convert tables to hypertables
        op.execute("SELECT create_hypertable('raw_metrics','collected_at', if_not_exists => TRUE);")
        op.execute("SELECT create_hypertable('metrics','observed_at', if_not_exists => TRUE);")

        # Enable compression and set policies
        op.execute(
            "ALTER TABLE raw_metrics SET (timescaledb.compress = true, timescaledb.compress_segmentby = 'org,project,repo', timescaledb.compress_orderby = 'collected_at DESC');"
        )
        op.execute(
            "ALTER TABLE metrics SET (timescaledb.compress = true, timescaledb.compress_segmentby = 'org,project,repo', timescaledb.compress_orderby = 'observed_at DESC');"
        )
        op.execute("SELECT add_compression_policy('raw_metrics', INTERVAL '7 days');")
        op.execute("SELECT add_compression_policy('metrics', INTERVAL '7 days');")
        op.execute("SELECT add_retention_policy('raw_metrics', INTERVAL '1 year');")
        op.execute("SELECT add_retention_policy('metrics', INTERVAL '2 years');")

    # Data quality checks (Note: PostgreSQL doesn't support IF NOT EXISTS for constraints)
    op.execute(
        """
        ALTER TABLE metrics
        ADD CONSTRAINT chk_metrics_has_value
        CHECK (metric_value IS NOT NULL OR metric_json IS NOT NULL);
        """
    )
    op.execute(
        """
        ALTER TABLE metrics
        ADD CONSTRAINT chk_metrics_status_enum
        CHECK (status IS NULL OR status IN ('pass', 'fail', 'warning', 'info'));
        """
    )


def downgrade():
    ts_enabled = _timescale_enabled()

    if ts_enabled:
        # Remove policies if present
        op.execute("SELECT remove_retention_policy('raw_metrics');")
        op.execute("SELECT remove_retention_policy('metrics');")
        op.execute("SELECT remove_compression_policy('raw_metrics');")
        op.execute("SELECT remove_compression_policy('metrics');")

    # Drop tables
    op.drop_table("metrics")
    op.drop_table("raw_metrics")
    op.drop_table("runs")
