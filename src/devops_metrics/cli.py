"""Azure DevOps Metrics CLI

This module provides the command-line interface for collecting and analyzing
Azure DevOps metrics.
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, cast, List, Optional

import typer
from rich.console import Console
from devops_metrics.types import RawData, ProjectAssessment, ExportOptions
from devops_metrics.backend.services.raw_data_collector import RawDataCollector
from devops_metrics.backend.services.raw_data_exporter import RawDataExporter
from devops_metrics.backend.services.maturity_assessment_exporter import MaturityAssessmentExporter
from devops_metrics.backend.services.raw_maturity_assessment import RawMaturityAssessmentService
from devops_metrics.backend.services.segment_maturity_assessment import SegmentMaturityAssessmentService
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from pydantic import SecretStr

from devops_metrics.config.settings import Settings
from devops_metrics.config.access import checked_path, validate_target
from devops_metrics.backend.api.ado_client import AzureDevOpsClient
from devops_metrics.backend.services.factory import MetricsServiceFactory
from devops_metrics.exporters.json_exporter import JsonExporter
from devops_metrics.utils.logging import get_logger, log_with_context
from devops_metrics.persistence.models import init_engine_and_tables
from devops_metrics.persistence.sinks import CompositeSink, FileSink, PostgresSink
from devops_metrics.validation.consistency import CalculationValidator
from devops_metrics import __version__ as TOOL_VERSION
from devops_metrics.api.models import CollectionConfig
from devops_metrics.runner import execute_collection_run

# Initialize logger
logger = get_logger(__name__)

# Initialize console
console = Console()

# Create Typer app
app = typer.Typer(
    name="ado-metrics",
    help="Azure DevOps Metrics Collection Tool",
    add_completion=False,
)

# Verbosity from ADO_VERBOSITY (quiet|normal|debug)
_VERBOSITY = os.environ.get("ADO_VERBOSITY", "normal").lower()

async def _list_projects(ado_client: AzureDevOpsClient) -> Table:
    """List all Azure DevOps projects.

    Args:
        ado_client: Azure DevOps client instance

    Returns:
        Rich table containing project information
    """
    projects = await ado_client.get_projects()

    # Create projects table
    table = Table(title="Azure DevOps Projects")
    table.add_column("Name", style="cyan")
    table.add_column("Description", style="green")
    table.add_column("Last Updated", style="blue")

    for project in projects:
        last_update = project.get("lastUpdateTime", "")
        if last_update:
            try:
                last_update = datetime.fromisoformat(last_update.replace("Z", "+00:00")).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            except (ValueError, AttributeError):
                last_update = "N/A"

        table.add_row(
            project.get("name", "Unknown"),
            project.get("description", "No description"),
            last_update or "N/A",
        )

    return table


async def list_projects_and_cleanup(ado_client: AzureDevOpsClient) -> Table:
    """List projects and ensure client cleanup.

    Args:
        ado_client: Azure DevOps client instance

    Returns:
        Rich table containing project information
    """
    try:
        table = await _list_projects(ado_client)
        return table
    finally:
        await ado_client.close()


@app.command()
def projects() -> None:
    """List all available Azure DevOps projects."""
    try:
        # Load settings
        settings = Settings(
            azure_devops_pat=SecretStr(os.getenv("ADO_AZURE_DEVOPS_PAT", "")),
            azure_devops_org=os.getenv("ADO_AZURE_DEVOPS_ORG", "")
        )

        # Check for required PAT and Org
        pat = os.getenv("ADO_AZURE_DEVOPS_PAT")
        org = os.getenv("ADO_AZURE_DEVOPS_ORG")
        if not pat or not org:
            console.print("\n❌ [red]Missing required environment variables.[/]\n")
            console.print("Set ADO_AZURE_DEVOPS_PAT and ADO_AZURE_DEVOPS_ORG before running this command.")
            raise typer.Exit(code=2)


        # Initialize Azure DevOps client
        ado_client = AzureDevOpsClient(settings)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task(description="Fetching projects...", total=None)
            table = asyncio.run(list_projects_and_cleanup(ado_client))

        console.print(table)

    except Exception as e:
        log_with_context(logger, logging.ERROR, f"Error listing projects: {str(e)}", error=str(e))
        console.print(f"\n❌ [red]Error: {str(e)}[/]")
        raise typer.Exit(code=1)


@app.command(help="Manage database migrations using Alembic")
def db_migrate(
    check_only: bool = typer.Option(False, "--check", help="Only check if migrations needed; exit 2 if up-to-date"),
    show_history: bool = typer.Option(False, "--history", help="Show migration history"),
    timeout: int = typer.Option(30, "--timeout", help="Database connection timeout (seconds)"),
) -> None:
    """Run pending migrations or show status/history."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        console.print("\n❌ [red]DATABASE_URL must be set for migration commands.[/]")
        raise typer.Exit(code=2)

    # Prefer programmatic helpers
    try:
        from scripts.migrate import (
            check_database_connectivity,
            get_pending_migrations,
            run_migrations,
            show_history as _show_history,
            show_status as _show_status,
        )
        if show_history:
            _show_history()
            raise typer.Exit(code=0)
        if not check_database_connectivity(database_url, timeout=timeout):
            console.print("\n❌ [red]Database not reachable.[/]")
            raise typer.Exit(code=1)
        if check_only:
            pending = get_pending_migrations(database_url)
            if not pending:
                raise typer.Exit(code=2)
            ok = run_migrations(database_url)
            raise typer.Exit(code=0 if ok else 1)
        # Default: run to head
        ok = run_migrations(database_url)
        raise typer.Exit(code=0 if ok else 1)
    except SystemExit as se:
        raise se
    except Exception:
        # Fallback to subprocess
        import subprocess, sys as _sys
        args = [
            _sys.executable,
            "scripts/migrate.py",
        ]
        if show_history:
            args.append("--history")
        if check_only:
            args.append("--check")
        try:
            rc = subprocess.call(args, env=os.environ.copy())
            raise typer.Exit(code=rc)
        except Exception as ex:  # noqa: BLE001
            console.print(f"\n❌ [red]Failed to run migration script: {ex}[/]")
            raise typer.Exit(code=1)


