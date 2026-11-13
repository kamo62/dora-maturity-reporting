"""Segment Maturity Assessment Service"""

from typing import Dict, Any, List, Optional
from typing_extensions import TypedDict
import pandas as pd
import aiohttp
from openpyxl.styles import Font, PatternFill, Alignment # type: ignore

from ...utils.logging import get_logger
from .base_service import MetricsService
from .raw_maturity_assessment import RawMaturityAssessmentService
from ..api.ado_client import source_request_trace

logger = get_logger(__name__)


class ProjectScores(TypedDict):
    name: str
    repositories: List[str]
    scores: List[float]
    maturity_score: float
    repository_count: int

class SegmentMaturityAssessmentService(MetricsService):
    """Service for assessing maturity at project and security group levels"""

    def __init__(self, ado_client: Any) -> None:
        """Initialize the service with an ADO client"""
        super().__init__(ado_client)
        self.raw_maturity_service = RawMaturityAssessmentService(ado_client)
        self.scoring_service = self.raw_maturity_service.scoring
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_aiohttp_session(self) -> aiohttp.ClientSession:
        """Get or create an aiohttp session"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self.ado_client.headers,
                trace_configs=[source_request_trace(self.ado_client.base_url)],
                trust_env=False,
            )
        assert self._session is not None
        return self._session

    async def collect_metrics(self) -> Dict[str, Any]:
        """Not used - this service only processes already collected data"""
        return {}

    async def get_cached_metrics(self) -> Optional[Dict[str, Any]]:
        """Not used - this service only processes already collected data"""
        return None

    async def get_group_entitlements(self) -> List[Dict[str, Any]]:
        """Get all group entitlements and their project mappings"""
        try:
            # Use vsaex.dev.azure.com for group entitlements API
            org_url = self.ado_client.base_url.replace("dev.azure.com", "vsaex.dev.azure.com")
            url = f"{org_url}/_apis/GroupEntitlements?api-version=7.1"

            async with aiohttp.ClientSession(
                headers=self.ado_client.headers,
                trace_configs=[source_request_trace(self.ado_client.base_url)],
                trust_env=False,
            ) as session:
                logger.info(f"Fetching group entitlements from: {url}")
                async with session.get(url) as response:
                    if response.status == 404:
                        # If 404, try the alternative groups API
                        alt_url = f"{self.ado_client.base_url}/_apis/graph/groups?api-version=7.1-preview.1"
                        logger.info(f"Group entitlements not found, trying groups API: {alt_url}")
                        async with session.get(alt_url) as alt_response:
                            alt_response.raise_for_status()
                            response_data = await alt_response.json()

                            # Process groups data
                            project_entitlements_data_list = []
                            for group in response_data.get("value", []):
                                # Get group projects using a separate API call
                                group_id = group.get("originId")
                                if group_id:
                                    projects_url = f"{self.ado_client.base_url}/_apis/graph/groups/{group_id}/memberships?api-version=7.1-preview.1"
                                    async with session.get(projects_url) as projects_response:
                                        if projects_response.status == 200:
                                            projects_data = await projects_response.json()
                                            projects_list = [
                                                member.get("containerName")
                                                for member in projects_data.get("value", [])
                                                if member.get("containerName")
                                            ]

                                            if projects_list:
                                                group_detail = {
                                                    "originId": group_id,
                                                    "displayName": group.get("displayName"),
                                                    "projects": projects_list,
                                                    "description": group.get("description", ""),
                                                }
                                                project_entitlements_data_list.append(group_detail)

                            if not project_entitlements_data_list:
                                logger.warning("No project mappings found in groups API response")
                            return project_entitlements_data_list
                    else:
                        response.raise_for_status()
                        response_data = await response.json()

                        project_entitlements_data_list = []
                        for detail in response_data.get("value", []):
                            # Extract group information
                            group_info = detail.get("group", {})
                            group_id = group_info.get("originId")
                            group_name = group_info.get("displayName")

                            if not group_id or not group_name:
                                continue

                            # Extract project information
                            projects_list = []
                            for project in detail.get("projectEntitlements", []):
                                project_ref = project.get("projectRef", {})
                                project_name = project_ref.get("name")
                                if project_name:
                                    projects_list.append(project_name)

                            if projects_list:  # Only include groups that have project mappings
                                group_detail = {
                                    "originId": group_id,
                                    "displayName": group_name,
                                    "projects": projects_list,
                                    "description": group_info.get("description", ""),
                                    "licenseType": detail.get("licenseRule", {}).get(
                                        "licenseDisplayName", ""
                                    ),
                                }
                                project_entitlements_data_list.append(group_detail)

                        if not project_entitlements_data_list:
                            logger.warning(
                                "No project mappings found in group entitlements response"
                            )
                        return project_entitlements_data_list

        except aiohttp.ClientResponseError as e:
            logger.error(f"HTTP error getting group entitlements: {str(e)}")
            if hasattr(e, "status"):
                logger.error(f"Status code: {e.status}")
            if hasattr(e, "message"):
                logger.error(f"Error message: {e.message}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error getting group entitlements: {str(e)}")
            return []

    async def cleanup(self) -> None:
        """Cleanup resources"""
        if self._session and not self._session.closed:
            await self._session.close()

    def calculate_project_maturity(self, maturity_data: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate maturity scores for each project by averaging repository scores

        Args:
            maturity_data: Raw maturity assessment data

        Returns:
            Dictionary with project maturity scores
        """
        project_scores: Dict[str, Dict[str, Any]] = {}
        repositories = maturity_data.get("repositories", {})
        logger.info(f"Starting project maturity calculation with {len(repositories)} repositories")

        # Group repositories by project
        for repo_id, repo_data in repositories.items():
            project_info = repo_data.get("project", {})
            if not project_info:
                logger.warning(f"No project info found for repository {repo_id}")
                continue

            project_id = project_info.get("id")
            project_name = project_info.get("name")
            if not project_id or not project_name:
                logger.warning(f"Invalid project info for repository {repo_id}")
                continue

            if project_id not in project_scores:
                project_scores[project_id] = {
                    "name": project_name,
                    "repositories": [],
                    "scores": [],
                    "maturity_score": 0.0,
                    "repository_count": 0
                }

            # Get repository score
            repo_name = repo_data.get("name", repo_id)
            logger.debug(f"Processing repository {repo_name} for project {project_name}")

            score = repo_data.get("overall", {}).get("score", 0)
            project_scores[project_id]["repositories"].append(repo_name)
            project_scores[project_id]["scores"].append(score)
            project_scores[project_id]["repository_count"] += 1

        # Calculate averages for each project
        for project_id, data in project_scores.items():
            if data["scores"]:
                avg_score = sum(data["scores"]) / len(data["scores"])
                logger.info(
                    f"Project {data['name']}: Average score {avg_score} from {len(data['repositories'])} repositories"
                )
                data["maturity_score"] = round(avg_score, 4)
            else:
                logger.warning(f"Project {data['name']} has no scores to calculate average")

        logger.info(f"Completed project maturity calculation for {len(project_scores)} projects")
        return project_scores

    def calculate_segment_maturity(
        self, project_scores: Dict[str, Any], group_entitlements: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Calculate maturity scores for each security group by averaging project scores

        Args:
            project_scores: Project maturity scores
            group_entitlements: Security group entitlements data

        Returns:
            Dictionary with security group maturity scores
        """
        segment_scores: Dict[str, Dict[str, Any]] = {}
        logger.info(f"Starting segment maturity calculation with {len(group_entitlements)} groups")
        
        # Create a mapping of project names to project IDs
        project_name_to_id: Dict[str, str] = {}
        for project_id, data in project_scores.items():
            project_name = data.get("name")
            if project_name:
                project_name_to_id[project_name] = project_id
                logger.debug(f"Added project mapping: {project_name} -> {project_id}")
        
        logger.info(f"Project name mappings available: {project_name_to_id}")
        logger.info(f"Available projects for scoring: {list(project_name_to_id.keys())}")

        for group in group_entitlements:
            group_name = group.get("displayName")
            if not group_name:
                logger.warning("Found group without displayName, skipping")
                continue

            group_projects = group.get("projects", [])
            if not group_projects:
                logger.warning(f"Group {group_name} has no projects, skipping")
                continue

            logger.info(f"Processing group {group_name} with projects: {group_projects}")

            valid_projects: List[str] = []
            project_scores_list: List[float] = []
            project_details: List[Dict[str, Any]] = []

            for project_name in group_projects:
                # Try exact match first
                matched_project_id: Optional[str] = project_name_to_id.get(project_name)
                
                # If no exact match, try case-insensitive match
                if not matched_project_id:
                    project_name_lower = project_name.lower()
                    for name, id in project_name_to_id.items():
                        if name.lower() == project_name_lower:
                            matched_project_id = id
                            logger.debug(f"Found case-insensitive match for {project_name} -> {name}")
                            break
                
                # If still no match, try removing special characters and spaces
                if not matched_project_id:
                    normalized_name = ''.join(c.lower() for c in project_name if c.isalnum())
                    for name, id in project_name_to_id.items():
                        normalized_existing = ''.join(c.lower() for c in name if c.isalnum())
                        if normalized_existing == normalized_name:
                            matched_project_id = id
                            logger.debug(f"Found normalized match for {project_name} -> {name}")
                            break
                
                if matched_project_id is not None and matched_project_id in project_scores:
                    project_data = project_scores[matched_project_id]
                    logger.info(
                        f"Found score data for project {project_name} in group {group_name}"
                    )
                    valid_projects.append(project_name)
                    project_scores_list.append(project_data["maturity_score"])
                    project_details.append(
                        {
                            "name": project_name,
                            "score": project_data["maturity_score"],
                            "level": self.scoring_service.determine_maturity_level(
                                project_data["maturity_score"]
                            ),
                            "repositories": project_data["repository_count"],
                        }
                    )
                else:
                    logger.warning(
                        f"Project {project_name} in group {group_name} has no score data. Available projects: {list(project_scores.keys())}"
                    )

            if project_scores_list:
                avg_score = sum(project_scores_list) / len(project_scores_list)
                logger.info(
                    f"Group {group_name}: Average score {avg_score} from {len(valid_projects)} projects"
                )

                # Calculate maturity level
                level = self.scoring_service.determine_maturity_level(avg_score)

                segment_scores[group_name] = {
                    "maturity_score": round(avg_score, 4),
                    "maturity_level": level,
                    "maturity_label": self.scoring_service.get_maturity_label(level),
                    "project_count": len(valid_projects),
                    "projects": valid_projects,
                    "project_details": project_details,
                    "description": group.get("description", ""),
                    "licenseType": group.get("licenseType", ""),
                    "originId": group.get("originId", ""),
                    "statistics": {
                        "min_score": round(min(project_scores_list), 4),
                        "max_score": round(max(project_scores_list), 4),
                        "avg_score": round(avg_score, 4),
                        "total_repositories": sum(p["repositories"] for p in project_details),
                    },
                }
            else:
                logger.warning(f"Group {group_name} has no valid projects with scores")

        return segment_scores

    def export_to_excel(
        self, project_scores: Dict[str, Any], segment_scores: Dict[str, Any], output_path: str
    ) -> None:
        """Export project and segment maturity scores to Excel

        Args:
            project_scores: Project maturity scores
            segment_scores: Security group maturity scores
            output_path: Path to save the Excel file
        """
        try:
            # Create project maturity dataframe
            project_data: List[Dict[str, Any]] = []
            for project_id, data in project_scores.items():
                level = self.scoring_service.determine_maturity_level(data["maturity_score"])
                # Format repositories with bullet points
                repositories_formatted = "\n".join(f"• {repo}" for repo in data["repositories"])
                project_data.append(
                    {
                        "Project Name": data["name"],  # Use the stored project name
                        "Maturity Score": data["maturity_score"],
                        "Maturity Level": level,
                        "Maturity Label": self.scoring_service.get_maturity_label(level),
                        "Number of Repositories": data["repository_count"],
                        "Repositories": repositories_formatted,
                    }
                )

            # Create segment maturity dataframe
            segment_data: List[Dict[str, Any]] = []
            for segment_name, data in segment_scores.items():
                maturity_level = self.scoring_service.determine_maturity_level(
                    data["maturity_score"]
                )
                # Format projects with bullet points
                projects_formatted = "\n".join(f"• {proj}" for proj in data["projects"])
                segment_data.append(
                    {
                        "Security Group Name": segment_name,
                        "Maturity Score": data["maturity_score"],
                        "Maturity Level": maturity_level,
                        "Maturity Label": self.scoring_service.get_maturity_label(maturity_level),
                        "Number of Projects": data["project_count"],
                        "Projects": projects_formatted,
                        "Description": data.get("description", ""),
                        "License Type": data.get("licenseType", ""),
                    }
                )

            # Export to Excel with formatting
            with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
                if project_data:
                    df_projects = pd.DataFrame(project_data)
                    df_projects.to_excel(writer, sheet_name="Project Maturity", index=False)

                    # Get the worksheet
                    ws = writer.sheets["Project Maturity"]

                    # Format columns
                    ws.column_dimensions["A"].width = 40  # Project Name
                    ws.column_dimensions["B"].width = 15  # Maturity Score
                    ws.column_dimensions["C"].width = 15  # Maturity Level
                    ws.column_dimensions["D"].width = 20  # Maturity Label
                    ws.column_dimensions["E"].width = 20  # Number of Repositories
                    ws.column_dimensions["F"].width = 60  # Repositories

                    # Format headers
                    for cell in ws[1]:
                        cell.font = Font(bold=True)
                        cell.fill = PatternFill(
                            start_color="E6E6E6",
                            end_color="E6E6E6",
                            fill_type="solid"
                        )

                    # Format score cells and enable text wrapping for repositories
                    for row in range(2, ws.max_row + 1):
                        # Format score as percentage
                        score_cell = ws[f'B{row}']
                        score_cell.number_format = '0.00%'
                        
                        # Enable text wrapping for repositories
                        repo_cell = ws[f'F{row}']
                        repo_cell.alignment = Alignment(wrap_text=True, vertical='top')
                        
                        # Color code based on score
                        score = float(score_cell.value or 0)
                        if score >= 0.81:
                            color = '92D050'  # Green
                        elif score >= 0.61:
                            color = '00B0F0'  # Blue
                        elif score >= 0.41:
                            color = 'FFFF00'  # Yellow
                        elif score >= 0.21:
                            color = 'FFC000'  # Orange
                        else:
                            color = 'FF0000'  # Red
                        
                        score_cell.fill = PatternFill(
                            start_color=color,
                            end_color=color,
                            fill_type='solid'
                        )

                if segment_data:
                    df_segments = pd.DataFrame(segment_data)
                    df_segments.to_excel(writer, sheet_name="Segment Maturity", index=False)

                    # Get the worksheet
                    ws = writer.sheets["Segment Maturity"]

                    # Format columns
                    ws.column_dimensions["A"].width = 40  # Security Group Name
                    ws.column_dimensions["B"].width = 15  # Maturity Score
                    ws.column_dimensions["C"].width = 15  # Maturity Level
                    ws.column_dimensions["D"].width = 20  # Maturity Label
                    ws.column_dimensions["E"].width = 20  # Number of Projects
                    ws.column_dimensions["F"].width = 60  # Projects
                    ws.column_dimensions["G"].width = 40  # Description
                    ws.column_dimensions["H"].width = 20  # License Type

                    # Format headers
                    for cell in ws[1]:
                        cell.font = Font(bold=True)
                        cell.fill = PatternFill(
                            start_color="E6E6E6",
                            end_color="E6E6E6",
                            fill_type="solid"
                        )

                    # Format score cells and enable text wrapping
                    for row in range(2, ws.max_row + 1):
                        # Format score as percentage
                        score_cell = ws[f'B{row}']
                        score_cell.number_format = '0.00%'
                        
                        # Enable text wrapping for projects and description
                        for col in ['F', 'G']:  # Projects and Description columns
                            cell = ws[f'{col}{row}']
                            cell.alignment = Alignment(wrap_text=True, vertical='top')
                        
                        # Color code based on score
                        score = float(score_cell.value or 0)
                        if score >= 0.81:
                            color = '92D050'  # Green
                        elif score >= 0.61:
                            color = '00B0F0'  # Blue
                        elif score >= 0.41:
                            color = 'FFFF00'  # Yellow
                        elif score >= 0.21:
                            color = 'FFC000'  # Orange
                        else:
                            color = 'FF0000'  # Red
                        
                        score_cell.fill = PatternFill(
                            start_color=color,
                            end_color=color,
                            fill_type='solid'
                        )

            logger.info(f"Segment maturity data exported to {output_path}")

        except Exception as e:
            logger.error(f"Error exporting to Excel: {str(e)}")
            raise
