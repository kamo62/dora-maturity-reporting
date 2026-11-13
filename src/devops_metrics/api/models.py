from __future__ import annotations

from pydantic import BaseModel, Field, model_validator
from devops_metrics.config.access import checked_path, validate_org, validate_target


class CollectionConfig(BaseModel):
    """
    API request model for triggering metrics collection.

    Mirrors CLI collect_raw options.
    """

    # Selection
    org: str | None = Field(None, description="Organization identifier (overrides ADO_AZURE_DEVOPS_ORG)")
    project: str | None = Field(None, description="Single project to collect from")
    all_projects: bool = Field(False, description="Collect from all available projects")

    # Execution profile
    minimal: bool = Field(False, description="Limit to 10 projects for testing/development")
    fast: bool = Field(False, description="Optimized run: limit projects, use delta-mode, skip stale repos")
    full: bool = Field(False, description="Deep run: disable delta-mode and repo skipping")

    # Outputs
    write_files: bool = Field(True, description="Write outputs to local files")
    write_db: bool = Field(True, description="Write outputs to database")
    output_path: str | None = Field(None, description="Custom output path (default: exports/{timestamp}/metrics)")

    # Assessments
    include_maturity: bool = Field(True, description="Include maturity assessment")
    include_segments: bool = Field(True, description="Include segment-level maturity (requires include_maturity)")

    # Metadata
    run_label: str | None = Field(None, description="Human-friendly label for this run")
    repo: str | None = Field(None, description="Repository name (for metadata)")
    branch: str | None = Field(None, description="Branch name (for metadata)")
    commit_sha: str | None = Field(None, description="Commit SHA (for metadata)")

    # Advanced options
    db_strict: bool = Field(False, description="Fail-fast if DB requested but unavailable")
    db_reset: bool = Field(False, description="DANGEROUS: Clear DB tables before run")
    validate_calculations: bool = Field(False, description="Validate Excel vs DB calculations (requires write_db + write_files)")

    # Backfill mode
    backfill_from: str | None = Field(None, description="Path to directory with historical JSON exports to ingest")
    dry_run: bool = Field(False, description="Preview operations without writing (for backfill)")
    continue_on_error: bool = Field(False, description="Continue on errors during backfill")
    max_errors: int = Field(0, description="Max errors before aborting backfill (0=unlimited)")
    auto_context: bool = Field(False, description="Infer org/project/repo/branch from JSON content during backfill")

    @model_validator(mode="after")
    def validate_config(self):  # type: ignore[override]
        if self.org:
            self.org = validate_org(self.org)
        if self.project:
            validate_target(self.project)
        for path in (self.output_path, self.backfill_from):
            if path:
                checked_path(path)
        # Mutually exclusive execution profiles
        if self.fast and self.full:
            raise ValueError("'fast' and 'full' are mutually exclusive")

        # Selection constraints
        if self.all_projects and self.project:
            raise ValueError("Cannot specify both 'project' and 'all_projects'")
        if not self.project and not self.all_projects and not self.backfill_from:
            raise ValueError("Must provide either 'project', 'all_projects', or 'backfill_from'")

        # Segments requires maturity
        if self.include_segments:
            self.include_maturity = True

        # Validation requirements
        if self.validate_calculations and (not self.write_db or not self.write_files):
            raise ValueError("validate_calculations requires both write_db and write_files")

        # Backfill implies write_db
        if self.backfill_from and not self.write_db:
            self.write_db = True

        return self
