"""Raw Data Export Service"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, cast

import pandas as pd

from ...utils.logging import get_logger
from ...types import RawProjectData

logger = get_logger(__name__)


from .base_service import MetricsService


class RawDataExporter(MetricsService):
    """Exports raw data to Excel and JSON formats"""

    def __init__(self, ado_client: Any) -> None:
        """Initialize the exporter"""
        super().__init__(ado_client)
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    async def collect_metrics(self) -> Dict[str, Any]:
        """Not used - this service only exports data"""
        return {}

    async def get_cached_metrics(self) -> Optional[Dict[str, Any]]:
        """Not used - this service only exports data"""
        return None

    def _safe_sheet_name(self, name: str) -> str:
        """Create Excel-safe sheet name

        Args:
            name: Original sheet name

        Returns:
            Excel-compatible sheet name (<=31 chars)
        """
        # Replace invalid characters
        safe_name = str(name).replace("/", "_").replace("\\", "_")
        # Truncate to Excel's 31 character limit
        return safe_name[:31]

    def _flatten_if_needed(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Flatten nested structures for Excel compatibility

        Args:
            data: Raw data dictionary

        Returns:
            Flattened dictionary
        """
        flattened = {}
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                flattened[key] = json.dumps(value)
            else:
                flattened[key] = value
        return flattened

    def export_data(
        self, raw_data: Dict[str, Any], output_path: Optional[str] = None
    ) -> Dict[str, str]:
        """Export the raw data to Excel and JSON files."""
        if not isinstance(raw_data, dict):
            logger.error("Raw data must be a dictionary")
            return {}

        try:
            # Initialize data structures
            all_builds = []
            all_coverage = []
            all_tests = []
            
            # Get project information
            project_info = None
            for repo in raw_data.get('repositories', []):
                if 'project' in repo:
                    project_info = repo['project']
                    break
            
            # Ensure build_data is a dictionary
            build_data_dict = raw_data.get("build_data", {})
            if not isinstance(build_data_dict, dict):
                logger.warning("Build data is not a dictionary, skipping processing")
                build_data_dict = {}

            logger.info(f"Processing build data from {len(build_data_dict)} builds")

            # Process each build
            for build_id, build_data in build_data_dict.items():
                if not isinstance(build_data, dict):
                    logger.warning(f"Invalid build data format for build {build_id}, skipping")
                    continue

                try:
                    # Get build info safely
                    builds = raw_data.get("builds", [])
                    if not isinstance(builds, list):
                        builds = []
                    
                    build = next(
                        (
                            b
                            for b in builds
                            if isinstance(b, dict) and str(b.get("id")) == str(build_id)
                        ),
                        {},
                    )
                    
                    # Get repository info safely
                    repo_info = build.get("repository", {})
                    if not isinstance(repo_info, dict):
                        repo_info = {}

                    # Add project information to build record
                    build_record = {
                        "build_id": build_id,
                        "repository_id": repo_info.get("id", ""),
                        "repository_name": repo_info.get("name", ""),
                        "project_id": project_info.get("id", "") if project_info else "",
                        "project_name": project_info.get("name", "") if project_info else "",
                        "status": build.get("status", ""),
                        "result": build.get("result", ""),
                        "start_time": build.get("startTime", ""),
                        "finish_time": build.get("finishTime", ""),
                        "duration": build.get("duration", 0),
                        "source_branch": build.get("sourceBranch", ""),
                        "source_version": build.get("sourceVersion", ""),
                    }
                    all_builds.append(build_record)

                    # Process coverage data
                    coverage_data = build_data.get("coverage", {})
                    if coverage_data:
                        logger.debug(
                            f"Coverage data structure for build {build_id}: {json.dumps(coverage_data)}"
                        )

                        # Ensure coverage_data is a dictionary with 'value' key
                        if not isinstance(coverage_data, dict):
                            coverage_data = {"value": [{"state": "0", "modules": []}]}
                        
                        # Get coverage values safely
                        coverage_values = coverage_data.get("value", [])
                        if not isinstance(coverage_values, list):
                            coverage_values = [coverage_values]

                        for coverage_info in coverage_values:
                            if not isinstance(coverage_info, dict):
                                logger.warning(f"Invalid coverage info format for build {build_id}")
                                continue

                            # Get modules safely
                            modules = coverage_info.get("modules", [])
                            if not isinstance(modules, list):
                                modules = [modules] if modules else []

                            if modules:
                                logger.info(
                                    f"Processing {len(modules)} coverage modules for build {build_id}"
                                )
                                total_covered = 0
                                total_not_covered = 0

                                for module in modules:
                                    if not isinstance(module, dict):
                                        logger.warning(f"Invalid module format in build {build_id}")
                                        continue
                                    
                                    stats = module.get("statistics", {})
                                    if not isinstance(stats, dict):
                                        logger.warning(f"Invalid statistics format in build {build_id}")
                                        continue
                                        
                                    total_covered += stats.get("linesCovered", 0)
                                    total_not_covered += stats.get("linesNotCovered", 0)

                                total_lines = total_covered + total_not_covered
                                if total_lines > 0:
                                    coverage_record = {
                                        "build_id": build_id,
                                        "repository_id": repo_info.get("id", ""),
                                        "repository_name": repo_info.get("name", ""),
                                        "total_lines": total_lines,
                                        "covered_lines": total_covered,
                                        "Coverage (%)": round(
                                            (total_covered / total_lines * 100), 2
                                        ),
                                        "status": coverage_info.get("state", ""),
                                        "modules_count": len(modules),
                                        "module_details": json.dumps(
                                            [
                                                {
                                                    "name": m.get("name", ""),
                                                    "covered": m.get("statistics", {}).get(
                                                        "linesCovered", 0
                                                    ),
                                                    "not_covered": m.get(
                                                        "statistics", {}
                                                    ).get("linesNotCovered", 0),
                                                }
                                                for m in modules
                                                if isinstance(m, dict)
                                            ]
                                        ),
                                    }
                                    all_coverage.append(coverage_record)
                            else:
                                logger.warning(
                                    f"No modules found in coverage data for build {build_id}"
                                )

                    # Process test data safely
                    test_data = build_data.get("tests", {})
                    if test_data:
                        # Ensure we have a valid results list
                        results = test_data.get("results", [])
                        if not isinstance(results, list):
                            results = []
                        
                        # Get the first result safely
                        test_results = results[0] if results else {}
                        if isinstance(test_results, dict):
                            test_record = {
                                "build_id": build_id,
                                "repository_id": repo_info.get("id", ""),
                                "repository_name": repo_info.get("name", ""),
                                "total_tests": test_results.get("total", 0),
                                "passed_tests": test_results.get("passed", 0),
                                "failed_tests": test_results.get("failed", 0),
                            }
                            all_tests.append(test_record)
                        else:
                            logger.warning(f"Invalid test results format for build {build_id}")

                except Exception as e:
                    logger.error(f"Error processing build {build_id}: {str(e)}")
                    continue

            # Create output directory if needed
            if output_path:
                output_dir = Path(output_path).parent
            else:
                output_dir = Path("exports") / self.timestamp
            output_dir.mkdir(parents=True, exist_ok=True)

            # Generate file paths
            project_name = raw_data.get("project", {}).get("name", "Unknown").replace(" ", "_")
            base_name = f"{project_name}_raw_data"

            if output_path:
                excel_path = Path(output_path)
                json_path = excel_path.with_suffix(".json")
            else:
                excel_path = output_dir / f"{base_name}.xlsx"
                json_path = output_dir / f"{base_name}.json"

            # Export JSON
            with open(json_path, "w") as f:
                json.dump(raw_data, f, indent=2)
            logger.info(f"Raw data exported to JSON: {json_path}")

            # Process all repository data into consolidated dataframes
            consolidated_data = self._process_repository_data(cast(RawProjectData, raw_data))

            logger.info("Consolidated data summary:")
            logger.info(f"Policies count: {len(consolidated_data.get('policies', []))}")

            has_data = False

            # Before processing build data
            logger.info(f"Processing build data from {len(raw_data.get('build_data', {}))} builds")

            with pd.ExcelWriter(excel_path) as writer:
                # Project sheet
                if "project" in raw_data:
                    pd.DataFrame([self._flatten_if_needed(raw_data["project"])]).to_excel(
                        writer, sheet_name="Project", index=False
                    )

                # Repositories sheet
                if "repositories" in raw_data:
                    pd.DataFrame(
                        [self._flatten_if_needed(repo) for repo in raw_data["repositories"]]
                    ).to_excel(writer, sheet_name="Repositories", index=False)

                # Consolidated repository sheets
                if consolidated_data.get("commits"):
                    pd.DataFrame.from_records(consolidated_data["commits"]).to_excel(
                        writer, sheet_name="All Commits", index=False
                    )

                if consolidated_data.get("branches"):
                    pd.DataFrame.from_records(consolidated_data["branches"]).to_excel(
                        writer, sheet_name="All Branches", index=False
                    )

                if consolidated_data.get("pull_requests"):
                    pd.DataFrame.from_records(consolidated_data["pull_requests"]).to_excel(
                        writer, sheet_name="All Pull Requests", index=False
                    )

                # Process policies data
                if raw_data.get("repository_data"):
                    all_policies = []
                    for repo_id, repo_data in raw_data["repository_data"].items():
                        repo_name = next(
                            (
                                r["name"]
                                for r in raw_data.get("repositories", [])
                                if r["id"] == repo_id
                            ),
                            f"Repository_{repo_id}",
                        )

                        # Process policies with type checking
                        policies_data = repo_data.get("policies", {})
                        if not isinstance(policies_data, dict):
                            logger.warning(f"Invalid policies data format for repo {repo_name}, skipping policies")
                            continue

                        logger.info(f"Found policies data for repo {repo_name}")

                        # Process repository-wide policies
                        repository_policies = policies_data.get("repository_policies", [])
                        if isinstance(repository_policies, list):
                            for policy in repository_policies:
                                try:
                                    policy_record = self._create_policy_record(
                                        policy, {"repository_id": repo_id, "repository_name": repo_name}, 
                                        "Repository", "All Branches", False
                                    )
                                    all_policies.append(policy_record)
                                except Exception as e:
                                    logger.error(f"Error processing repository policy: {str(e)}")
                                    continue

                            # Process branch-specific policies
                            branch_policies = policies_data.get("branch_policies", [])
                            if isinstance(branch_policies, list):
                                for branch_policy in branch_policies:
                                    branch_name = branch_policy.get("branch_name", "")
                                    is_default = branch_policy.get("is_default_branch", False)

                                    for policy in branch_policy.get("policies", []):
                                        try:
                                            policy_record = self._create_policy_record(
                                                policy, {"repository_id": repo_id, "repository_name": repo_name},
                                                "Branch", branch_name, is_default
                                            )
                                            all_policies.append(policy_record)
                                        except Exception as e:
                                            logger.error(f"Error processing branch policy: {str(e)}")
                                            continue

                    if all_policies:
                        logger.info(f"Writing {len(all_policies)} policies to Excel")
                        pd.DataFrame.from_records(all_policies).to_excel(
                            writer, sheet_name="All Policies", index=False
                        )
                    else:
                        logger.warning("No policies data available to write to Excel")

                # Builds sheet
                if "builds" in raw_data:
                    pd.DataFrame(
                        [self._flatten_if_needed(build) for build in raw_data["builds"]]
                    ).to_excel(writer, sheet_name="All Builds", index=False)

                # Consolidated build data
                if all_coverage:
                    logger.info(f"Writing {len(all_coverage)} coverage records to Excel")
                    pd.DataFrame.from_records(all_coverage).to_excel(
                        writer, sheet_name="Coverage Data", index=False
                    )
                else:
                    logger.warning("No code coverage data available to write to Excel")

                if all_tests:
                    pd.DataFrame.from_records(all_tests).to_excel(
                        writer, sheet_name="Test Results", index=False
                    )

            logger.info("Data export complete")
            return {"excel": str(excel_path), "json": str(json_path)}

        except Exception as e:
            logger.error(f"Error exporting data: {str(e)}")
            raise

    def _process_repository_data(self, raw_data: RawProjectData) -> Dict[str, List[Dict[str, Any]]]:
        """Process repository data into dataframes"""
        all_commits = []
        all_branches = []
        all_pull_requests = []
        all_policies = []

        for repo_id, repo_data in raw_data.get("repository_data", {}).items():
            try:
                # Get repository info
                repo_info = next(
                    (
                        {"repository_id": r["id"], "repository_name": r["name"]}
                        for r in raw_data.get("repositories", [])
                        if r["id"] == repo_id
                    ),
                    {"repository_id": repo_id, "repository_name": f"Repository_{repo_id}"},
                )

                # Process policies with type checking
                policies_data = repo_data.get("policies", {})
                if not isinstance(policies_data, dict):
                    logger.warning(f"Invalid policies data format for repo {repo_info['repository_name']}, skipping policies")
                    continue

                logger.info(f"Found policies data for repo {repo_info['repository_name']}")

                # Process repository-wide policies
                repository_policies = policies_data.get("repository_policies", [])
                if isinstance(repository_policies, list):
                    for policy in repository_policies:
                        try:
                            policy_record = self._create_policy_record(
                                policy, repo_info, "Repository", "All Branches", False
                            )
                            all_policies.append(policy_record)
                        except Exception as e:
                            logger.error(f"Error processing repository policy: {str(e)}")
                            continue

                    # Process branch-specific policies
                    branch_policies = policies_data.get("branch_policies", [])
                    if isinstance(branch_policies, list):
                        for branch_policy in branch_policies:
                            branch_name = branch_policy.get("branch_name", "")
                            is_default = branch_policy.get("is_default_branch", False)

                            for policy in branch_policy.get("policies", []):
                                try:
                                    policy_record = self._create_policy_record(
                                        policy, repo_info, "Branch", branch_name, is_default
                                    )
                                    all_policies.append(policy_record)
                                except Exception as e:
                                    logger.error(f"Error processing branch policy: {str(e)}")
                                    continue

                # Process commits
                if commits := repo_data.get("commits", []):
                    for commit in commits:
                        commit_record = {**self._flatten_if_needed(commit), **repo_info}
                        all_commits.append(commit_record)

                # Process branches
                if branches := repo_data.get("branches", []):
                    for branch in branches:
                        branch_record = {**self._flatten_if_needed(branch), **repo_info}
                        all_branches.append(branch_record)

                # Process pull requests
                if pull_requests := repo_data.get("pull_requests", []):
                    for pr in pull_requests:
                        pr_record = {**self._flatten_if_needed(pr), **repo_info}
                        all_pull_requests.append(pr_record)

            except Exception as e:
                logger.error(
                    f"Error processing repository data: {str(e)}",
                    extra={"repository_id": repo_id, "error": str(e)},
                )
                continue

        logger.info(f"Processed data summary:")
        logger.info(f"- Policies: {len(all_policies)}")
        logger.info(f"- Commits: {len(all_commits)}")
        logger.info(f"- Branches: {len(all_branches)}")
        logger.info(f"- Pull Requests: {len(all_pull_requests)}")

        if all_policies:
            logger.info("Sample policy record structure:")
            logger.info(str(all_policies[0].keys()))

        return {
            "commits": all_commits,
            "branches": all_branches,
            "pull_requests": all_pull_requests,
            "policies": all_policies,
        }

    def _create_policy_record(
        self, policy: Dict[str, Any], repo_info: Dict[str, Any], scope_type: str, branch_name: str, is_default: bool
    ) -> Dict[str, Any]:
        """Create a standardized policy record"""
        return {
            "repository_id": repo_info["repository_id"],
            "repository_name": repo_info["repository_name"],
            "policy_type": policy.get("type", {}).get("displayName", "Unknown"),
            "policy_id": policy.get("id", ""),
            "is_enabled": policy.get("is_enabled", False),
            "is_blocking": policy.get("is_blocking", False),
            "scope_type": scope_type,
            "branch_name": branch_name,
            "is_default_branch": is_default,
            "created_by": json.dumps(policy.get("created_by", {})),
            "created_date": policy.get("created_date", ""),
            "modified_by": json.dumps(policy.get("modified_by", {})),
            "modified_date": policy.get("modified_date", ""),
            "settings": json.dumps(policy.get("settings", {})),
        }
