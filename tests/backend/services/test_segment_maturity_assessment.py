"""Test Segment Maturity Assessment Service"""

from typing import Dict, Any, List
import pytest
from unittest.mock import Mock
from pathlib import Path
import pandas as pd
import openpyxl
from devops_metrics.backend.services.segment_maturity_assessment import (
    SegmentMaturityAssessmentService,
)


@pytest.fixture
def mock_ado_client() -> Mock:
    """Create mock ADO client"""
    client = Mock()
    client.base_url = "https://dev.azure.com/org"
    client.headers = {"Authorization": "Bearer token"}
    return client


@pytest.fixture
def segment_service(mock_ado_client: Mock) -> SegmentMaturityAssessmentService:
    """Create segment maturity service instance"""
    return SegmentMaturityAssessmentService(mock_ado_client)


@pytest.fixture
def sample_maturity_data() -> Dict[str, Any]:
    """Sample maturity assessment data"""
    return {
        "project_name": "Project1",
        "repositories": {
            "repo1": {
                "name": "repo1", 
                "overall": {"score": 0.75},
                "project": {
                    "id": "Project1",
                    "name": "Project 1"
                }
            },
            "repo2": {
                "name": "repo2", 
                "overall": {"score": 0.85},
                "project": {
                    "id": "Project1",
                    "name": "Project 1"
                }
            },
        }
    }


@pytest.fixture
def sample_group_entitlements() -> List[Dict[str, Any]]:
    """Sample security group entitlements data"""
    return [
        {"displayName": "Group1", "projects": ["Project1", "Project2"]},
        {"displayName": "Group2", "projects": ["Project1"]},
    ]


def test_calculate_project_maturity(
    segment_service: SegmentMaturityAssessmentService,
    sample_maturity_data: Dict[str, Any]
) -> None:
    """Test calculating project maturity scores"""
    result = segment_service.calculate_project_maturity(sample_maturity_data)

    assert len(result) == 1  # One project
    assert "Project1" in result

    # Project1 should have average of repo1 (0.75) and repo2 (0.85)
    assert result["Project1"]["maturity_score"] == pytest.approx(0.8, 0.01)
    assert result["Project1"]["repository_count"] == 2
    assert set(result["Project1"]["repositories"]) == {"repo1", "repo2"}


def test_calculate_project_maturity_no_project_name(
    segment_service: SegmentMaturityAssessmentService
) -> None:
    """Test calculating project maturity with missing project name"""
    maturity_data = {
        "repositories": {
            "repo1": {"name": "repo1", "overall": {"score": 0.75}},
        }
    }
    result = segment_service.calculate_project_maturity(maturity_data)
    assert result == {}


def test_calculate_project_maturity_empty_repositories(
    segment_service: SegmentMaturityAssessmentService
) -> None:
    """Test calculating project maturity with empty repositories"""
    maturity_data = {
        "project_name": "Project1",
        "repositories": {}
    }
    result = segment_service.calculate_project_maturity(maturity_data)
    assert "Project1" not in result


def test_calculate_project_maturity_missing_score(
    segment_service: SegmentMaturityAssessmentService
) -> None:
    """Test calculating project maturity with missing score data"""
    maturity_data = {
        "project_name": "Project1",
        "repositories": {
            "repo1": {
                "name": "repo1",  # Missing overall/score
                "project": {
                    "id": "Project1",
                    "name": "Project 1"
                }
            },
            "repo2": {
                "name": "repo2", 
                "overall": {"score": 0.85},
                "project": {
                    "id": "Project1",
                    "name": "Project 1"
                }
            },
        }
    }
    result = segment_service.calculate_project_maturity(maturity_data)
    assert "Project1" in result
    # When a repository is missing a score, it defaults to 0
    assert result["Project1"]["maturity_score"] == pytest.approx(0.425, 0.01)  # (0 + 0.85) / 2
    assert result["Project1"]["repository_count"] == 2


def test_calculate_segment_maturity(
    segment_service: SegmentMaturityAssessmentService,
    sample_group_entitlements: List[Dict[str, Any]]
) -> None:
    """Test calculating segment maturity scores"""
    project_scores = {
        "Project1": {
            "name": "Project1",
            "maturity_score": 0.8, 
            "repository_count": 2
        },
        "Project2": {
            "name": "Project2",
            "maturity_score": 0.65, 
            "repository_count": 1
        },
    }

    result = segment_service.calculate_segment_maturity(project_scores, sample_group_entitlements)

    assert len(result) == 2  # Two security groups
    assert "Group1" in result
    assert "Group2" in result

    # Group1 has both projects, average should be (0.8 + 0.65) / 2
    assert result["Group1"]["maturity_score"] == pytest.approx(0.725, 0.01)
    assert result["Group1"]["project_count"] == 2

    # Group2 has only Project1
    assert result["Group2"]["maturity_score"] == pytest.approx(0.8, 0.01)
    assert result["Group2"]["project_count"] == 1


def test_calculate_segment_maturity_empty_groups(
    segment_service: SegmentMaturityAssessmentService
) -> None:
    """Test calculating segment maturity with empty groups"""
    project_scores = {
        "Project1": {"maturity_score": 0.8, "repository_count": 2},
    }
    result = segment_service.calculate_segment_maturity(project_scores, [])
    assert result == {}


def test_calculate_segment_maturity_no_valid_projects(
    segment_service: SegmentMaturityAssessmentService
) -> None:
    """Test calculating segment maturity with no valid projects"""
    project_scores = {
        "Project1": {"maturity_score": 0.8, "repository_count": 2},
    }
    group_entitlements = [
        {"displayName": "Group1", "projects": ["Project2"]},  # Project2 not in scores
    ]
    result = segment_service.calculate_segment_maturity(project_scores, group_entitlements)
    assert "Group1" not in result


