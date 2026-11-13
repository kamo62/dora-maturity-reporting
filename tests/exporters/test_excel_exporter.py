import pandas as pd
import pytest
from pathlib import Path

from devops_metrics.exporters.excel_exporter import ExcelExporter
from devops_metrics.types.export_types import ExportOptions


@pytest.fixture
def sample_assessment():
    return {
        "project_name": "MyProject",
        "repositories": {
            "repo1": {
                "name": "Repo1",
                "overall": {
                    "score": 0.75,
                    "level": 3,
                    "label": "Medium",
                    "explanation": ["Explanation line 1", "Explanation line 2"]
                },
                "quality_assurance": {"score": 0.8, "level": 4},
                "deployment_pipeline": {"score": 0.6, "level": 2},
                "security_compliance": {"score": 0.5, "level": 2},
                "collaboration_metrics": {"score": 0.4, "level": 1}
            }
        }
    }


def test_create_overall_score_df(sample_assessment):
    exporter = ExcelExporter()
    df = exporter._create_overall_score_df(sample_assessment)
    assert list(df.columns) == [
        "Project", "Repository", "Overall Score", "Level", "Level Label", "Score Explanation"
    ]
    assert df.iloc[0]["Project"] == "MyProject"
    assert df.iloc[0]["Repository"] == "Repo1"
    assert df.iloc[0]["Overall Score"] == 0.75
    assert df.iloc[0]["Level"] == 3
    assert df.iloc[0]["Level Label"] == "Medium"
    assert "• Explanation line 1" in df.iloc[0]["Score Explanation"]


def test_create_maturity_assessment_df(sample_assessment):
    exporter = ExcelExporter()
    df = exporter._create_maturity_assessment_df(sample_assessment)
    expected_cols = [
        "Repository", "Project", "Overall Score", "Overall Level", "Overall Label",
        "Quality Assurance Score", "Quality Assurance Level",
        "Deployment Pipeline Score", "Deployment Pipeline Level",
        "Security Compliance Score", "Security Compliance Level",
        "Collaboration Metrics Score", "Collaboration Metrics Level"
    ]
    assert list(df.columns) == expected_cols
    row = df.iloc[0]
    assert row["Project"] == "MyProject"
    assert row["Repository"] == "Repo1"
    assert row["Overall Score"] == 0.75
    assert row["Overall Level"] == 3
    assert row["Overall Label"] == "Medium"
    assert row["Quality Assurance Score"] == 0.8
    assert row["Quality Assurance Level"] == 4
    assert row["Deployment Pipeline Score"] == 0.6
    assert row["Deployment Pipeline Level"] == 2
    assert row["Security Compliance Score"] == 0.5
    assert row["Security Compliance Level"] == 2
    assert row["Collaboration Metrics Score"] == 0.4
    assert row["Collaboration Metrics Level"] == 1


def test_export_creates_excel_file(sample_assessment, tmp_path):
    exporter = ExcelExporter()
    options: ExportOptions = {
        "format": "excel",
        "include_charts": False,
        "include_explanations": False
    }
    output_file = tmp_path / "output.xlsx"
    result = exporter.export(sample_assessment, str(output_file), options)
    assert result["path"] == str(output_file)
    assert Path(result["path"]).exists()

    # Read the created Excel file
    xls = pd.read_excel(result["path"], sheet_name=None)
    assert "Overall Maturity Score" in xls
    assert "Maturity Assessment" in xls

    overall = xls["Overall Maturity Score"]
    assert overall.iloc[0]["Project"] == "MyProject"
    assert overall.iloc[0]["Overall Score"] == 0.75

    maturity = xls["Maturity Assessment"]
    assert maturity.iloc[0]["Repository"] == "Repo1"
