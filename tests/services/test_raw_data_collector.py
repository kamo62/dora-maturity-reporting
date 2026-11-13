import pytest
import asyncio
from datetime import datetime, timezone
from devops_metrics.backend.services.raw_data_collector import RawDataCollector

@pytest.mark.asyncio
async def test_get_with_retry_success():
    calls = []
    async def dummy_func(*args):
        calls.append(None)
        if len(calls) < 2:
            raise Exception("fail")
        return "ok"

    collector = RawDataCollector(None)
    # Use actual _get_with_retry implementation
    result = await collector._get_with_retry(dummy_func, "op")
    assert result == "ok"
    assert len(calls) == 2

@pytest.mark.asyncio
async def test_get_with_retry_timeout():
    async def always_fail(*args):
        raise Exception("error")
    collector = RawDataCollector(None)
    with pytest.raises(TimeoutError):
        await collector._get_with_retry(always_fail, "op")

@pytest.mark.asyncio
async def test_get_repository_policies_no_policies(monkeypatch):
    async def stub_get_with_retry(func, op, *args):
        return []
    collector = RawDataCollector(None)
    collector.ado_client = type("DummyClient", (), {"get_policy_configurations": None, "get_policy_types": None})()
    monkeypatch.setattr(collector, "_get_with_retry", stub_get_with_retry)
    result = await collector._get_repository_policies("proj", "repo1")
    assert result["repository_policies"] == []
    assert result["branch_policies"] == []
    stats = result["statistics"]
    assert stats["total_policies"] == 0
    assert stats["blocking_policies"] == 0
    assert stats["enabled_policies"] == 0

@pytest.mark.asyncio
async def test_get_repository_policies_with_repo_and_branch(monkeypatch):
    policy_configs = [
        {
            "settings": {"scope": [{"repositoryId": "repo1"}]},
            "type": {"id": "type1"},
            "isBlocking": True,
            "isEnabled": False,
            "id": "p1"
        },
        {
            "settings": {"scope": [{"repositoryId": "repo1", "refName": "refs/heads/main"}]},
            "type": {"id": "type2"},
            "isBlocking": False,
            "isEnabled": True,
            "id": "p2"
        }
    ]
    policy_types = [
        {"id": "type1", "displayName": "TypeOne"},
        {"id": "type2", "displayName": "TypeTwo"}
    ]

    async def fake_get_with_retry(func, op, *args):
        if op == "policy configurations":
            return policy_configs
        if op == "policy types":
            return policy_types
        return []

    collector = RawDataCollector(None)
    collector.ado_client = type("DummyClient", (), {"get_policy_configurations": None, "get_policy_types": None})()
    monkeypatch.setattr(collector, "_get_with_retry", fake_get_with_retry)
    result = await collector._get_repository_policies("proj", "repo1")

    # One repository-wide and one branch-specific policy
    repos = result["repository_policies"]
    branches = result["branch_policies"]
    assert len(repos) == 1
    assert repos[0]["type"]["displayName"] == "TypeOne"
    assert len(branches) == 1
    assert branches[0]["branch_name"] == "main"
    assert branches[0]["policies"][0]["type"]["displayName"] == "TypeTwo"

    stats = result["statistics"]
    assert stats["total_policies"] == 2
    assert stats["blocking_policies"] == 1
    assert stats["enabled_policies"] == 1

