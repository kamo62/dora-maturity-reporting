"""Raw Data Collection Service

This module handles collecting raw data directly from Azure DevOps API endpoints
without processing or transforming the responses. Supports both single-project
and all-projects collection modes.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any, Optional, TypedDict
import asyncio

from ...backend.api.ado_client import AzureDevOpsClient
from ...utils.logging import get_logger, log_with_context
from ...utils.time import parse_ts
# Lazy import DB modules in _get_last_run_time to avoid SQLModel redefinition issues in tests.
from ...types.data_types import (
    RepositoryData,
    create_project_info,
    merge_project_info,
)

logger = get_logger(__name__)


class BuildData(TypedDict):
    """Type definition for build data"""
    coverage: Dict[str, Any]
    tests: Dict[str, Any]
    security_scans: List[Dict[str, Any]] # Security scan results


class ConsolidatedData(TypedDict):
    """Type definition for consolidated data from all projects"""
    projects: List[Dict[str, Any]]
    repositories: List[Dict[str, Any]]
    repository_data: Dict[str, RepositoryData]
    builds: List[Dict[str, Any]]
    build_data: Dict[str, BuildData]
    project: Dict[str, Any]  # Root-level project info


class RawProjectData(TypedDict):
    """Type definition for raw project data"""
    project: Dict[str, Any]
    repositories: List[Dict[str, Any]]
    repository_data: Dict[str, RepositoryData]
    builds: List[Dict[str, Any]]
    build_data: Dict[str, BuildData]


from .base_service import MetricsService


class RawDataCollector(MetricsService):
    """Collects raw data from Azure DevOps API endpoints

    This collector supports two modes of operation:
    1. Single project collection via collect_all_raw_data()
    2. All projects collection via collect_all_projects_raw_data()

    Both modes maintain the same output data structure for consistency.
    """

    async def collect_metrics(self) -> Dict[str, Any]:
        """Collect metrics from Azure DevOps"""
        data: ConsolidatedData = await self.collect_all_projects_raw_data()
        # Convert TypedDict to dict to match base class
        return {k: v for k, v in data.items()}

    async def get_cached_metrics(self) -> Optional[Dict[str, Any]]:
        """Get metrics from cache if available"""
        return None

    def __init__(
        self,
        ado_client: AzureDevOpsClient,
        window_days: int = 180,
        minimal: bool = False,
        activity_window_days: int = 90,
        enable_delta: bool = True,
        skip_stale_repos: bool = True,
        force_year_window: bool = False,
    ):
        """Initialize the collector with an ADO client

        Args:
            ado_client: Initialized Azure DevOps client
            window_days: Number of days of historical data to collect (default: 180)
                        This window affects all metrics including builds, PRs, and commits.
                        A 180-day (6 month) window provides sufficient historical data
                        for accurate time-weighted scoring.
            minimal: If True, limit collection to 10 projects for testing purposes
            activity_window_days: Number of days to look back for determining if a repository
                                is active (default: 90). Repositories with no activity in this
                                period will be excluded from detailed data collection.
        """
        super().__init__(ado_client)
        self.window_days = window_days
        self.minimal = minimal
        self.activity_window_days = activity_window_days
        self.enable_delta = enable_delta
        self.skip_stale_repos = skip_stale_repos
        # Use ISO strings for logs, but datetime objects for comparisons
        now_utc = datetime.now(timezone.utc)
        # Default to N-day window unless calendar year is forced
        if force_year_window:
            jan1 = datetime(now_utc.year, 1, 1, tzinfo=timezone.utc)
            self.start_date = jan1.isoformat()
        else:
            self.start_date = (now_utc - timedelta(days=window_days)).isoformat()
        self.activity_threshold_dt = now_utc - timedelta(days=activity_window_days)
        self.activity_threshold_date = self.activity_threshold_dt.isoformat()
        # Lazily initialized DB engine for delta-mode reads
        self._db_engine = None

    async def collect_all_projects_raw_data(self) -> ConsolidatedData:
        """Collect raw data from all available projects

        Returns:
            Consolidated dictionary containing raw API responses from all projects

        Raises:
            ConnectionError: If unable to connect to Azure DevOps
            TimeoutError: If requests timeout
        """
        try:
            # Fetch all projects
            projects = await self._get_with_retry(self.ado_client.get_projects, "projects")

            if not projects:
                raise ValueError("No projects found in Azure DevOps")

            # Exclude archived projects (Azure DevOps has no first-class archived state for projects)
            # Heuristics used:
            #  - Keep only projects with state 'wellFormed'
            #  - Exclude names that indicate archival (e.g., contains 'archiv')
            def _project_looks_archived(p: Dict[str, Any]) -> bool:
                try:
                    state = str(p.get("state", "")).lower()
                    if state and state != "wellformed":
                        return True
                    name = str(p.get("name", ""))
                    return "archiv" in name.lower()
                except Exception:
                    return False

            active_projects = [p for p in projects if not _project_looks_archived(p)]
            if len(active_projects) < len(projects):
                log_with_context(
                    logger,
                    logging.INFO,
                    "Excluded archived/non-wellFormed projects",
                    total=len(projects),
                    excluded=len(projects) - len(active_projects),
                    remaining=len(active_projects),
                )

            # Limit to 10 projects in minimal mode
            if self.minimal:
                logger.info("Minimal mode enabled - limiting to 10 projects")
                active_projects = active_projects[:10]

            # Consolidated data structure for all projects
            try:
                merged_project_info = (
                    merge_project_info([create_project_info(p) for p in active_projects])
                    if isinstance(active_projects, list) and active_projects
                    else {}
                )
            except Exception:
                merged_project_info = {}

            consolidated_data: ConsolidatedData = {
                "projects": active_projects,
                "repositories": [],
                "repository_data": {},
                "builds": [],
                "build_data": {},
                # Provide a consolidated project descriptor for clarity in downstream exports
                "project": merged_project_info,
            }

            # Collect data for each project
            for project in active_projects:
                project_name = project["name"]
                try:
                    log_with_context(
                        logger,
                        logging.INFO,
                        f"Starting collection for project: {project_name}",
                        project=project_name,
                    )

                    project_data = await self.collect_all_raw_data(project_name)

                    # Merge repository list with project info
                    for repo in project_data.get("repositories", []):
                        repo["project"] = project_data.get("project", {})
                        consolidated_data["repositories"].append(repo)

                    # Merge repository data with project info
                    for repo_id, repo_data in project_data.get("repository_data", {}).items():
                        repo_data["project"] = project_data.get("project", {})
                        consolidated_data["repository_data"][repo_id] = repo_data

                    # Merge build list
                    consolidated_data["builds"].extend(project_data.get("builds", []))

                    # Merge build data
                    consolidated_data["build_data"].update(project_data.get("build_data", {}))

                    log_with_context(
                        logger,
                        logging.INFO,
                        f"Completed collection for project: {project_name}",
                        project=project_name,
                        repositories_count=len(project_data.get("repositories", [])),
                        builds_count=len(project_data.get("builds", [])),
                    )

                except Exception as e:
                    log_with_context(
                        logger,
                        logging.ERROR,
                        f"Error collecting data for project {project_name}: {str(e)}",
                        project=project_name,
                        error=str(e),
                    )
                    continue

            # Log summary of collection
            log_with_context(
                logger,
                logging.INFO,
                "Completed all projects collection",
                total_projects=len(active_projects),
                total_repositories=len(consolidated_data["repositories"]),
                total_builds=len(consolidated_data["builds"]),
            )

            return consolidated_data

        except Exception as e:
            logger.error(f"Error collecting all projects data: {str(e)}")
            raise

    async def collect_all_raw_data(self, project: str) -> RawProjectData:
        """Collect all available raw data for a project

        Args:
            project: Project name or ID

        Returns:
            Dictionary containing raw API responses

        Raises:
            ValueError: If project name is invalid
            ConnectionError: If unable to connect to Azure DevOps
            TimeoutError: If requests timeout
        """
        try:
            log_with_context(
                logger,
                logging.INFO,
                f"Starting raw data collection for project: {project}",
                project=project,
            )

            # Fetch project info
            project_data = await self._get_with_retry(
                self.ado_client.get_project, "project info", project
            )

            if not project_data:
                raise ValueError(f"Project not found: {project}")

            # Transform project data structure
            try:
                transformed_project = create_project_info(project_data)
            except ValueError as e:
                logger.error(f"Error transforming project info: {str(e)}")
                raise

            # Calculate time window for project
            project_start_date = self.start_date
            # Delta-mode: Adjust window based on last DB run time
            if self.enable_delta:
                try:
                    loop = asyncio.get_running_loop()
                    last_run_dt = await loop.run_in_executor(None, self._get_last_run_time, project)
                    if last_run_dt:
                        candidate = last_run_dt + timedelta(minutes=1)
                        if candidate > parse_ts(project_start_date):
                            prev = project_start_date
                            project_start_date = candidate.isoformat()
                            log_with_context(
                                logger,
                                logging.INFO,
                                "Delta mode active: updated start_date from last DB run",
                                project=project,
                                previous_start=prev,
                                new_start=project_start_date,
                            )
                except Exception as e:
                    log_with_context(
                        logger,
                        logging.WARNING,
                        f"Delta mode unavailable; continuing default window: {e}",
                        project=project,
                    )

            # Fetch repositories
            repositories = await self._get_with_retry(
                self.ado_client.get_repositories, "repositories", project
            )

            # First exclude archived/disabled repositories explicitly
            # Azure DevOps exposes disabled repos via `isDisabled`; also defensively exclude
            # repos whose names indicate archival (e.g., "(Archived)")
            def _looks_archived(repo: Dict[str, Any]) -> bool:
                try:
                    if bool(repo.get("isDisabled", False)):
                        return True
                    name = str(repo.get("name", ""))
                    return "archiv" in name.lower()
                except Exception:
                    return False

            non_archived_repositories = [r for r in repositories if not _looks_archived(r)]

            if len(non_archived_repositories) < len(repositories):
                log_with_context(
                    logger,
                    logging.INFO,
                    "Excluded archived/disabled repositories",
                    project=project,
                    excluded=len(repositories) - len(non_archived_repositories),
                    remaining=len(non_archived_repositories),
                )

            # Filter out stale repositories unless in "full" mode
            active_repositories = []
            if self.skip_stale_repos:
                for repo in non_archived_repositories:
                    repo_name = repo["name"]
                    last_update_time = repo.get("lastUpdateTime")

                    if not last_update_time:
                        log_with_context(
                            logger,
                            logging.WARNING,
                            f"Repository {repo_name} has no lastUpdateTime field, including by default",
                            project=project,
                            repository=repo_name,
                        )
                        active_repositories.append(repo)
                        continue

                    # Check if repository is active
                    try:
                        repo_updated_dt = parse_ts(last_update_time)
                    except (TypeError, ValueError):
                        # Include repo if timestamp is unparsable, but log a warning
                        log_with_context(
                            logger,
                            logging.WARNING,
                            f"Repository {repo_name} has unparsable lastUpdateTime, including by default",
                            project=project,
                            repository=repo_name,
                            last_update_time=str(last_update_time),
                        )
                        active_repositories.append(repo)
                        continue

                    if repo_updated_dt >= self.activity_threshold_dt:
                        active_repositories.append(repo)
                    else:
                        log_with_context(
                            logger,
                            logging.INFO,
                            f"Skipping repository {repo_name} due to inactivity, last updated at {last_update_time}",
                            project=project,
                            repository=repo_name,
                            last_update_time=last_update_time,
                            activity_threshold_date=self.activity_threshold_date,
                        )
            else:
                # Include all non-archived repositories in full mode
                active_repositories = list(non_archived_repositories)
            
            # Log repository filtering summary
            log_with_context(
                logger,
                logging.INFO,
                (
                    f"Filtered repositories by activity: {len(active_repositories)} active out of {len(repositories)} total"
                    if self.skip_stale_repos
                    else f"Full mode: including all {len(repositories)} repositories"
                ),
                project=project,
                active_count=len(active_repositories),
                total_count=len(repositories),
                activity_window_days=self.activity_window_days,
                skip_stale_repos=self.skip_stale_repos,
            )

            # Raw data dictionary for the project
            raw_data: RawProjectData = {
                "project": transformed_project,
                "repositories": active_repositories,
                "repository_data": {},
                "builds": [],
                "build_data": {},
            }

            # Collect data for each active repository
            # Get last run time once for skip logic
            try:
                loop = asyncio.get_running_loop()
                last_run_dt = await loop.run_in_executor(None, self._get_last_run_time, project)
            except Exception:
                last_run_dt = None

            for repo in active_repositories:
                repo_id = repo["id"]
                repo_name = repo["name"]
                if self.skip_stale_repos:
                    last_update_time = repo.get("lastUpdateTime")
                    # Skip repo if no changes since last run
                    if last_run_dt and last_update_time:
                        try:
                            updated_dt = parse_ts(last_update_time)
                            if updated_dt < last_run_dt:
                                log_with_context(
                                    logger,
                                    logging.INFO,
                                    f"Skipping repo (no changes since last run)",
                                    project=project,
                                    repository=repo_name,
                                    last_update_time=last_update_time,
                                    last_run=last_run_dt.isoformat(),
                                )
                                # Register a minimal entry even for skipped repos
                                raw_data["repository_data"][repo_id] = RepositoryData(
                                    commits=[], branches=[], pull_requests=[], policies={
                                        "repository_policies": [],
                                        "branch_policies": [],
                                        "statistics": {
                                            "total_policies": 0,
                                            "blocking_policies": 0,
                                            "enabled_policies": 0,
                                            "policy_types": [],
                                        },
                                        "scan_time": datetime.now(timezone.utc).isoformat(),
                                    },
                                    project={"id": transformed_project.get("id"), "name": transformed_project.get("name")},
                                )
                                continue
                        except Exception:
                            pass

                log_with_context(
                    logger,
                    logging.INFO,
                    f"Collecting data for repository: {repo_name}",
                    project=project,
                    repository=repo_name,
                )

                try:
                    # Group repository data
                    repo_dict = {
                        "commits": await self._get_with_retry(
                            self.ado_client.get_commits,
                            "commits",
                            repo_id,
                            project,
                            project_start_date,
                        ),
                        "branches": await self._get_with_retry(
                            self.ado_client.get_branches, "branches", repo_id, project
                        ),
                        "pull_requests": await self._get_with_retry(
                            self.ado_client.get_pull_requests, 
                            "pull requests", 
                            project, 
                            repo_id,
                            project_start_date
                        ),
                        "policies": [],  # Initialize as empty list
                    }
                    
                    # Fetch and structure repository policies
                    policies = await self._get_repository_policies(project, repo_id)
                    repo_dict["policies"] = {
                        "repository_policies": policies.get("repository_policies", []),
                        "branch_policies": policies.get("branch_policies", []),
                        "statistics": policies.get("statistics", {
                            "total_policies": 0,
                            "blocking_policies": 0,
                            "enabled_policies": 0,
                            "policy_types": [],
                        }),
                        "scan_time": policies.get("scan_time", datetime.now(timezone.utc).isoformat())
                    }
                    
                    # Add project context
                    repo_dict["project"] = {
                        "id": raw_data["project"]["id"],
                        "name": raw_data["project"]["name"]
                    }
                    
                    # Create a type-safe RepositoryData object
                    raw_data["repository_data"][repo_id] = RepositoryData(
                        commits=repo_dict["commits"],
                        branches=repo_dict["branches"],
                        pull_requests=repo_dict["pull_requests"],
                        policies=repo_dict["policies"],
                        project=repo_dict["project"]
                    )
                except Exception as e:
                    logger.error(
                        f"Error collecting repository data for {repo_name}: {str(e)}",
                        extra={"project": project, "repository": repo_name, "error": str(e)},
                    )
                    continue

            # Fetch builds
            builds = await self._get_with_retry(
                self.ado_client.get_builds, 
                "builds", 
                project,
                None,  # project_name parameter
                project_start_date
            )
            raw_data["builds"] = builds

            # Collect details for each build
            for build in builds:
                build_id = build["id"]

                log_with_context(
                    logger,
                    logging.INFO,
                    f"Collecting data for build: {build_id}",
                    project=project,
                    build_id=build_id,
                )

                try:
                    coverage_data = await self._get_with_retry(
                        self.ado_client.get_code_coverage, "build coverage", project, build_id
                    )

                    test_data = await self._get_with_retry(
                        self.ado_client.get_test_results, "test results", project, build_id
                    )

                    security_scan_data = await self._get_with_retry(
                        self.ado_client.get_security_scan_results, "security scan results", project, build_id
                    )

                    # Normalize coverage_data to the expected format
                    if coverage_data:
                        if isinstance(coverage_data, list):
                            # If it's a list, wrap it in the expected structure
                            coverage_data = {
                                "value": [
                                    {
                                        "state": "0",
                                        "modules": coverage_data
                                    }
                                ]
                            }
                        elif isinstance(coverage_data, dict):
                            if "value" not in coverage_data:
                                # If it's a dict without value, wrap it
                                coverage_data = {
                                    "value": [
                                        {
                                            "state": "0",
                                            "modules": [coverage_data] if "modules" not in coverage_data else [coverage_data]
                                        }
                                    ]
                                }
                            else:
                                # Ensure each value item has modules
                                coverage_data["value"] = [
                                    {
                                        "state": item.get("state", "0"),
                                        "modules": item.get("modules", [])
                                    }
                                    for item in coverage_data["value"]
                                ]
                    else:
                        coverage_data = {"value": [{"state": "0", "modules": []}]}

                    raw_data["build_data"][build_id] = {
                        "coverage": coverage_data,
                        "tests": (
                            test_data
                            if test_data
                            else {"results": [{"total": 0, "passed": 0, "failed": 0}]}
                        ),
                        "security_scans": security_scan_data if security_scan_data else []
                    }
                except Exception as e:
                    logger.error(
                        f"Error collecting build data for build {build_id}: {str(e)}",
                        extra={"project": project, "build_id": build_id, "error": str(e)},
                    )
                    raw_data["build_data"][build_id] = {
                        "coverage": {"value": []},
                        "tests": {"results": [{"total": 0, "passed": 0, "failed": 0}]},
                        "security_scans": []
                    }

            log_with_context(
                logger,
                logging.INFO,
                f"Completed raw data collection for project: {project}",
                project=project,
                repositories_count=len(active_repositories),
                builds_count=len(builds),
            )
            return raw_data

        except ValueError as e:
            logger.error(f"Invalid project: {str(e)}")
            raise
        except ConnectionError as e:
            logger.error(f"Connection error: {str(e)}")
            raise
        except TimeoutError as e:
            logger.error(f"Request timeout: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error collecting raw data: {str(e)}")
            raise

    async def _get_with_retry(self, func: Any, operation: str, *args: Any) -> Any:
        """Execute API call with retry logic

        Args:
            func: API function to call
            operation: Operation name for logging
            *args: Arguments for the function

        Returns:
            API response data

        Raises:
            TimeoutError: If all retries fail
        """
        max_retries = 3
        retry_delay = 1

        for attempt in range(max_retries):
            try:
                return await func(*args)
            except Exception as e:
                if attempt == max_retries - 1:
                    raise TimeoutError(
                        f"Failed to get {operation} after {max_retries} attempts: {str(e)}"
                    )
                logger.warning(
                    f"Attempt {attempt + 1}/{max_retries} failed for {operation}: {str(e)}. Retrying..."
                )
                await asyncio.sleep(retry_delay * (attempt + 1))

    def _get_last_run_time(self, project: str) -> Optional[datetime]:
        """Return most recent run time for project from DB, or None.

        Uses env var DATABASE_URL. Does not create tables.
        """
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            return None
        try:
            from importlib import import_module
            models = import_module("devops_metrics.persistence.models")
            init_engine_and_tables = getattr(models, "init_engine_and_tables")
            Run = getattr(models, "Run")
            from sqlmodel import Session, select  # type: ignore

            if self._db_engine is None:
                self._db_engine = init_engine_and_tables(db_url, allow_create_all=False)
            org = getattr(getattr(self.ado_client, "settings", object()), "azure_devops_org", None)
            with Session(self._db_engine) as session:
                stmt = select(Run).order_by(Run.executed_at.desc())
                if org:
                    stmt = stmt.where(Run.org == org)
                if project:
                    stmt = stmt.where(Run.project == project)
                run = session.exec(stmt).first()
                return run.executed_at if run else None
        except Exception as e:
            logger.debug(f"_get_last_run_time error: {e}")
            return None

    async def _get_repository_policies(self, project: str, repo_id: str) -> Dict[str, Any]:
        """Get all policies for a repository

        Args:
            project: Project name or ID
            repo_id: Repository ID

        Returns:
            Dictionary containing all policies and their statistics
        """
        try:
            # Fetch policy configurations
            policy_configs = await self._get_with_retry(
                self.ado_client.get_policy_configurations, "policy configurations", project
            )

            # Fetch policy types
            policy_types = await self._get_with_retry(
                self.ado_client.get_policy_types, "policy types", project
            )

            # Create a lookup map for policy types
            policy_type_map = {pt["id"]: pt for pt in policy_types}

            repository_policies: List[Dict[str, Any]] = []
            branch_policies: List[Dict[str, Any]] = []
            all_policies_for_repo: List[Dict[str, Any]] = []

            for policy in policy_configs:
                policy_scope = policy.get("settings", {}).get("scope", [])
                
                # Ensure policy_scope is a list for consistent processing
                if not isinstance(policy_scope, list):
                    policy_scope = [policy_scope] if policy_scope else []
                
                # Policy applies if its scope matches the repo or is project-level (repo ID is null).
                applies_to_repo = False
                has_specific_repo_id = False
                is_project_level = False
                
                for scope_item in policy_scope:
                    if not isinstance(scope_item, dict):
                        continue
                    
                    repo_id_value = scope_item.get("repositoryId")
                    
                    # Check if this is a project-level policy 
                    # (repositoryId is None/null OR missing entirely)
                    if ("repositoryId" in scope_item and repo_id_value is None) or "repositoryId" not in scope_item:
                        is_project_level = True
                        applies_to_repo = True
                        logger.debug(
                            f"Found project-level policy {policy.get('id')} with null/missing repositoryId"
                        )
                    # Check if this is a repo-specific policy
                    elif repo_id_value == repo_id:
                        has_specific_repo_id = True
                        applies_to_repo = True
                        break
                
                if not applies_to_repo:
                    continue # Skip policies not related to this repository

                # Add policy type display name
                policy_type_id = policy.get("type", {}).get("id")
                policy_type = policy_type_map.get(policy_type_id)
                if policy_type:
                    policy["type"] = policy_type

                all_policies_for_repo.append(policy)

                # Categorize into repository or branch policies
                has_branch_scope = False
                for scope_item in policy_scope:
                    if isinstance(scope_item, dict) and scope_item.get("refName"):
                        has_branch_scope = True
                        break
                        
                if has_branch_scope:
                    # This is a branch-specific policy
                    # Extract branch name if available
                    branch_name = "Unknown"
                    is_default_branch = False
                    
                    for scope_item in policy_scope:
                        if isinstance(scope_item, dict) and scope_item.get("refName"):
                            ref_name = scope_item.get("refName", "")
                            branch_name = ref_name.replace("refs/heads/", "") if ref_name else "Unknown"
                            # Check if it's the default branch (assuming 'main' or 'master' for simplicity)
                            if branch_name in ["main", "master"]: # This might need to be dynamic based on actual default branch
                                is_default_branch = True
                            break
                    
                    # Find or create the branch entry in branch_policies
                    branch_entry = next(
                        (item for item in branch_policies if item["branch_name"] == branch_name and 
                         item.get("is_project_level", False) == is_project_level),
                        None
                    )
                    if not branch_entry:
                        branch_entry = {
                            "branch_name": branch_name,
                            "is_default_branch": is_default_branch,
                            "policies": [],
                            "is_project_level": is_project_level
                        }
                        branch_policies.append(branch_entry)
                    branch_entry["policies"].append(policy)
                else:
                    # This is a repository-wide policy
                    repository_policies.append(policy)

            # Calculate statistics based on all policies found for the repository
            policy_count = len(all_policies_for_repo)
            blocking_policies = sum(1 for p in all_policies_for_repo if p.get("isBlocking", False))
            enabled_policies = sum(1 for p in all_policies_for_repo if p.get("isEnabled", False))
            policy_types_used = list(
                set(p.get("type", {}).get("displayName", "Unknown") for p in all_policies_for_repo)
            )
            
            # Count project-level vs repository-level policies
            project_level_count = sum(1 for p in all_policies_for_repo 
                                    if any(isinstance(scope_item, dict) and 
                                          (("repositoryId" in scope_item and scope_item.get("repositoryId") is None) or
                                           "repositoryId" not in scope_item)
                                          for scope_item in p.get("settings", {}).get("scope", [])))
            repo_level_count = policy_count - project_level_count
            
            # Log policy counts for debugging
            logger.info(
                f"Found policies for repository {repo_id} in project {project}: "
                f"total={policy_count}, repo={len(repository_policies)}, "
                f"branch={sum(len(b['policies']) for b in branch_policies)}, "
                f"project_level={project_level_count}, repo_level={repo_level_count}"
            )

            return {
                "repository_policies": repository_policies,
                "branch_policies": branch_policies,
                "statistics": {
                    "total_policies": policy_count,
                    "blocking_policies": blocking_policies,
                    "enabled_policies": enabled_policies,
                    "policy_types": policy_types_used,
                    "project_level_policies": project_level_count,
                    "repository_level_policies": repo_level_count,
                },
                "scan_time": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            logger.error(
                f"Error getting policies for repository {repo_id} in project {project}: {str(e)}",
                extra={"project": project, "repository_id": repo_id, "error": str(e)},
            )
            return {
                "repository_policies": [],
                "branch_policies": [],
                "statistics": {
                    "total_policies": 0,
                    "blocking_policies": 0,
                    "enabled_policies": 0,
                    "policy_types": [],
                    "project_level_policies": 0,
                    "repository_level_policies": 0,
                },
                "scan_time": datetime.now(timezone.utc).isoformat(),
                "error": str(e),
            }