@app.command(help="Show database migration status")
def db_status() -> None:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        console.print("\n❌ [red]DATABASE_URL must be set for migration commands.[/]")
        raise typer.Exit(code=2)
    try:
        from scripts.migrate import show_status as _show_status
        rc = _show_status(database_url)
        raise typer.Exit(code=rc)
    except SystemExit as se:
        raise se
    except Exception:
        # Fallback
        import subprocess, sys as _sys
        try:
            rc = subprocess.call([_sys.executable, "scripts/migrate.py", "--status"], env=os.environ.copy())
            raise typer.Exit(code=rc)
        except Exception as ex:  # noqa: BLE001
            console.print(f"\n❌ [red]Failed to run migration status: {ex}[/]")
            raise typer.Exit(code=1)

@app.command(help="Collect raw data from Azure DevOps with optional maturity assessment.")
def collect_raw(
    project: str = typer.Option(None, "--project", "-p", help="Project to collect raw data from"),
    all_projects: bool = typer.Option(
        False, "--all-projects", "-a", help="Collect data from all available projects"
    ),
    output: Path = typer.Option(
        None,
        "--output",
        "-o",
        help="Path for the output files (both JSON and Excel)",
        dir_okay=False,
        file_okay=True,
        writable=True,
    ),
    include_maturity: bool = typer.Option(
        False, "--maturity", "-m", help="Include maturity assessment in the output"
    ),
    include_segments: bool = typer.Option(
        False,
        "--segments",
        "-s",
        help="Include segment-level maturity assessment (requires --maturity)",
    ),
    minimal: bool = typer.Option(
        False,
        "--minimal",
        help="Limit collection to 10 projects for testing and development purposes. Useful for quick testing, debugging, and verifying functionality without processing all projects.",
    ),
    write_files: bool = typer.Option(
        True, "--write-files/--no-write-files", help="Write outputs to local files (default: true)."
    ),
    write_db: bool = typer.Option(
        False, "--write-db", help="Write outputs to the database (default: false)."
    ),
    db_strict: bool = typer.Option(
        False, "--db-strict", help="Fail-fast if DB is requested but unreachable or misconfigured."
    ),
    db_reset: bool = typer.Option(
        False,
        "--db-reset",
        help="DANGEROUS: Clear runs/raw_metrics/metrics in DB before run and collect full calendar year.",
    ),
    org: Optional[str] = typer.Option(None, "--org", help="Organization identifier"),
    repo: Optional[str] = typer.Option(None, "--repo", help="Repository name"),
    branch: Optional[str] = typer.Option(None, "--branch", help="Branch name"),
    commit_sha: Optional[str] = typer.Option(None, "--commit-sha", help="Commit SHA associated with this run"),
    run_label: Optional[str] = typer.Option(None, "--run-label", help="Human-friendly label or tag for this run"),
    backfill_from: Optional[Path] = typer.Option(None, "--backfill-from", help="Path to directory containing historical JSON exports to ingest into DB (implies --write-db)."),
    validate_calculations: bool = typer.Option(False, "--validate-calculations", help="Validate Excel vs DB calculations after run (requires --write-db and --write-files)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview operations without writing to sinks (primarily for --backfill-from)."),
    continue_on_error: bool = typer.Option(False, "--continue-on-error", help="Continue processing on errors during backfill."),
    max_errors: int = typer.Option(0, "--max-errors", help="Maximum errors before aborting during backfill (0 = unlimited)."),
    auto_context: bool = typer.Option(False, "--auto-context", help="Infer org/project/repo/branch/commit_sha from JSON content where possible."),
    fast: bool = typer.Option(False, "--fast", help="Optimized run: limit projects (like --minimal), use delta-mode and skip stale repos"),
    full: bool = typer.Option(False, "--full", help="Deep run: disable delta-mode and repo skipping"),
) -> None:
    """Collect raw data from Azure DevOps with optional maturity assessment."""
    start_time = time.time()
    try:
        # Load settings (CLI --org overrides env)
        env_pat = os.getenv("ADO_AZURE_DEVOPS_PAT", "")
        env_org = os.getenv("ADO_AZURE_DEVOPS_ORG", "")
        effective_org = org or env_org
        settings = Settings(
            azure_devops_pat=SecretStr(env_pat),
            azure_devops_org=effective_org
        )

        if project:
            validate_target(project)
        if output:
            checked_path(output)
        if backfill_from:
            checked_path(backfill_from)

        # Check for required auth details
        if not env_pat or not effective_org:
            console.print("\n❌ [red]Missing required authentication/organization details.[/]\n")
            console.print("Ensure ADO_AZURE_DEVOPS_PAT is set, and provide --org or set ADO_AZURE_DEVOPS_ORG.")
            raise typer.Exit(code=2)

        if not project and not all_projects:
            console.print("\n❌ [red]You must provide either --project/-p or --all-projects/-a.[/]\n")
            console.print("Example: python -m devops_metrics.cli collect-raw --all-projects --minimal")
            raise typer.Exit(code=2)

        # Validate CLI argument combinations
        if validate_calculations:
            if not write_db or not write_files:
                console.print("\n❌ [red]--validate-calculations requires BOTH --write-db and --write-files.[/]")
                raise typer.Exit(code=2)

        # Check for mutually exclusive presets
        if fast and full:
            console.print("\n❌ [red]--fast and --full are mutually exclusive.[/]")
            raise typer.Exit(code=2)

        # Initialize Azure DevOps client
        ado_client = AzureDevOpsClient(settings)

        # Set up output paths
        if not output:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            project_suffix = f"_{project}" if project else ""
            # Create timestamped directory
            output_dir = Path(f"exports/{timestamp}")
            output = output_dir / f"metrics{project_suffix}"

        # Create output directory if needed
        output.parent.mkdir(parents=True, exist_ok=True)

        # Segments require maturity assessment
        if include_segments:
            include_maturity = True

        results_container: Dict[str, Any] = {}

        # Backfill mode: ingest historical JSON
        if backfill_from is not None:
            if not write_db:
                console.print("\n⚠️ [yellow]--backfill-from implies --write-db; proceeding with DB sink.[/]")
            database_url = os.getenv("DATABASE_URL")
            if not database_url:
                msg = "DATABASE_URL must be set for backfill (--backfill-from)"
                if db_strict:
                    console.print(f"\n❌ [red]{msg}[/]")
                    raise typer.Exit(code=2)
                else:
                    console.print(f"\n⚠️ [yellow]{msg}. Aborting backfill.[/]")
                    return
            try:
                # Always require Alembic-managed schema; do not auto-create tables
                engine = init_engine_and_tables(database_url, allow_create_all=False)
                sink = PostgresSink(engine)
            except Exception as ex:
                if db_strict:
                    console.print(f"\n❌ [red]Failed to connect/init DB: {ex}[/]")
                    raise typer.Exit(code=2)
                else:
                    console.print(f"\n⚠️ [yellow]DB init failed: {ex}. Aborting backfill.[/]")
                    return

            total_files = 0
            ingested = 0
            errors = 0
            for json_file in Path(backfill_from).rglob("*.json"):
                checked_path(json_file)
                total_files += 1
                try:
                    with open(json_file, "r") as f:
                        data = json.load(f)
                    results_container["raw_ado_data"] = data

                    # Infer executed_at from JSON, filename, or mtime
                    from devops_metrics.utils.time import parse_ts
                    executed_at = None

                    # 1) JSON content timestamps
                    try:
                        if isinstance(data, dict):
                            # builds.finishTime / queueTime
                            if "builds" in data and isinstance(data["builds"], list):
                                times = []
                                for b in data["builds"]:
                                    for key in ("finishTime", "queueTime"):
                                        v = b.get(key)
                                        if v:
                                            try:
                                                times.append(parse_ts(v))
                                            except Exception:
                                                pass
                                if times:
                                    executed_at = max(times)
                            # pipelines.completedDate
                            if executed_at is None and "pipelines" in data and isinstance(data["pipelines"], list):
                                times = []
                                for p in data["pipelines"]:
                                    v = p.get("completedDate")
                                    if v:
                                        try:
                                            times.append(parse_ts(v))
                                        except Exception:
                                            pass
                                if times:
                                    executed_at = max(times)
                            # pull_requests.closedDate / creationDate
                            if executed_at is None and "pull_requests" in data and isinstance(data["pull_requests"], list):
                                times = []
                                for pr in data["pull_requests"]:
                                    for key in ("closedDate", "creationDate"):
                                        v = pr.get(key)
                                        if v:
                                            try:
                                                times.append(parse_ts(v))
                                            except Exception:
                                                pass
                                if times:
                                    executed_at = max(times)
                    except Exception:
                        # ignore JSON parsing issues for timestamp inference
                        executed_at = None

                    # 2) Folder pattern YYYYMMDD_HHMMSS
                    if executed_at is None:
                        for parent in json_file.parents:
                            name = parent.name
                            if len(name) >= 15 and name[:8].isdigit() and name[8] == "_" and name[9:15].isdigit():
                                try:
                                    executed_at = datetime.strptime(name[:15], "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
                                    break
                                except Exception:
                                    pass

                    # 2.5) File name timestamp patterns (metrics_YYYYMMDD_HHMMSS.json, export_YYYY-MM-DD.json, YYYYMMDD.json)
                    if executed_at is None:
                        stem = json_file.stem
                        try:
                            # Try YYYYMMDD_HHMMSS
                            if len(stem) >= 15 and stem[-15:-7].isdigit() and stem[-7] == "_" and stem[-6:].isdigit():
                                ts = stem[-15:]
                                executed_at = datetime.strptime(ts, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
                            # Try YYYYMMDD at end
                            elif len(stem) >= 8 and stem[-8:].isdigit():
                                ts = stem[-8:]
                                executed_at = datetime.strptime(ts, "%Y%m%d").replace(tzinfo=timezone.utc)
                            # Try YYYY-MM-DD anywhere
                            else:
                                import re
                                m = re.search(r"(20\d{2})-(\d{2})-(\d{2})", stem)
                                if m:
                                    executed_at = datetime.strptime(m.group(0), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                        except Exception:
                            pass

                    # 3) File mtime
                    if executed_at is None:
                        try:
                            executed_at = datetime.fromtimestamp(json_file.stat().st_mtime, tz=timezone.utc)
                        except Exception:
                            executed_at = datetime.now(timezone.utc)

                    # Infer context (org, project, etc.) from JSON
                    inferred_org = None
                    inferred_project = None
                    inferred_repo = None
                    inferred_branch = None
                    inferred_commit = None
                    if auto_context and isinstance(data, dict):
                        try:
                            from urllib.parse import urlparse, unquote
                            # Try builds repository URL
                            if "builds" in data and isinstance(data["builds"], list):
                                for b in data["builds"]:
                                    repo_obj = b.get("repository") or {}
                                    url = repo_obj.get("url") or repo_obj.get("webUrl") or repo_obj.get("remoteUrl")
                                    if url:
                                        parsed = urlparse(url)
                                        parts = [unquote(p) for p in parsed.path.strip("/").split("/")]
                                        # /ORG/PROJECT/_git/REPO
                                        if len(parts) >= 4 and parts[2] == "_git":
                                            inferred_org = parts[0]
                                            inferred_project = parts[1]
                                            inferred_repo = parts[3]
                                    # Branch and commit
                                    sb = b.get("sourceBranch")
                                    if isinstance(sb, str):
                                        inferred_branch = sb[11:] if sb.startswith("refs/heads/") else sb
                                    sv = b.get("sourceVersion")
                                    if sv:
                                        inferred_commit = sv
                                    if inferred_org or inferred_project or inferred_repo or inferred_branch or inferred_commit:
                                        break
                            # Try pull request repo URL/branch/commit
                            if (not inferred_org or not inferred_project or not inferred_repo) and "pull_requests" in data and isinstance(data["pull_requests"], list):
                                for pr in data["pull_requests"]:
                                    repo_obj = pr.get("repository") or {}
                                    url = repo_obj.get("webUrl") or repo_obj.get("url") or repo_obj.get("remoteUrl")
                                    if url:
                                        parsed = urlparse(url)
                                        parts = [unquote(p) for p in parsed.path.strip("/").split("/")]
                                        if len(parts) >= 4 and parts[2] == "_git":
                                            inferred_org = inferred_org or parts[0]
                                            inferred_project = inferred_project or parts[1]
                                            inferred_repo = inferred_repo or parts[3]
                                    trg = pr.get("targetRefName")
                                    if isinstance(trg, str) and not inferred_branch:
                                        inferred_branch = trg[11:] if trg.startswith("refs/heads/") else trg
                                    lmc = pr.get("lastMergeCommit") or {}
                                    if not inferred_commit and isinstance(lmc, dict):
                                        inferred_commit = lmc.get("commitId") or inferred_commit
                                    if inferred_org or inferred_project or inferred_repo or inferred_branch or inferred_commit:
                                        break
                            # Try pipelines
                            if (not inferred_org or not inferred_project or not inferred_repo) and "pipelines" in data and isinstance(data["pipelines"], list):
                                for p in data["pipelines"]:
                                    repo_obj = p.get("repository") or {}
                                    url = repo_obj.get("webUrl") or repo_obj.get("url") or repo_obj.get("remoteUrl")
                                    if url:
                                        parsed = urlparse(url)
                                        parts = [unquote(p) for p in parsed.path.strip("/").split("/")]
                                        if len(parts) >= 4 and parts[2] == "_git":
                                            inferred_org = inferred_org or parts[0]
                                            inferred_project = inferred_project or parts[1]
                                            inferred_repo = inferred_repo or parts[3]
                                    if inferred_org or inferred_project or inferred_repo:
                                        break
                            # Try top-level project metadata
                            if not inferred_project and isinstance(data.get("project"), dict):
                                inferred_project = data["project"].get("name") or inferred_project
                            # Try repositories map -> project names
                            if not inferred_project and isinstance(data.get("repositories"), list) and data["repositories"]:
                                first = data["repositories"][0]
                                if isinstance(first, dict) and isinstance(first.get("project"), dict):
                                    inferred_project = first["project"].get("name") or inferred_project
                        except Exception:
                            # best-effort inference; ignore failures
                            pass

                    # Build run context (CLI > inferred > env)
                    effective_run_context = {
                        "org": (org or inferred_org or effective_org),
                        "project": project or inferred_project,
                        "repo": repo or inferred_repo,
                        "branch": branch or inferred_branch,
                        "commit_sha": commit_sha or inferred_commit,
                        "run_label": run_label or json_file.parent.name,
                        "environment": (os.getenv("ADO_RUN_ENV") or os.getenv("RUN_ENV") or "local"),
                        "tool_version": TOOL_VERSION,
                        "calculation_version": None,
                        "executed_at": executed_at,
                    }

                    # Derive metrics from backfill data if possible
                    calculated_metrics: List[Dict[str, Any]] = []
                    try:
                        # If this file already contains segment_scores (as produced by our exporter), map them
                        seg_scores = None
                        if isinstance(data, dict) and "segment_scores" in data and isinstance(data["segment_scores"], dict):
                            seg_scores = data["segment_scores"]
                        # Some exports might nest scores under a top-level key
                        if seg_scores is None and isinstance(data, dict):
                            # e.g., { "segment_scores": {...} } already handled; else look for a sibling container
                            for k, v in data.items():
                                if isinstance(v, dict) and "segment_scores" in v and isinstance(v["segment_scores"], dict):
                                    seg_scores = v["segment_scores"]
                                    break
                        if seg_scores:
                            for seg_name, seg_data in seg_scores.items():
                                try:
                                    maturity_score = float(seg_data.get("maturity_score", 0.0))
                                except Exception:
                                    maturity_score = None  # type: ignore[assignment]
                                calculated_metrics.append(
                                    {
                                        "scope": seg_name,
                                        "metric_key": "maturity_score",
                                        "value_float": maturity_score,
                                        "status": None,
                                        "observed_at": executed_at,
                                    }
                                )
                        # If this file includes a raw_maturity snapshot, aggregate common metrics via helper
                        from devops_metrics.utils.aggregation import aggregate_common_metrics_from_raw_maturity_container
                        agg = aggregate_common_metrics_from_raw_maturity_container(data)
                        for key, val in (agg or {}).items():
                            if val is None:
                                continue
                            calculated_metrics.append(
                                {
                                    "scope": None,
                                    "metric_key": key,
                                    "value_float": float(val),
                                    "observed_at": executed_at,
                                }
                            )
                    except Exception:
                        # best-effort; ignore mapping errors
                        pass

                    # Log structured summary for each file
                    ctx_str = f"{(org or inferred_org or effective_org)}/{project or inferred_project}/{repo or inferred_repo}"
                    if branch or inferred_branch:
                        ctx_str += f"@{branch or inferred_branch}"
                    summary = (
                        f"File: {json_file} | executed_at: {executed_at.isoformat()} | "
                        f"context: {ctx_str} | run_label: {run_label or json_file.parent.name} | "
                        f"calculated_metrics: {len(calculated_metrics)}"
                    )
                    # Print summary for traceability
                    console.print(f"📄 {summary}")

                    if dry_run:
                        ingested += 1  # counted as would-process
                        continue

                    sink.persist(
                        {
                            "raw_ado_data": results_container.get("raw_ado_data"),
                            "calculated_metrics": calculated_metrics,
                        },
                        effective_run_context,
                    )
                    ingested += 1
                except Exception as ex:
                    errors += 1
                    console.print(f"⚠️ [yellow]Failed to ingest {json_file}: {ex}[/]")
                    if not continue_on_error:
                        raise
                    if max_errors and errors >= max_errors:
                        console.print(f"\n❌ [red]Aborting backfill after reaching max-errors={max_errors}[/]")
                        break
                    continue

            if dry_run:
                console.print(f"\n🧪 [green]Backfill DRY-RUN complete.[/] Files discovered: {total_files}, would process: {ingested}, errors: {errors}")
            else:
                console.print(f"\n🗄️ [green]Backfill complete.[/] Files discovered: {total_files}, ingested: {ingested}, errors: {errors}")
            return

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:

            async def collect_data() -> None:
                # Create services
                # Preset semantics
                effective_minimal = minimal or fast
                enable_delta = not full
                skip_stale = not full
                force_year_window = False

                if db_reset:
                    enable_delta = False
                    force_year_window = True

                raw_collector = cast(
                    RawDataCollector,
                    MetricsServiceFactory.create_service(
                        "raw",
                        ado_client,
                        minimal=effective_minimal,
                        enable_delta=enable_delta,
                        skip_stale_repos=skip_stale,
                        force_year_window=force_year_window,
                    ),
                )
                exporter = cast(RawDataExporter, MetricsServiceFactory.create_service("export", ado_client))
                maturity_service = cast(RawMaturityAssessmentService, MetricsServiceFactory.create_service(
                    "raw_maturity",
                    ado_client,
                    current_month_weight=settings.current_month_weight,
                    historical_weight=settings.historical_weight
                ))
                segment_service = cast(SegmentMaturityAssessmentService, MetricsServiceFactory.create_service("segment_maturity", ado_client))

                # Collect raw data
                task = progress.add_task(description="Collecting raw data...", total=None)
                # Heartbeat progress updater (normal/debug only)
                from asyncio import Event
                done_event: Event = Event()

                async def heartbeat() -> None:
                    if _VERBOSITY not in ("normal", "debug"):
                        return
                    try:
                        while not done_event.is_set():
                            s = getattr(ado_client, "stats", {}) or {}
                            pages = s.get("pages", 0)
                            prs = s.get("prs", 0)
                            builds_cnt = s.get("builds", 0)
                            desc = f"Collecting raw data... pages={pages} prs={prs} builds={builds_cnt}"
                            try:
                                progress.update(task, description=desc)
                            except Exception:
                                pass
                            await asyncio.sleep(1.0 if _VERBOSITY == "debug" else 2.0)
                    except asyncio.CancelledError:
                        return

                hb_task = asyncio.create_task(heartbeat())

                raw_data_result = await raw_collector.collect_all_projects_raw_data() if all_projects else await raw_collector.collect_all_raw_data(project)
                raw_data_dict: Dict[str, Any] = dict(raw_data_result) # Convert TypedDict to Dict[str, Any]
                progress.update(task, completed=True)

                # Stop progress heartbeat
                done_event.set()
                try:
                    hb_task.cancel()
                except Exception:
                    pass

                # Ensure 'projects' key exists and is a list
                if "projects" not in raw_data_dict or not isinstance(raw_data_dict["projects"], list):
                    raw_data_dict["projects"] = cast(List[Dict[str, Any]], []) # Explicitly cast to List[Dict[str, Any]]
                
                # If collecting for a single project and 'projects' is empty, add it
                if project and not raw_data_dict["projects"]:
                    try:
                        project_data = await ado_client.get_project(project)
                        if project_data:
                            raw_data_dict["projects"].append({
                                "id": project_data.get("id"),
                                "name": project_data.get("name", "Unknown")
                            })
                    except Exception as e:
                        logger.error(f"Error collecting project data for project {project}: {str(e)}")
                        raise

                results_container["raw_ado_data"] = raw_data_dict

                # Set output base path for sinks
                results_container["file_output_base"] = str(output)

                # Run maturity assessment if requested
                if include_maturity:
                    task = progress.add_task(
                        description="Calculating maturity assessment...", total=None
                    )
                    
                    maturity_data = maturity_service.assess_raw_data(cast(RawData, raw_data_dict)) # Keep this cast, as RawData is a TypedDict
                    # Add project name to maturity data at the top level, if it's a dict
                    if isinstance(maturity_data, dict) and "project_name" not in maturity_data:
                        # Use the project name if provided, otherwise default to "Unknown" from raw_data_dict
                        maturity_data["project_name"] = project or (raw_data_dict["projects"][0]["name"] if raw_data_dict.get("projects") and raw_data_dict["projects"] else "Unknown")

                    progress.update(task, completed=True)

                    # Export maturity data (if write_files)
                    # Expose maturity for sinks
                    results_container["maturity_data"] = cast(Dict[str, Any], maturity_data)

                    # Derive aggregated calculated metrics (build_success_rate, coverage)
                    try:
                        repos = maturity_data.get("repositories", {}) if isinstance(maturity_data, dict) else {}
                        if isinstance(repos, dict) and repos:
                            bsr_vals: List[float] = []
                            cov_vals: List[float] = []
                            for _rid, rdata in repos.items():
                                qa = (rdata or {}).get("quality_assurance") or {}
                                m = qa.get("metrics") or {}
                                bsr = m.get("build_success_rate")
                                cov = m.get("code_coverage")
                                if isinstance(bsr, (int, float)):
                                    bsr_vals.append(float(bsr))
                                if isinstance(cov, (int, float)):
                                    cov_vals.append(float(cov))
                            def _mean(xs: List[float]) -> Optional[float]:
                                return sum(xs) / len(xs) if xs else None
                            calc_list: List[Dict[str, Any]] = results_container.get("calculated_metrics") or []
                            agg_bsr = _mean(bsr_vals)
                            agg_cov = _mean(cov_vals)
                            if agg_bsr is not None:
                                calc_list.append({
                                    "scope": None,
                                    "metric_key": "build_success_rate",
                                    "value_float": agg_bsr,
                                })
                            if agg_cov is not None:
                                calc_list.append({
                                    "scope": None,
                                    "metric_key": "coverage",
                                    "value_float": agg_cov,
                                })
                            if calc_list:
                                results_container["calculated_metrics"] = calc_list
                    except Exception:
                        # Best-effort aggregation; ignore if structure differs
                        pass

                # Run segment-level assessment if requested
                if include_segments:
                    task = progress.add_task(
                        description="Calculating segment maturity assessment...", total=None
                    )

                    # Get group entitlements
                    group_entitlements = await segment_service.get_group_entitlements()
                    if not group_entitlements:
                        logger.warning("No security group mappings found")
                        raise typer.Exit(code=1)

                    # Calculate project scores
                    logger.info(
                        f"Calculating project maturity scores from maturity data with {len(maturity_data.get('repositories', []))} repositories"
                    )
                    project_scores = segment_service.calculate_project_maturity(
                        cast(Dict[str, Any], maturity_data) # Cast to Dict[str, Any] as expected by calculate_project_maturity
                    )
                    logger.info(
                        f"Calculated project scores for {len(project_scores)} projects"
                    )

                    # Calculate segment scores
                    logger.info(
                        f"Calculating segment scores from {len(group_entitlements)} group entitlements"
                    )
                    segment_scores = segment_service.calculate_segment_maturity(
                        project_scores, group_entitlements
                    )
                    logger.info(f"Calculated scores for {len(segment_scores)} segments")

                    # Expose segment scores for downstream persistence/validation
                    results_container["segment_scores"] = segment_scores

                    # Map segment maturity into calculated_metrics for DB persistence
                    calculated_metrics: List[Dict[str, Any]] = []
                    now_utc = datetime.now(timezone.utc)
                    for seg_name, seg_data in segment_scores.items():
                        try:
                            maturity_score = float(seg_data.get("maturity_score", 0.0))
                        except Exception:
                            maturity_score = None  # type: ignore[assignment]
                        calculated_metrics.append({
                            "scope": seg_name,
                            "metric_key": "maturity_score",
                            "value_float": maturity_score,
                            "status": None,
                            "observed_at": now_utc,
                        })
                    results_container["calculated_metrics"] = calculated_metrics

                    # Expose segment outputs for sinks
                    results_container["project_scores"] = project_scores
                    results_container["segment_scores"] = segment_scores
                    progress.update(task, completed=True)

            # Run collection
            try:
                asyncio.run(collect_data())
            finally:
                # Cleanup ADO client sessions
                if "ado_client" in locals():
                    asyncio.run(ado_client.close())

        # Persist to files if DB is not used
        if not write_db and write_files:
            fs = FileSink()
            run_context_for_files = {
                        "org": effective_org,

                "project": project,
                "repo": repo,
                "branch": branch,
                "commit_sha": commit_sha,
                "run_label": run_label,
                "environment": (os.getenv("ADO_RUN_ENV") or os.getenv("RUN_ENV") or "local"),
                "tool_version": TOOL_VERSION,
                "calculation_version": None,
            }
            file_payload = {
                "raw_ado_data": results_container.get("raw_ado_data"),
                "file_output_base": str(output),
                "maturity_data": results_container.get("maturity_data"),
                "project_scores": results_container.get("project_scores"),
                "segment_scores": results_container.get("segment_scores"),
            }
            fs.persist(file_payload, run_context_for_files)

        # Persist to database if requested
        if write_db:
            database_url = os.getenv("DATABASE_URL")
            if not database_url:
                msg = "DATABASE_URL must be set when using --write-db"
                if db_strict:
                    console.print(f"\n❌ [red]{msg}[/]")
                    raise typer.Exit(code=2)
                else:
                    console.print(f"\n⚠️ [yellow]{msg}. Continuing without DB.[/]")
            else:
                try:
                    # Always require Alembic-managed schema; do not auto-create tables
                    engine = init_engine_and_tables(database_url, allow_create_all=False)
                    # Optional destructive reset before writing
                    if db_reset:
                        import sys
                        from sqlalchemy import text
                        env_run = (os.getenv("ADO_RUN_ENV") or os.getenv("RUN_ENV") or "").lower()
                        if env_run not in {"local", "dev"}:
                            console.print("\n❌ [red]Refusing to reset DB outside local/dev environment. Set RUN_ENV=local or ADO_RUN_ENV=local to proceed.[/]\n")
                            raise typer.Exit(code=2)
                        if sys.stdout.isatty():
                            proceed = typer.confirm("This will CLEAR runs/raw_metrics/metrics. Continue?", default=False)
                            if not proceed:
                                raise typer.Exit(code=1)
                        with engine.begin() as conn:
                            dialect = conn.dialect.name
                            if dialect == "postgresql":
                                conn.execute(text("TRUNCATE TABLE metrics, raw_metrics, runs RESTART IDENTITY CASCADE;"))
                            else:
                                conn.execute(text("DELETE FROM metrics;"))
                                conn.execute(text("DELETE FROM raw_metrics;"))
                                conn.execute(text("DELETE FROM runs;"))
                        console.print("\n🧹 [yellow]Database reset complete (tables cleared).[/]\n")
                    # Build sinks (DB plus optional FileSink) and persist via CompositeSink
                    sinks: List[Any] = []
                    if write_files:
                        sinks.append(FileSink())
                    sinks.append(PostgresSink(engine))
                    composite = CompositeSink(sinks)
                    run_context = {
                "org": effective_org,

                        "project": project,
                        "repo": repo,
                        "branch": branch,
                        "commit_sha": commit_sha,
                        "run_label": run_label,
                        "environment": (os.getenv("ADO_RUN_ENV") or os.getenv("RUN_ENV") or "local"),
                        "tool_version": TOOL_VERSION,
                        "calculation_version": None,
                    }
                    payload: Dict[str, Any] = {
                        "raw_ado_data": results_container.get("raw_ado_data"),
                        "file_output_base": str(output),
                        "maturity_data": results_container.get("maturity_data"),
                        "project_scores": results_container.get("project_scores"),
                        "segment_scores": results_container.get("segment_scores"),
                        "calculated_metrics": results_container.get("calculated_metrics") or [],
                    }
                    composite.persist(payload, run_context)
                    console.print("\n🗄️ [green]Persisted data to database.[/]")

                    # Run validation if requested
                    if validate_calculations:
                        validator = CalculationValidator(database_url)
                        seg_scores = results_container.get("segment_scores") or {}
                        last_run_id = getattr(composite, "last_run_id", None)
                        if last_run_id is not None:
                            vr = validator.validate_segments_by_run_id(last_run_id, seg_scores)
                        else:
                            vr = validator.validate_segments(run_context, seg_scores)
                        # Validate additional metrics from artifacts if available
                        try:
                            seg_json_base = str(output)
                            vr += validator.validate_from_artifacts(run_context, seg_json_base)
                        except Exception:
                            pass
                        inconsistencies = [r for r in vr if not r.is_consistent]
                        if inconsistencies:
                            console.print(f"\n❌ [red]VALIDATION FAILED: {len(inconsistencies)} inconsistencies found.[/]")
                            for r in inconsistencies[:5]:
                                console.print(f"  {r.key}: Excel={r.excel_value}, DB={r.db_value} diff={r.diff_percentage}%")
                            raise typer.Exit(code=3)
                        else:
                            console.print("\n✅ [green]VALIDATION PASSED: Excel and database calculations are consistent.[/]")
                except Exception as ex:
                    if db_strict:
                        console.print(f"\n❌ [red]Failed to connect/init DB: {ex}[/]")
                        raise typer.Exit(code=2)
                    else:
                        console.print(f"\n⚠️ [yellow]DB init failed: {ex}. Continuing without DB.[/]")
        console.print(f"\n✨ [green]Data collection complete![/]\n")
        
        if write_files:
            console.print(f"📊 Raw data JSON: [blue]{output.with_suffix('.json')}[/]")
            console.print(f"📊 Raw data Excel: [blue]{output.with_suffix('.xlsx')}[/]")

        if write_files and include_maturity:
            console.print(f"📊 Maturity assessment JSON: [blue]{output.with_name(f'{output.stem}_maturity_assessment').with_suffix('.json')}[/]")
            console.print(f"📊 Maturity assessment Excel: [blue]{output.with_name(f'{output.stem}_maturity_assessment').with_suffix('.xlsx')}[/]")
        
        if write_files and include_segments:
            console.print(f"📊 Segment maturity JSON: [blue]{output.with_name(f'{output.stem}_segments').with_suffix('.json')}[/]")
            console.print(f"📊 Segment maturity Excel: [blue]{output.with_name(f'{output.stem}_segments').with_suffix('.xlsx')}[/]")

        # Display total run time
        end_time = time.time()
        duration = timedelta(seconds=int(end_time - start_time))
        console.print(f"\n⏱️ Total run time: [blue]{duration}[/]")

    except Exception as e:
        log_with_context(
            logger, logging.ERROR, f"Error collecting raw data: {str(e)}", error=str(e)
        )
        console.print(f"\n❌ [red]Error: {str(e)}[/]")
        raise typer.Exit(code=1)


@app.command(help="Collect via shared runner (API-parity)")
def collect_raw_runner(
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Project to collect raw data from"),
    all_projects: bool = typer.Option(False, "--all-projects", "-a", help="Collect data from all available projects"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Path base for output files"),
    include_maturity: bool = typer.Option(False, "--maturity", "-m", help="Include maturity assessment"),
    include_segments: bool = typer.Option(False, "--segments", "-s", help="Include segment-level maturity (requires --maturity)"),
    minimal: bool = typer.Option(False, "--minimal", help="Limit run for testing"),
    fast: bool = typer.Option(False, "--fast", help="Optimized run: limit projects, delta-mode, skip stale repos"),
    full: bool = typer.Option(False, "--full", help="Deep run: disable delta-mode and skipping"),
    write_files: bool = typer.Option(True, "--write-files/--no-write-files", help="Write outputs to local files"),
    write_db: bool = typer.Option(False, "--write-db", help="Write outputs to DB"),
    db_strict: bool = typer.Option(False, "--db-strict", help="Fail-fast if DB requested but unavailable"),
    db_reset: bool = typer.Option(False, "--db-reset", help="DANGEROUS: Clear DB tables before run"),
    run_label: Optional[str] = typer.Option(None, "--run-label", help="Human-friendly label"),
    org: Optional[str] = typer.Option(None, "--org", help="Organization identifier"),
    repo: Optional[str] = typer.Option(None, "--repo", help="Repository name"),
    branch: Optional[str] = typer.Option(None, "--branch", help="Branch name"),
    commit_sha: Optional[str] = typer.Option(None, "--commit-sha", help="Commit SHA"),
    validate_calculations: bool = typer.Option(False, "--validate-calculations", help="Validate Excel vs DB calculations"),
    backfill_from: Optional[Path] = typer.Option(None, "--backfill-from", help="Path to directory with historical JSON exports"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview operations without writing (for backfill)"),
    continue_on_error: bool = typer.Option(False, "--continue-on-error", help="Continue on errors during backfill"),
    max_errors: int = typer.Option(0, "--max-errors", help="Max backfill errors before abort (0=unlimited)"),
    auto_context: bool = typer.Option(False, "--auto-context", help="Infer org/project/repo/branch from JSON content during backfill"),
) -> None:
    """Runner-backed collection with full API parity."""
    try:
        env_pat = os.getenv("ADO_AZURE_DEVOPS_PAT", "")
        env_org = os.getenv("ADO_AZURE_DEVOPS_ORG", "")
        effective_org = org or env_org
        settings = Settings(azure_devops_pat=SecretStr(env_pat), azure_devops_org=effective_org)

        if not env_pat or not effective_org:
            console.print("\n❌ [red]Missing required authentication/organization details.[/]\n")
            console.print("Ensure ADO_AZURE_DEVOPS_PAT is set, and provide --org or set ADO_AZURE_DEVOPS_ORG.")
            raise typer.Exit(code=2)

        cfg = CollectionConfig(
            org=org,
            project=project,
            all_projects=all_projects,
            minimal=minimal,
            fast=fast,
            full=full,
            write_files=write_files,
            write_db=write_db,
            output_path=str(output) if output else None,
            include_maturity=include_maturity,
            include_segments=include_segments,
            run_label=run_label,
            repo=repo,
            branch=branch,
            commit_sha=commit_sha,
            db_strict=db_strict,
            db_reset=db_reset,
            validate_calculations=validate_calculations,
            backfill_from=str(backfill_from) if backfill_from else None,
            dry_run=dry_run,
            continue_on_error=continue_on_error,
            max_errors=max_errors,
            auto_context=auto_context,
        )

        result = asyncio.run(execute_collection_run(cfg, settings))
        status = result.get("status")
        rid = result.get("id")
        console.print(f"\n▶️  Runner started: id={rid} status={status}")
        if result.get("error"):
            console.print(f"❌ [red]Error: {result['error']}[/]")
            raise typer.Exit(code=1)
        console.print("✅ [green]Run finished successfully[/]")
        if result.get("artifacts_dir"):
            console.print(f"📁 Artifacts: [blue]{result['artifacts_dir']}[/]")
    except Exception as e:
        log_with_context(logger, logging.ERROR, f"Runner error: {str(e)}", error=str(e))
        console.print(f"\n❌ [red]Error: {str(e)}[/]")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