@pytest.mark.asyncio
async def test_get_repository_policies_with_project_level(monkeypatch):
    """Test that project-level policies (without repositoryId) are included"""
    policy_configs = [
        {
            # Project-level policy - no repositoryId in scope
            "settings": {"scope": [{"refName": "refs/heads/main"}]},
            "type": {"id": "type1"},
            "isBlocking": True,
            "isEnabled": True,
            "id": "p1"
        },
        {
            # Repository-specific policy
            "settings": {"scope": [{"repositoryId": "repo1", "refName": "refs/heads/main"}]},
            "type": {"id": "type2"},
            "isBlocking": False,
            "isEnabled": True,
            "id": "p2"
        },
        {
            # Different repository - should be excluded
            "settings": {"scope": [{"repositoryId": "repo2"}]},
            "type": {"id": "type3"},
            "isBlocking": False,
            "isEnabled": False,
            "id": "p3"
        }
    ]
    policy_types = [
        {"id": "type1", "displayName": "Minimum Reviewers"},
        {"id": "type2", "displayName": "Build Validation"},
        {"id": "type3", "displayName": "Work Item Linking"}
    ]

    async def fake_get_with_retry(func, op, *args):
        if op == "policy configurations":
            return policy_configs
        if op == "policy types":
            return policy_types
        return []

    collector = RawDataCollector(None)
    collector.ado_client = type("DummyClient", (), {"get_policy_configurations": None, "get_policy_types": None})()
    monkeypatch.setattr(collector, "_get_with_retry", fake_get_with_retry)
    result = await collector._get_repository_policies("proj", "repo1")

    # Should include project-level and repo1-specific policies, but not repo2
    branches = result["branch_policies"]
    assert len(branches) == 2  # Two separate branch entries (project-level and repo-level)
    
    # Find the project-level and repository-level branch entries
    project_level_branch = next(b for b in branches if b.get("is_project_level", False))
    repo_level_branch = next(b for b in branches if not b.get("is_project_level", False))
    
    assert project_level_branch["branch_name"] == "main"
    assert len(project_level_branch["policies"]) == 1
    assert project_level_branch["policies"][0]["type"]["displayName"] == "Minimum Reviewers"
    
    assert repo_level_branch["branch_name"] == "main"  
    assert len(repo_level_branch["policies"]) == 1
    assert repo_level_branch["policies"][0]["type"]["displayName"] == "Build Validation"
    
    # Check statistics
    stats = result["statistics"]
    assert stats["total_policies"] == 2
    assert stats["project_level_policies"] == 1
    assert stats["repository_level_policies"] == 1
    assert stats["blocking_policies"] == 1
    assert stats["enabled_policies"] == 2

@pytest.mark.asyncio
async def test_get_repository_policies_with_null_refname(monkeypatch):
    """Test the specific bug we fixed - policies with refName: null should be categorized as repository policies"""
    policy_configs = [
        {
            # Project-level policy with refName: null (the bug we fixed)
            "settings": {"scope": [{"repositoryId": None, "refName": None, "matchKind": "DefaultBranch"}]},
            "type": {"id": "type1"},
            "isBlocking": True,
            "isEnabled": True,
            "id": "p1"
        },
        {
            # Project-level policy with actual branch name
            "settings": {"scope": [{"repositoryId": None, "refName": "refs/heads/main", "matchKind": "Exact"}]},
            "type": {"id": "type2"},
            "isBlocking": True,
            "isEnabled": True,
            "id": "p2"
        }
    ]
    policy_types = [
        {"id": "type1", "displayName": "Minimum Reviewers"},
        {"id": "type2", "displayName": "Build Validation"}
    ]

    async def fake_get_with_retry(func, op, *args):
        if op == "policy configurations":
            return policy_configs
        if op == "policy types":
            return policy_types
        return []

    collector = RawDataCollector(None)
    collector.ado_client = type("DummyClient", (), {"get_policy_configurations": None, "get_policy_types": None})()
    monkeypatch.setattr(collector, "_get_with_retry", fake_get_with_retry)
    result = await collector._get_repository_policies("proj", "repo1")

    # The refName: null policy should be categorized as repository policy, not branch policy
    repository_policies = result["repository_policies"]
    branch_policies = result["branch_policies"]
    
    assert len(repository_policies) == 1  # The refName: null policy
    assert repository_policies[0]["type"]["displayName"] == "Minimum Reviewers"
    
    assert len(branch_policies) == 1  # The refs/heads/main policy
    assert branch_policies[0]["branch_name"] == "main"
    assert branch_policies[0]["policies"][0]["type"]["displayName"] == "Build Validation"
    
    # Check statistics
    stats = result["statistics"]
    assert stats["total_policies"] == 2
    assert stats["project_level_policies"] == 2  # Both are project-level
    assert stats["repository_level_policies"] == 0  # None are repo-specific

