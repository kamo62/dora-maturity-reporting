"""Maturity Assessment Export Service"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List

import pandas as pd  # type: ignore
import openpyxl  # type: ignore

from ...utils.logging import get_logger
from .base_service import MetricsService

logger = get_logger(__name__)


class MaturityAssessmentExporter(MetricsService):
    """Exports maturity assessment data to Excel and JSON formats"""

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

    def export_data(self, assessment_data: Dict[str, Any], output_path: str) -> Dict[str, str]:
        """Export assessment data to Excel and JSON formats.
        
        This is an alias for export_assessment to maintain compatibility with existing code.
        
        Args:
            assessment_data: Assessment data to export
            output_path: Path to export the data to
            
        Returns:
            Dictionary with paths to the exported files
        """
        return self.export_assessment(assessment_data, output_path)

    def _get_project_name_from_repo(self, repo_data: Dict[str, Any]) -> str:
        """Extract project name from repository data.
        
        Args:
            repo_data: Repository data dictionary
            
        Returns:
            Project name or 'Unknown' if not found
        """
        # First try to get project info from repository data
        if "project" in repo_data and isinstance(repo_data["project"], dict):
            project_name = repo_data["project"].get("name")
            if project_name:
                return project_name
        
        # Fallback to legacy project info structure
        if "project" in repo_data:
            project_info = repo_data["project"]
            if isinstance(project_info, dict) and "name" in project_info:
                return project_info["name"]
        
        logger.warning(f"No project name found in repository data: {repo_data.get('name', 'Unknown')}")
        return "Unknown"

    def _get_primary_project_name(self, assessment_data: Dict[str, Any]) -> str:
        """Get the primary project name from assessment data.
        
        Tries multiple sources in order:
        1. Top-level project_name field
        2. Most common project name from repositories
        3. Fallback to 'Unknown'
        
        Args:
            assessment_data: Assessment data dictionary
            
        Returns:
            Primary project name
        """
        # First try top-level project name
        if "project_name" in assessment_data and assessment_data["project_name"] != "Unknown":
            return assessment_data["project_name"]
        
        # If not found or Unknown, try to get most common project from repositories
        repositories = assessment_data.get("repositories", {})
        if repositories:
            project_names: Dict[str, int] = {}
            for repo_data in repositories.values():
                project_name = self._get_project_name_from_repo(repo_data)
                if project_name != "Unknown":
                    project_names[project_name] = project_names.get(project_name, 0) + 1
            
            if project_names:
                # Return the most common project name
                return max(project_names.items(), key=lambda x: x[1])[0]
        
        return "Unknown"

    def _create_overall_score_df(
        self, assessment_data: Dict[str, Any]
    ) -> pd.DataFrame:
        """Create DataFrame for Overall Maturity Score sheet with repository-level data"""
        data: Dict[str, List[Any]] = {
            "Project": [],
            "Repository": [],
            "Overall Score": [],
            "Level": [],
            "Level Label": [],
            "Score Explanation": []
        }
        
        # Get repositories data
        repositories = assessment_data.get("repositories", {})
        
        # Create a row for each repository
        for repo_id, repo_data in repositories.items():
            repo_name = repo_data.get("name", repo_id)
            overall = repo_data.get("overall", {})
            
            # Get project name from repository data
            project_name = self._get_project_name_from_repo(repo_data)
            
            data["Project"].append(project_name)
            data["Repository"].append(repo_name)
            data["Overall Score"].append(overall.get("score", 0.0))
            data["Level"].append(overall.get("level", 1))
            data["Level Label"].append(overall.get("label", "Initial"))
            # Format explanations with bullet points
            explanations = overall.get("explanation", [])
            formatted_explanations = "\n".join(f"• {exp}" for exp in explanations)
            data["Score Explanation"].append(formatted_explanations)
        
        return pd.DataFrame(data)

    def _create_maturity_assessment_df(
        self, assessment_data: Dict[str, Any]
    ) -> pd.DataFrame:
        """Create DataFrame for Maturity Assessment sheet with repository-level data"""
        data: Dict[str, List[Any]] = {
            "Repository": [],
            "Project": [],
            "Overall Score": [],
            "Overall Level": [],
            "Overall Label": [],
            "Quality Assurance Score": [],
            "Quality Assurance Level": [],
            "Deployment Pipeline Score": [],
            "Deployment Pipeline Level": [],
            "Security Compliance Score": [],
            "Security Compliance Level": [],
            "Collaboration Metrics Score": [],
            "Collaboration Metrics Level": []
        }
        
        # Get repositories data
        repositories = assessment_data.get("repositories", {})
        
        # Create a row for each repository
        for repo_id, repo_data in repositories.items():
            repo_name = repo_data.get("name", repo_id)
            
            # Get project name from repository data
            project_name = self._get_project_name_from_repo(repo_data)
            
            # Get dimension data for this repository
            overall = repo_data.get("overall", {})
            quality = repo_data.get("quality_assurance", {})
            deployment = repo_data.get("deployment_pipeline", {})
            security = repo_data.get("security_compliance", {})
            collaboration = repo_data.get("collaboration_metrics", {})
            
            # Add repository row
            data["Repository"].append(repo_name)
            data["Project"].append(project_name)
            data["Overall Score"].append(overall.get("score", 0.0))
            data["Overall Level"].append(overall.get("level", 1))
            data["Overall Label"].append(overall.get("label", "Initial"))
            data["Quality Assurance Score"].append(quality.get("score", 0.0))
            data["Quality Assurance Level"].append(quality.get("level", 1))
            data["Deployment Pipeline Score"].append(deployment.get("score", 0.0))
            data["Deployment Pipeline Level"].append(deployment.get("level", 1))
            data["Security Compliance Score"].append(security.get("score", 0.0))
            data["Security Compliance Level"].append(security.get("level", 1))
            data["Collaboration Metrics Score"].append(collaboration.get("score", 0.0))
            data["Collaboration Metrics Level"].append(collaboration.get("level", 1))
        
        return pd.DataFrame(data)

    def export_assessment(
        self, assessment_data: Dict[str, Any], output_path: Optional[str] = None
    ) -> Dict[str, str]:
        """Export the assessment data to Excel and JSON files.
        
        Args:
            assessment_data: Assessment data to export
            output_path: Optional path for the output files
            
        Returns:
            Dictionary with paths to the created Excel and JSON files
        """
        if not isinstance(assessment_data, dict):
            logger.error("Assessment data must be a dictionary")
            return {}
            
        try:
            # Create output directory if needed
            if output_path:
                output_dir = Path(output_path).parent
            else:
                output_dir = Path("exports") / self.timestamp
            output_dir.mkdir(parents=True, exist_ok=True)

            # Get primary project name for file naming
            project_name = self._get_primary_project_name(assessment_data)
            base_name = f"metrics_{project_name}_maturity_assessment"
            
            # Generate file paths
            if output_path:
                excel_path = Path(output_path)
                json_path = excel_path.with_suffix(".json")
            else:
                excel_path = output_dir / f"{base_name}.xlsx"
                json_path = output_dir / f"{base_name}.json"

            # Create DataFrames for each sheet
            overall_score_df = self._create_overall_score_df(assessment_data)
            maturity_assessment_df = self._create_maturity_assessment_df(assessment_data)

            # Export to Excel with formatting
            with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
                # Export Overall Maturity Score sheet
                overall_score_df.to_excel(
                    writer, sheet_name="Overall Maturity Score", index=False
                )
                sheet = writer.sheets["Overall Maturity Score"]
                
                # Format headers
                for cell in sheet[1]:
                    cell.font = openpyxl.styles.Font(bold=True)
                    cell.fill = openpyxl.styles.PatternFill(
                        start_color='E6E6E6',
                        end_color='E6E6E6',
                        fill_type='solid'
                    )
                
                # Format column widths
                sheet.column_dimensions['A'].width = 30  # Project
                sheet.column_dimensions['B'].width = 30  # Repository
                sheet.column_dimensions['C'].width = 15  # Overall Score
                sheet.column_dimensions['D'].width = 10  # Level
                sheet.column_dimensions['E'].width = 15  # Level Label
                # Format Score Explanation column
                sheet.column_dimensions['F'].width = 100  # Score Explanation
                
                # Enable text wrapping and set alignment for Score Explanation column
                for row in range(2, sheet.max_row + 1):
                    cell = sheet[f'F{row}']
                    cell.alignment = openpyxl.styles.Alignment(
                        wrap_text=True,
                        vertical='top',
                        horizontal='left'
                    )
                
                # Format score cells
                for row in range(2, sheet.max_row + 1):
                    score_cell = sheet[f'C{row}']
                    score_cell.number_format = '0.00%'
                    
                    # Color coding based on score
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
                    
                    score_cell.fill = openpyxl.styles.PatternFill(
                        start_color=color,
                        end_color=color,
                        fill_type='solid'
                    )
                
                # Export Maturity Assessment sheet
                maturity_assessment_df.to_excel(
                    writer, sheet_name="Maturity Assessment", index=False
                )
                sheet = writer.sheets["Maturity Assessment"]
                
                # Format headers
                for cell in sheet[1]:
                    cell.font = openpyxl.styles.Font(bold=True)
                    cell.fill = openpyxl.styles.PatternFill(
                        start_color='E6E6E6',
                        end_color='E6E6E6',
                        fill_type='solid'
                    )
                
                # Format column widths
                sheet.column_dimensions['A'].width = 30  # Repository
                sheet.column_dimensions['B'].width = 30  # Project
                
                # Format score columns as percentage with color coding
                score_columns = ['C', 'F', 'H', 'J', 'L', 'N']  # Score columns
                for col in score_columns:
                    sheet.column_dimensions[col].width = 15
                    for row in range(2, sheet.max_row + 1):
                        score_cell = sheet[f'{col}{row}']
                        score_cell.number_format = '0.00%'
                        
                        # Color coding based on score
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
                        
                        score_cell.fill = openpyxl.styles.PatternFill(
                            start_color=color,
                            end_color=color,
                            fill_type='solid'
                        )
                
                # Format level columns
                level_columns = ['D', 'E', 'G', 'I', 'K', 'M', 'O']
                for col in level_columns:
                    sheet.column_dimensions[col].width = 10

            # Export to JSON
            with open(json_path, 'w') as f:
                json.dump(assessment_data, f, indent=2)

            return {
                'excel': str(excel_path),
                'json': str(json_path)
            }

        except Exception as e:
            logger.error(f"Error exporting data: {str(e)}")
            return {}
