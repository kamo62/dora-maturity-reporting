"""Excel exporter implementation."""
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

import pandas as pd
from openpyxl.worksheet.worksheet import Worksheet
from openpyxl.utils.cell import get_column_letter

from .base_exporter import BaseExporter
from .formatters.base_formatter import ScoreFormatter, ExplanationFormatter, HeaderFormatter
from ..types.export_types import ExportOptions, ExportResult
from ..types.assessment_types import ProjectAssessment
from ..utils.logging import get_logger

logger = get_logger(__name__)


class ExcelExporter(BaseExporter):
    """Excel exporter implementation."""

    def __init__(self) -> None:
        """Initialize formatters."""
        self.formatters = {
            "score": ScoreFormatter(),
            "explanation": ExplanationFormatter(),
            "header": HeaderFormatter()
        }

    def _create_overall_score_df(self, assessment_data: ProjectAssessment) -> pd.DataFrame:
        """Create DataFrame for Overall Maturity Score sheet.
        
        Args:
            assessment_data: Assessment data
            
        Returns:
            DataFrame with overall scores
        """
        data: Dict[str, List[Any]] = {
            "Project": [],
            "Repository": [],
            "Overall Score": [],
            "Level": [],
            "Level Label": [],
            "Score Explanation": []
        }
        
        # Extract project name
        project_name = assessment_data["project_name"]

        # Get repositories data
        repositories = assessment_data.get("repositories", {})
        
        # Create a row for each repository
        for repo_id, repo_data in repositories.items():
            repo_name = repo_data.get("name", repo_id)
            overall = repo_data.get("overall", {})
            
            data["Project"].append(project_name)
            data["Repository"].append(repo_name)
            data["Overall Score"].append(overall.get("score", 0.0))
            data["Level"].append(overall.get("level", 1))
            data["Level Label"].append(overall.get("label", "Initial"))
            explanations = overall.get("explanation", [])
            formatted_explanations = "\n".join(f"• {exp}" for exp in explanations)
            data["Score Explanation"].append(formatted_explanations)
        
        return pd.DataFrame(data)

    def _create_maturity_assessment_df(self, assessment_data: ProjectAssessment) -> pd.DataFrame:
        """Create DataFrame for Maturity Assessment sheet.
        
        Args:
            assessment_data: Assessment data
            
        Returns:
            DataFrame with detailed assessment
        """
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
        
        # Extract project name
        project_name = assessment_data["project_name"]

        # Get repositories data
        repositories = assessment_data.get("repositories", {})
        
        # Create a row for each repository
        for repo_id, repo_data in repositories.items():
            repo_name = repo_data.get("name", repo_id)
            
            # Get dimension data
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

    def _format_worksheet(self, sheet: Worksheet, df: pd.DataFrame, options: ExportOptions) -> None:
        """Format worksheet with styles.
        
        Args:
            sheet: Worksheet to format
            df: DataFrame with data
            options: Export options
        """
        # Format headers
        for col_idx, col_name in enumerate(df.columns, start=1):
            cell = sheet.cell(row=1, column=col_idx)
            header_format = self.formatters["header"].format_cell(col_name)
            cell.value = header_format["value"]
            cell.fill = header_format["fill"]
            cell.font = header_format["font"]
            cell.alignment = header_format["alignment"]
            cell.number_format = header_format["number_format"]
        
        # Format data cells
        for row_idx, row in enumerate(df.itertuples(), start=2):
            for col_idx, value in enumerate(row[1:], start=1):
                cell = sheet.cell(row=row_idx, column=col_idx)
                
                # Apply appropriate formatter based on column type
                col_name = df.columns[col_idx-1]
                if "Explanation" in col_name:
                    format_dict = self.formatters["explanation"].format_cell(str(value))
                elif "Score" in col_name:
                    format_dict = self.formatters["score"].format_cell(float(value))
                elif "Project" in col_name:
                    format_dict = self.formatters["header"].format_cell(str(value))
                else:
                    # Default text formatting
                    format_dict = self.formatters["explanation"].format_cell(str(value))

                cell.value = format_dict["value"]
                cell.fill = format_dict["fill"]
                cell.font = format_dict["font"]
                cell.alignment = format_dict["alignment"]
                cell.number_format = format_dict["number_format"]

        # Adjust column widths
        for col_idx, col in enumerate(df.columns, start=1):
            max_length = max(
                len(str(cell.value)) for cell in sheet[get_column_letter(col_idx)]
            )
            adjusted_width = min(max_length + 2, 100)  # Cap at 100 characters
            sheet.column_dimensions[get_column_letter(col_idx)].width = adjusted_width

    def export(self, data: ProjectAssessment, path: str, options: ExportOptions) -> ExportResult:
        """Export assessment data to Excel.
        
        Args:
            data: Assessment data to export
            path: Path to export to
            options: Export options
            
        Returns:
            Export result
        """
        try:
            # Create output directory if needed
            output_path = Path(path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Create DataFrames
            overall_score_df = self._create_overall_score_df(data)
            maturity_assessment_df = self._create_maturity_assessment_df(data)

            # Create Excel writer
            with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
                # Export Overall Maturity Score sheet
                overall_score_df.to_excel(
                    writer, sheet_name="Overall Maturity Score", index=False
                )
                self._format_worksheet(
                    writer.sheets["Overall Maturity Score"],
                    overall_score_df,
                    options
                )
                
                # Export Maturity Assessment sheet
                maturity_assessment_df.to_excel(
                    writer, sheet_name="Maturity Assessment", index=False
                )
                self._format_worksheet(
                    writer.sheets["Maturity Assessment"],
                    maturity_assessment_df,
                    options
                )

            logger.info(f"Assessment exported to Excel: {output_path}")

            return {
                "path": str(output_path),
                "format": "excel",
                "timestamp": datetime.now().isoformat()
            }

        except Exception as e:
            logger.error(f"Error exporting to Excel: {str(e)}")
            raise