@pytest.mark.asyncio
async def test_missing_repository_name_field(monkeypatch):
    """Test handling of repositories without 'name' field"""
    from unittest.mock import Mock, AsyncMock
    
    # Create a repository without 'name' field (one of the bugs we identified)
    repositories = [
        {
            "id": "repo1", 
            # Missing "name" field - this would cause KeyError in production
            "lastUpdateTime": "2024-01-01T00:00:00Z"
        }
    ]
    
    # Create a mock ADO client with all required methods
    mock_ado_client = Mock()
    mock_ado_client.get_project = AsyncMock(return_value={"id": "proj1", "name": "Test Project"})
    mock_ado_client.get_repositories = AsyncMock(return_value=repositories)
    mock_ado_client.get_commits = AsyncMock(return_value=[])
    mock_ado_client.get_branches = AsyncMock(return_value=[])
    mock_ado_client.get_pull_requests = AsyncMock(return_value=[])
    mock_ado_client.get_policy_configurations = AsyncMock(return_value=[])
    mock_ado_client.get_policy_types = AsyncMock(return_value=[])
    mock_ado_client.get_builds = AsyncMock(return_value=[])
    mock_ado_client.get_code_coverage = AsyncMock(return_value={})
    mock_ado_client.get_test_results = AsyncMock(return_value={})
    mock_ado_client.get_security_scan_results = AsyncMock(return_value=[])
    
    collector = RawDataCollector(mock_ado_client)
    
    # This should handle missing 'name' gracefully without crashing
    # The bug is in lines 234 and 285 where repo["name"] is accessed directly
    try:
        result = await collector.collect_all_raw_data("test_project")
        # If we get here without KeyError, the code is already fixed or handling it
        assert False, "Expected KeyError due to missing 'name' field, but none occurred"
    except KeyError as e:
        # This confirms the bug is present
        assert "name" in str(e), f"Expected KeyError for 'name' field: {e}"

@pytest.mark.asyncio  
async def test_missing_project_id_field(monkeypatch):
    """Test handling of project data without 'id' field"""
    from unittest.mock import Mock, AsyncMock
    
    # Create a repository to trigger the unsafe nested access in lines 334-335
    # Use a recent date so it passes the activity filter
    from datetime import datetime, timedelta, timezone
    recent_date = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    
    repositories = [
        {
            "id": "repo1", 
            "name": "test-repo",
            "lastUpdateTime": recent_date
        }
    ]
    
    # Project data without 'id' field (the bug we found)
    project_data = {
        # Missing "id" field - would cause KeyError in lines 334-335
        "name": "Test Project"
    }
    
    # Mock ADO client
    mock_ado_client = Mock()
    mock_ado_client.get_project = AsyncMock(return_value=project_data)
    mock_ado_client.get_repositories = AsyncMock(return_value=repositories)
    mock_ado_client.get_commits = AsyncMock(return_value=[])
    mock_ado_client.get_branches = AsyncMock(return_value=[])
    mock_ado_client.get_pull_requests = AsyncMock(return_value=[])
    mock_ado_client.get_policy_configurations = AsyncMock(return_value=[])
    mock_ado_client.get_policy_types = AsyncMock(return_value=[])
    mock_ado_client.get_builds = AsyncMock(return_value=[])
    mock_ado_client.get_code_coverage = AsyncMock(return_value={})
    mock_ado_client.get_test_results = AsyncMock(return_value={})
    mock_ado_client.get_security_scan_results = AsyncMock(return_value=[])
    
    collector = RawDataCollector(mock_ado_client)
    
    # This should fail due to unsafe nested access in lines 334-335
    # The code tries to access raw_data["project"]["id"] without checking if "id" exists
    # This test confirms that the code handles missing project 'id' fields gracefully
    # The create_project_info function uses .get() with defaults and validates the result
    result = await collector.collect_all_raw_data("test_project")
    
    # Verify that the collection completed successfully despite missing 'id' field
    assert "project" in result
    assert "repositories" in result
    assert "repository_data" in result
    assert "builds" in result
    assert "build_data" in result
    
    # The transformed project should have an empty string for the missing id
    assert result["project"]["id"] == ""  # Default value from create_project_info