def test_export_to_excel(
    segment_service: SegmentMaturityAssessmentService,
    tmp_path: Path
) -> None:
    """Test exporting segment maturity data to Excel"""
    project_scores = {
        "Project1": {
            "name": "Project 1",
            "maturity_score": 0.8,
            "repository_count": 2,
            "repositories": ["repo1", "repo2"],
        }
    }

    segment_scores = {
        "Group1": {
            "maturity_score": 0.8,
            "project_count": 1,
            "projects": ["Project1"],
            "description": "Test Group",
            "licenseType": "Basic"
        }
    }

    output_path = tmp_path / "segment_maturity.xlsx"
    segment_service.export_to_excel(project_scores, segment_scores, output_path)

    assert output_path.exists()
    
    # Verify Excel file contents
    wb = openpyxl.load_workbook(output_path)
    
    # Check Project Maturity sheet
    assert "Project Maturity" in wb.sheetnames
    ws_projects = wb["Project Maturity"]
    assert ws_projects["A1"].value == "Project Name"
    assert ws_projects["B1"].value == "Maturity Score"
    assert ws_projects["A2"].value == "Project 1"
    assert float(ws_projects["B2"].value) == pytest.approx(0.8, 0.01)

    # Check Segment Maturity sheet
    assert "Segment Maturity" in wb.sheetnames
    ws_groups = wb["Segment Maturity"]
    assert ws_groups["A1"].value == "Security Group Name"
    assert ws_groups["B1"].value == "Maturity Score"
    assert ws_groups["A2"].value == "Group1"
    assert float(ws_groups["B2"].value) == pytest.approx(0.8, 0.01)


@pytest.fixture
def scoring_service() -> Mock:
    mock = Mock()
    mock.determine_maturity_level.side_effect = lambda score: 1 if score < 0.2 else 2 if score < 0.4 else 3 if score < 0.6 else 4 if score < 0.8 else 5
    mock.get_maturity_label.side_effect = lambda level: f"Level {level}"
    return mock


@pytest.fixture
def service(scoring_service: Mock) -> SegmentMaturityAssessmentService:
    svc = SegmentMaturityAssessmentService(ado_client=Mock())
    svc.scoring_service = scoring_service
    return svc


def test_calculate_project_maturity_empty(service: SegmentMaturityAssessmentService) -> None:
    result = service.calculate_project_maturity({"repositories": {}})
    assert result == {}


def test_calculate_project_maturity_single(service: SegmentMaturityAssessmentService) -> None:
    maturity_data = {
        "repositories": {
            "repo1": {
                "project": {"id": "p1", "name": "Project1"},
                "name": "Repo1",
                "overall": {"score": 0.75}
            }
        }
    }
    result = service.calculate_project_maturity(maturity_data)
    assert "p1" in result
    assert result["p1"]["name"] == "Project1"
    assert result["p1"]["maturity_score"] == 0.75
    assert result["p1"]["repository_count"] == 1


def test_calculate_project_maturity_multiple(service: SegmentMaturityAssessmentService) -> None:
    maturity_data = {
        "repositories": {
            "repo1": {
                "project": {"id": "p1", "name": "Project1"},
                "name": "Repo1",
                "overall": {"score": 0.5}
            },
            "repo2": {
                "project": {"id": "p1", "name": "Project1"},
                "name": "Repo2",
                "overall": {"score": 1.0}
            },
            "repo3": {
                "project": {"id": "p2", "name": "Project2"},
                "name": "Repo3",
                "overall": {"score": 0.2}
            }
        }
    }
    result = service.calculate_project_maturity(maturity_data)
    assert set(result.keys()) == {"p1", "p2"}
    assert result["p1"]["maturity_score"] == 0.75
    assert result["p2"]["maturity_score"] == 0.2


def test_calculate_segment_maturity_empty(service: SegmentMaturityAssessmentService) -> None:
    result = service.calculate_segment_maturity({}, [])
    assert result == {}


def test_calculate_segment_maturity_single(service: SegmentMaturityAssessmentService) -> None:
    project_scores = {
        "p1": {"name": "Project1", "maturity_score": 0.8, "repository_count": 2}
    }
    group_entitlements = [
        {"displayName": "Group1", "projects": ["Project1"], "description": "desc", "licenseType": "type", "originId": "g1"}
    ]
    result = service.calculate_segment_maturity(project_scores, group_entitlements)
    assert "Group1" in result
    group = result["Group1"]
    assert group["maturity_score"] == 0.8
    assert group["project_count"] == 1
    assert group["projects"] == ["Project1"]
    assert group["description"] == "desc"
    assert group["licenseType"] == "type"
    assert group["originId"] == "g1"


def test_calculate_segment_maturity_multiple(service: SegmentMaturityAssessmentService) -> None:
    project_scores = {
        "p1": {"name": "Project1", "maturity_score": 0.8, "repository_count": 2},
        "p2": {"name": "Project2", "maturity_score": 0.4, "repository_count": 1}
    }
    group_entitlements = [
        {"displayName": "Group1", "projects": ["Project1", "Project2"], "description": "desc", "licenseType": "type", "originId": "g1"}
    ]
    result = service.calculate_segment_maturity(project_scores, group_entitlements)
    assert "Group1" in result
    group = result["Group1"]
    assert group["maturity_score"] == 0.6
    assert group["project_count"] == 2
    assert set(group["projects"]) == {"Project1", "Project2"}
    assert group["statistics"]["min_score"] == 0.4
    assert group["statistics"]["max_score"] == 0.8
    assert group["statistics"]["avg_score"] == 0.6
    assert group["statistics"]["total_repositories"] == 3
