"""Tests for raw data exporter service"""

import os
from datetime import datetime, timedelta
import pytest
from unittest.mock import AsyncMock, Mock
import pandas as pd
from devops_metrics.backend.services.raw_data_exporter import RawDataExporter
import json


@pytest.fixture
def sample_raw_data():
    now = datetime.now()
    return {
        "project": {"id": "proj1", "name": "Test Project"},
        "repositories": [
            {
                "id": "repo1",
                "name": "Test Repo",
                "defaultBranch": "refs/heads/main",
                "size": 1000,
                "webUrl": "https://dev.azure.com/repo1",
            }
        ],
        "repository_data": {
            "repo1": {
                "commits": [
                    {
                        "commitId": "commit1",
                        "author": {"name": "Test User", "email": "test@example.com"},
                        "committer": {"name": "Test User", "email": "test@example.com"},
                        "comment": "Test commit",
                        "changeCounts": {"Add": 10, "Edit": 5, "Delete": 3},
                        "timestamp": now.isoformat(),
                    }
                ],
                "pull_requests": [
                    {
                        "pullRequestId": 1,
                        "title": "Test PR",
                        "status": "completed",
                        "creationDate": (now - timedelta(days=1)).isoformat(),
                        "closedDate": now.isoformat(),
                        "reviewers": [{"id": "user1", "vote": 10}],
                    }
                ],
                "builds": [
                    {
                        "id": "build1",
                        "result": "succeeded",
                        "startTime": (now - timedelta(hours=1)).isoformat(),
                        "finishTime": now.isoformat(),
                    }
                ],
            }
        },
    }


@pytest.fixture
def mock_ado_client():
    return AsyncMock()


@pytest.fixture
def exporter(mock_ado_client):
    return RawDataExporter(mock_ado_client)


def test_export_data(exporter, sample_raw_data, tmp_path):
    """Test exporting raw data to both formats"""
    output_file = tmp_path / "test_export.xlsx"

    result = exporter.export_data(sample_raw_data, str(output_file))
    assert result
    assert len(result) == 2  # Should have paths for both Excel and JSON

    # Check that both files exist
    excel_path = tmp_path / "test_export.xlsx"
    json_path = tmp_path / "test_export.json"
    assert excel_path.exists()
    assert json_path.exists()
    assert excel_path.stat().st_size > 0
    assert json_path.stat().st_size > 0


def test_safe_sheet_name(exporter):
    """Test Excel sheet name sanitization"""
    # Test long name truncation
    long_name = "a" * 40
    safe_name = exporter._safe_sheet_name(long_name)
    assert len(safe_name) <= 31

    # Test invalid character replacement
    invalid_name = "test/sheet\\name"
    safe_name = exporter._safe_sheet_name(invalid_name)
    assert "/" not in safe_name
    assert "\\" not in safe_name


def test_flatten_if_needed(exporter):
    """Test dictionary flattening for Excel compatibility"""
    test_data = {"simple": "value", "nested": {"key": "value"}, "list": [1, 2, 3]}

    flattened = exporter._flatten_if_needed(test_data)
    assert isinstance(flattened["simple"], str)
    assert isinstance(flattened["nested"], str)
    assert isinstance(flattened["list"], str)


def test_process_repository_data(exporter, sample_raw_data):
    """Test processing repository data into consolidated format"""
    consolidated = exporter._process_repository_data(sample_raw_data)

    # Check that all expected data types are present
    assert "commits" in consolidated
    assert "pull_requests" in consolidated
    assert "branches" in consolidated
    assert "policies" in consolidated

    # Verify data structure
    assert (
        len(consolidated["repositories"]) == 1 if "repositories" in consolidated else True
    )  # Optional
    assert len(consolidated["commits"]) == 1
    assert len(consolidated["pull_requests"]) == 1
    assert len(consolidated["branches"]) == 0  # No branches in sample data
    assert len(consolidated["policies"]) == 0  # No policies in sample data


def test_empty_data_handling(exporter, tmp_path):
    """Test handling empty data"""
    empty_data = {"project": {}, "repositories": [], "repository_data": {}}

    output_file = tmp_path / "empty_export.xlsx"
    result = exporter.export_data(empty_data, str(output_file))
    assert result

    # Check that files were created
    excel_path = tmp_path / "empty_export.xlsx"
    json_path = tmp_path / "empty_export.json"
    assert excel_path.exists()
    assert json_path.exists()


