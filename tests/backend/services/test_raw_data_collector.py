"""Tests for raw data collector service"""

from datetime import datetime, timedelta, timezone
import pytest
from unittest.mock import AsyncMock, MagicMock, Mock
from devops_metrics.backend.services.raw_data_collector import RawDataCollector
from devops_metrics.backend.api.ado_client import AzureDevOpsClient
import asyncio
from typing import Any, Dict


@pytest.fixture
def mock_ado_client() -> Mock:
    client = Mock()
    client.get_project = AsyncMock()
    client.get_repositories = AsyncMock()
    client.get_commits = AsyncMock()
    client.get_branches = AsyncMock()
    client.get_pull_requests = AsyncMock()
    client.get_policy_configurations = AsyncMock()
    client.get_policy_types = AsyncMock()
    client.get_builds = AsyncMock()
    client.get_code_coverage = AsyncMock()
    client.get_test_results = AsyncMock()
    client.get_security_scan_results = AsyncMock()
    return client


@pytest.fixture
def collector_service(mock_ado_client: Mock) -> RawDataCollector:
    return RawDataCollector(mock_ado_client)


@pytest.fixture
def sample_raw_data() -> Dict[str, Any]:
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
                "branches": [
                    {"name": "main", "isBaseVersion": True},
                    {"name": "feature", "isBaseVersion": False},
                ],
                "pull_requests": [
                    {
                        "pullRequestId": 1,
                        "title": "Test PR",
                        "description": "Test description",
                        "sourceRefName": "refs/heads/feature",
                        "targetRefName": "refs/heads/main",
                        "status": "completed",
                        "creationDate": (now - timedelta(days=1)).isoformat(),
                        "closedDate": now.isoformat(),
                        "reviewers": [{"id": "user1", "vote": 10}],
                        "commits": [{"commitId": "commit1"}],
                    }
                ],
                "policies": {
                    "repository_policies": [
                        {
                            "type": {"id": "policy1", "displayName": "Required reviewers"},
                            "isEnabled": True,
                            "isBlocking": True,
                            "settings": {"scope": [{"repositoryId": "repo1"}]},
                        }
                    ],
                    "branch_policies": [],
                    "statistics": {
                        "total_policies": 1,
                        "blocking_policies": 1,
                        "enabled_policies": 1,
                        "policy_types": ["Required reviewers"],
                        "project_level_policies": 0,
                        "repository_level_policies": 1,
                    },
                    "scan_time": datetime.now(timezone.utc).isoformat(),
                },
            }
        },
        "builds": [
            {
                "id": "build1",
                "result": "succeeded",
                "startTime": (now - timedelta(hours=1)).isoformat(),
                "finishTime": now.isoformat(),
            }
        ],
        "build_data": {"build1": {"coverage": {"value": []}, "tests": {"results": []}}},
    }


@pytest.mark.asyncio
async def test_collect_raw_data_empty(collector_service: RawDataCollector, mock_ado_client: Mock) -> None:
    """Test collecting raw data with empty responses"""
    mock_ado_client.get_project.return_value = {}
    with pytest.raises(ValueError, match="Project not found: project"):
        await collector_service.collect_all_raw_data("project")


