"""Tests for maturity assessment exporter service"""

import os
from datetime import datetime
import pytest
from unittest.mock import AsyncMock
import pandas as pd
from devops_metrics.backend.services.maturity_assessment_exporter import MaturityAssessmentExporter


@pytest.fixture
def sample_assessment_data():
    """Sample maturity assessment data for testing"""
    return {
        "project_name": "Test Project",
        "repositories": {
            "repo1": {
                "name": "Test Repo",
                "quality_assurance": {
                    "score": 0.92,
                    "level": 5,
                    "metrics": {
                        "code_coverage": 72.09,
                        "test_pass_rate": 100.0,
                        "build_success_rate": 51.89
                    },
                    "explanation": [
                        "Code Coverage (72.1%) meets Level 4 threshold",
                        "Test Pass Rate (100.0%) meets Level 5 threshold"
                    ]
                },
                "deployment_pipeline": {
                    "score": 0.60,
                    "level": 3,
                    "metrics": {
                        "build_success_rate": 51.89,
                        "deployment_frequency": 3.73
                    },
                    "explanation": [
                        "Build Success Rate (51.9%) meets Level 2 threshold",
                        "Deployment Frequency (3.7 per day) meets Level 4 threshold"
                    ]
                },
                "security_compliance": {
                    "score": 0.0,
                    "level": 1,
                    "metrics": {},
                    "explanation": ["No policy data available"]
                },
                "collaboration_metrics": {
                    "score": 0.0,
                    "level": 1,
                    "metrics": {},
                    "explanation": ["No pull request data available"]
                },
                "overall": {
                    "score": 0.41,
                    "level": 3,
                    "label": "Standardized",
                    "explanation": [
                        "Quality Assurance score: 0.92 (Level 5)",
                        "Deployment Pipeline score: 0.60 (Level 3)",
                        "Missing security compliance assessment",
                        "Missing collaboration metrics assessment"
                    ]
                }
            }
        }
    }


@pytest.fixture
def mock_ado_client():
    """Mock Azure DevOps client"""
    return AsyncMock()


@pytest.fixture
def exporter(mock_ado_client):
    """Create MaturityAssessmentExporter instance"""
    return MaturityAssessmentExporter(mock_ado_client)


def test_export_data(exporter, sample_assessment_data, tmp_path):
    """Test exporting assessment data to both formats"""
    output_file = tmp_path / "test_export.xlsx"

    result = exporter.export_assessment(sample_assessment_data, str(output_file))
    assert result
    assert len(result) == 2  # Should have paths for both Excel and JSON

    # Check that both files exist
    excel_path = tmp_path / "test_export.xlsx"
    json_path = tmp_path / "test_export.json"
    assert excel_path.exists()
    assert json_path.exists()
    assert excel_path.stat().st_size > 0
    assert json_path.stat().st_size > 0

    # Verify Excel content
    excel_data = pd.read_excel(excel_path, sheet_name=None)
    
    # Check Overall Maturity Score sheet
    assert "Overall Maturity Score" in excel_data
    overall_sheet = excel_data["Overall Maturity Score"]
    assert "Project" in overall_sheet.columns
    assert "Repository" in overall_sheet.columns
    assert "Overall Score" in overall_sheet.columns
    assert "Level" in overall_sheet.columns
    assert "Level Label" in overall_sheet.columns
    assert "Score Explanation" in overall_sheet.columns
    
    # Check values
    assert overall_sheet["Overall Score"].iloc[0] == 0.41
    assert overall_sheet["Level"].iloc[0] == 3
    assert overall_sheet["Level Label"].iloc[0] == "Standardized"
    
    # Check Maturity Assessment sheet
    assert "Maturity Assessment" in excel_data
    maturity_sheet = excel_data["Maturity Assessment"]
    assert "Repository" in maturity_sheet.columns
    assert "Project" in maturity_sheet.columns
    assert "Overall Score" in maturity_sheet.columns
    assert "Overall Level" in maturity_sheet.columns
    
    # Check values
    assert maturity_sheet["Overall Score"].iloc[0] == 0.41
    assert maturity_sheet["Overall Level"].iloc[0] == 3


def test_empty_assessment(exporter, tmp_path):
    """Test handling empty assessment data"""
    empty_data = {}
    output_file = tmp_path / "empty_export.xlsx"

    result = exporter.export_assessment(empty_data, str(output_file))
    assert result
    
    # Check that files were created
    excel_path = tmp_path / "empty_export.xlsx"
    json_path = tmp_path / "empty_export.json"
    assert excel_path.exists()
    assert json_path.exists()

    # Verify Excel content has empty DataFrames
    excel_data = pd.read_excel(excel_path, sheet_name=None)
    assert "Overall Maturity Score" in excel_data
    assert "Maturity Assessment" in excel_data
    assert len(excel_data["Overall Maturity Score"]) == 0
    assert len(excel_data["Maturity Assessment"]) == 0


def test_partial_assessment(exporter, sample_assessment_data, tmp_path):
    """Test handling partial assessment data"""
    # Remove some sections but keep the repository structure
    partial_data = {
        "project_name": "Test Project",
        "repositories": {
            "repo1": {
                "name": "Test Repo",
                "quality_assurance": sample_assessment_data["repositories"]["repo1"]["quality_assurance"],
                "overall": sample_assessment_data["repositories"]["repo1"]["overall"]
            }
        }
    }

    output_file = tmp_path / "partial_export.xlsx"
    result = exporter.export_assessment(partial_data, str(output_file))
    assert result

    # Verify Excel content
    excel_data = pd.read_excel(output_file, sheet_name=None)
    assert "Overall Maturity Score" in excel_data
    assert "Maturity Assessment" in excel_data


def test_file_path_handling(exporter, sample_assessment_data, tmp_path, monkeypatch):
    """Test handling various file paths"""
    monkeypatch.chdir(tmp_path)
    # Test with absolute path
    abs_path = os.path.join(str(tmp_path), "abs_export.xlsx")
    result = exporter.export_assessment(sample_assessment_data, abs_path)
    assert result
    assert os.path.exists(abs_path)

    # Test with relative path
    rel_path = "rel_export.xlsx"
    result = exporter.export_assessment(sample_assessment_data, rel_path)
    assert result
    assert os.path.exists(rel_path)
    
    # Clean up relative path file
    if os.path.exists(rel_path):
        os.remove(rel_path)


def test_invalid_data_handling(exporter, tmp_path):
    """Test handling invalid data"""
    invalid_data = None
    output_file = tmp_path / "invalid_export.xlsx"
    
    result = exporter.export_assessment(invalid_data, str(output_file))
    assert result == {}


def test_create_overall_score_df(exporter, sample_assessment_data):
    """Test creation of Overall Maturity Score DataFrame"""
    df = exporter._create_overall_score_df(sample_assessment_data)
    
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    assert all(col in df.columns for col in [
        "Project", "Repository", "Overall Score", 
        "Level", "Level Label", "Score Explanation"
    ])


def test_create_maturity_assessment_df(exporter, sample_assessment_data):
    """Test creation of Maturity Assessment DataFrame"""
    df = exporter._create_maturity_assessment_df(sample_assessment_data)
    
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    assert all(col in df.columns for col in [
        "Repository", "Project", "Overall Score", "Overall Level",
        "Overall Label"
    ])