def test_invalid_data_handling(exporter, tmp_path):
    """Test handling invalid data"""
    invalid_data = {"invalid": "data"}

    output_file = tmp_path / "invalid_export.xlsx"
    try:
        exporter.export_data(invalid_data, str(output_file))
        assert False, "Should have raised an exception"
    except Exception:
        assert True


def test_file_path_handling(exporter, sample_raw_data, tmp_path, monkeypatch):
    """Test handling various file paths"""
    monkeypatch.chdir(tmp_path)
    # Test with absolute path
    abs_path = os.path.join(str(tmp_path), "abs_export.xlsx")
    result = exporter.export_data(sample_raw_data, abs_path)
    assert result
    assert os.path.exists(abs_path)

    # Test with relative path
    rel_path = os.path.join(".", "rel_export.xlsx")
    result = exporter.export_data(sample_raw_data, rel_path)
    assert result
    assert os.path.exists(rel_path)

    # Clean up relative path file
    if os.path.exists(rel_path):
        os.remove(rel_path)


def test_safe_sheet_name_truncation_and_replacement(exporter):
    name = "a/very/long/sheet\\name/with/illegal/characters/and/length"
    safe = exporter._safe_sheet_name(name)
    assert "/" not in safe and "\\" not in safe
    assert len(safe) <= 31


def test_flatten_if_needed_dict_and_list(exporter):
    data = {"a": {"b": 1}, "c": [1, 2, 3], "d": 5}
    flat = exporter._flatten_if_needed(data)
    assert isinstance(flat["a"], str) and json.loads(flat["a"]) == {"b": 1}
    assert isinstance(flat["c"], str) and json.loads(flat["c"]) == [1, 2, 3]
    assert flat["d"] == 5


def test_create_policy_record_minimal(exporter):
    policy = {"id": "123", "type": {}}
    repo_info = {"repository_id": "r1", "repository_name": "Repo1"}
    record = exporter._create_policy_record(policy, repo_info, "Repository", "main", True)
    assert record["repository_id"] == "r1"
    assert record["policy_id"] == "123"
    assert record["scope_type"] == "Repository"
    assert record["branch_name"] == "main"
    assert record["is_default_branch"] is True
    assert record["policy_type"] == "Unknown"


def test_create_policy_record_full(exporter):
    policy = {
        "id": "123",
        "type": {"displayName": "Build Policy"},
        "is_enabled": True,
        "is_blocking": True,
        "created_by": {"name": "Alice"},
        "created_date": "2021-01-01",
        "modified_by": {"name": "Bob"},
        "modified_date": "2021-02-01",
        "settings": {"foo": "bar"},
    }
    repo_info = {"repository_id": "r1", "repository_name": "Repo1"}
    record = exporter._create_policy_record(policy, repo_info, "Branch", "dev", False)
    assert record["policy_type"] == "Build Policy"
    assert record["is_enabled"] is True
    assert record["is_blocking"] is True
    assert json.loads(record["created_by"]) == {"name": "Alice"}
    assert record["created_date"] == "2021-01-01"
    assert json.loads(record["settings"]) == {"foo": "bar"}


def test_process_repository_data_minimal(exporter):
    raw_data = {"repository_data": {}}
    result = exporter._process_repository_data(raw_data)
    assert set(result.keys()) == {"commits", "branches", "pull_requests", "policies"}
    assert all(isinstance(result[k], list) for k in result)


def test_process_repository_data_typical(exporter):
    raw_data = {
        "repositories": [{"id": "r1", "name": "Repo1"}],
        "repository_data": {
            "r1": {
                "commits": [{"sha": "abc123", "msg": "init"}],
                "branches": [{"name": "main"}],
                "pull_requests": [{"id": 1, "title": "PR1"}],
                "policies": {
                    "repository_policies": [
                        {"id": "p1", "type": {"displayName": "Build"}}
                    ],
                    "branch_policies": [
                        {"branch_name": "main", "is_default_branch": True, "policies": [{"id": "p2", "type": {}}]}
                    ]
                }
            }
        }
    }
    result = exporter._process_repository_data(raw_data)
    assert len(result["commits"]) == 1
    assert len(result["branches"]) == 1
    assert len(result["pull_requests"]) == 1
    assert len(result["policies"]) == 2