@pytest.mark.asyncio
async def test_collect_raw_data(collector_service: RawDataCollector, mock_ado_client: Mock, sample_raw_data: Dict[str, Any]) -> None:
    """Test collecting raw data with sample data"""
    mock_ado_client.get_project.return_value = sample_raw_data["project"]
    mock_ado_client.get_repositories.return_value = sample_raw_data["repositories"]
    mock_ado_client.get_commits.return_value = sample_raw_data["repository_data"]["repo1"][
        "commits"
    ]
    mock_ado_client.get_branches.return_value = sample_raw_data["repository_data"]["repo1"][
        "branches"
    ]
    mock_ado_client.get_pull_requests.return_value = sample_raw_data["repository_data"]["repo1"][
        "pull_requests"
    ]
    mock_ado_client.get_policy_configurations.return_value = [
        {
            "type": {"id": "policy1", "displayName": "Required reviewers"},
            "isEnabled": True,
            "isBlocking": True,
            "settings": {"scope": [{"repositoryId": "repo1"}]},
        }
    ]
    mock_ado_client.get_policy_types.return_value = [
        {"id": "policy1", "displayName": "Required reviewers"}
    ]
    mock_ado_client.get_builds.return_value = sample_raw_data["builds"]
    mock_ado_client.get_code_coverage.return_value = sample_raw_data["build_data"]["build1"][
        "coverage"
    ]
    mock_ado_client.get_test_results.return_value = sample_raw_data["build_data"]["build1"]["tests"]
    mock_ado_client.get_security_scan_results.return_value = []  # Empty security scan results

    result = await collector_service.collect_all_raw_data("project")
    assert isinstance(result, dict)
    assert result["project"]["id"] == sample_raw_data["project"]["id"]
    assert result["project"]["name"] == sample_raw_data["project"]["name"]
    assert result["repositories"] == sample_raw_data["repositories"]
    assert "repository_data" in result
    assert "repo1" in result["repository_data"]
    assert (
        result["repository_data"]["repo1"]["commits"]
        == sample_raw_data["repository_data"]["repo1"]["commits"]
    )
    assert (
        result["repository_data"]["repo1"]["branches"]
        == sample_raw_data["repository_data"]["repo1"]["branches"]
    )
    assert (
        result["repository_data"]["repo1"]["pull_requests"]
        == sample_raw_data["repository_data"]["repo1"]["pull_requests"]
    )
    # Compare everything except scan_time since it's generated at runtime
    result_policies = result["repository_data"]["repo1"]["policies"].copy()
    expected_policies = sample_raw_data["repository_data"]["repo1"]["policies"].copy()
    result_policies.pop("scan_time")
    expected_policies.pop("scan_time")
    assert result_policies == expected_policies


@pytest.mark.asyncio
async def test_collect_repository_data(collector_service: RawDataCollector, mock_ado_client: Mock, sample_raw_data: Dict[str, Any]) -> None:
    """Test collecting repository data"""
    repo = sample_raw_data["repositories"][0]
    mock_ado_client.get_commits.return_value = sample_raw_data["repository_data"]["repo1"][
        "commits"
    ]
    mock_ado_client.get_branches.return_value = sample_raw_data["repository_data"]["repo1"][
        "branches"
    ]
    mock_ado_client.get_pull_requests.return_value = sample_raw_data["repository_data"]["repo1"][
        "pull_requests"
    ]
    mock_ado_client.get_policy_configurations.return_value = [
        {
            "type": {"id": "policy1", "displayName": "Required reviewers"},
            "isEnabled": True,
            "isBlocking": True,
            "settings": {"scope": [{"repositoryId": "repo1"}]},
        }
    ]
    mock_ado_client.get_policy_types.return_value = [
        {"id": "policy1", "displayName": "Required reviewers"}
    ]

    result = await collector_service._get_with_retry(
        collector_service.ado_client.get_commits, "commits", repo["id"], "project"
    )
    assert isinstance(result, list)
    assert result == sample_raw_data["repository_data"]["repo1"]["commits"]


@pytest.mark.asyncio
async def test_collect_repository_data_error(collector_service: RawDataCollector, mock_ado_client: Mock) -> None:
    """Test error handling in repository data collection"""
    mock_ado_client.get_commits.side_effect = Exception("API Error")
    with pytest.raises(TimeoutError, match="Failed to get commits after 3 attempts: API Error"):
        await collector_service._get_with_retry(
            collector_service.ado_client.get_commits, "commits", "invalid", "project"
        )


@pytest.mark.asyncio
async def test_collect_build_data(collector_service: RawDataCollector, mock_ado_client: Mock, sample_raw_data: Dict[str, Any]) -> None:
    """Test collecting build data"""
    mock_ado_client.get_builds.return_value = sample_raw_data["builds"]
    result = await collector_service._get_with_retry(
        collector_service.ado_client.get_builds, "builds", "project"
    )
    assert isinstance(result, list)
    assert result == sample_raw_data["builds"]


@pytest.mark.asyncio
async def test_collect_build_data_error(collector_service: RawDataCollector, mock_ado_client: Mock) -> None:
    """Test error handling in build data collection"""
    mock_ado_client.get_builds.side_effect = Exception("API Error")
    with pytest.raises(TimeoutError, match="Failed to get builds after 3 attempts: API Error"):
        await collector_service._get_with_retry(
            collector_service.ado_client.get_builds, "builds", "project"
        )


@pytest.mark.asyncio
async def test_collect_build_details(collector_service: RawDataCollector, mock_ado_client: Mock, sample_raw_data: Dict[str, Any]) -> None:
    """Test collecting build details"""
    build = sample_raw_data["builds"][0]
    build_details = sample_raw_data["build_data"]["build1"]
    mock_ado_client.get_code_coverage.return_value = build_details["coverage"]
    mock_ado_client.get_test_results.return_value = build_details["tests"]

    coverage_result = await collector_service._get_with_retry(
        collector_service.ado_client.get_code_coverage, "build coverage", "project", build["id"]
    )
    assert isinstance(coverage_result, dict)
    assert coverage_result == build_details["coverage"]

    test_result = await collector_service._get_with_retry(
        collector_service.ado_client.get_test_results, "test results", "project", build["id"]
    )
    assert isinstance(test_result, dict)
    assert test_result == build_details["tests"]


@pytest.mark.asyncio
async def test_collect_build_details_error(collector_service: RawDataCollector, mock_ado_client: Mock) -> None:
    """Test error handling in build details collection"""
    mock_ado_client.get_code_coverage.side_effect = Exception("API Error")
    with pytest.raises(
        TimeoutError, match="Failed to get build coverage after 3 attempts: API Error"
    ):
        await collector_service._get_with_retry(
            collector_service.ado_client.get_code_coverage, "build coverage", "project", "invalid"
        )


@pytest.mark.asyncio
async def test_init_sets_attributes(mock_ado_client: Mock) -> None:
    collector = RawDataCollector(mock_ado_client, window_days=30, minimal=True, activity_window_days=10)
    assert collector.window_days == 30
    assert collector.minimal is True
    assert collector.activity_window_days == 10
    assert isinstance(collector.start_date, str)
    assert isinstance(collector.activity_threshold_date, str)


@pytest.mark.asyncio
async def test_get_with_retry_success() -> None:
    async def succeed(x: int) -> int:
        return x * 2
    collector = RawDataCollector(Mock())
    result = await collector._get_with_retry(succeed, "test", 3)
    assert result == 6


@pytest.mark.asyncio
async def test_get_with_retry_retries_and_fails() -> None:
    calls = []
    async def fail(x: int) -> None:
        calls.append(x)
        raise Exception("fail")
    collector = RawDataCollector(Mock())
    with pytest.raises(TimeoutError):
        await collector._get_with_retry(fail, "test", 1)
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_get_repository_policies_success(mock_ado_client: Mock) -> None:
    # Set explicit return value for the mock to match expected statistics
    mock_ado_client.get_policy_configurations.return_value = [
        {
            "type": {"id": "policy1", "displayName": "Build Policy"},
            "isEnabled": True,
            "isBlocking": True,
            "settings": {"scope": [{"repositoryId": "r1"}]},
        }
    ]
    collector = RawDataCollector(mock_ado_client)
    result = await collector._get_repository_policies("proj", "r1")
    assert "repository_policies" in result
    assert "branch_policies" in result
    assert "statistics" in result
    assert result["statistics"]["total_policies"] == 1
    assert result["statistics"]["blocking_policies"] == 1
    assert result["statistics"]["enabled_policies"] == 1
    assert result["statistics"]["policy_types"] == ["Build Policy"]


@pytest.mark.asyncio
async def test_get_repository_policies_error(mock_ado_client: Mock) -> None:
    mock_ado_client.get_policy_configurations.side_effect = Exception("fail")
    collector = RawDataCollector(mock_ado_client)
    result = await collector._get_repository_policies("proj", "r1")
    assert result["repository_policies"] == []
    assert result["statistics"]["total_policies"] == 0
    assert "error" in result
