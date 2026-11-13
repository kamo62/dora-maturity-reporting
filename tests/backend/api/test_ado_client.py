"""Tests for the Azure DevOps API client"""

import pytest
import types
import base64
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timedelta
import aiohttp
import json
import yarl
import os

from devops_metrics.backend.api import ado_client


class FakeSecret:
    def get_secret_value(self):
        return "test_token"


@pytest.fixture
def mock_settings():
    return types.SimpleNamespace(
        azure_devops_pat=FakeSecret(),
        azure_devops_org="test_org"
    )


@pytest.fixture
def azure_client(mock_settings):
    return ado_client.AzureDevOpsClient(mock_settings)


def test_adorequesterror():
    """Test the custom ADORequestError exception"""
    err = ado_client.ADORequestError("test error")
    assert isinstance(err, Exception)
    assert str(err) == "test error"
    
    with pytest.raises(ado_client.ADORequestError) as exc_info:
        raise ado_client.ADORequestError("error message")
    assert "error message" in str(exc_info.value)


def test_initialize_headers():
    """Test the initialize_headers function"""
    fake_secret = FakeSecret()
    settings = types.SimpleNamespace(azure_devops_pat=fake_secret)
    
    headers = ado_client.initialize_headers(settings)
    
    assert headers["Content-Type"] == "application/json"
    expected_token = base64.b64encode(":test_token".encode()).decode()
    assert headers["Authorization"] == f"Basic {expected_token}"


def test_inherited_pipeline_tokens_are_not_used(monkeypatch):
    monkeypatch.setenv("SYSTEM_ACCESSTOKEN", "synthetic-inherited-token")
    monkeypatch.setenv("ADO_BEARER_TOKEN", "synthetic-bearer-token")
    headers = ado_client.initialize_headers(types.SimpleNamespace(azure_devops_pat=FakeSecret()))
    assert headers["Authorization"] == "Basic " + base64.b64encode(b":test_token").decode()

    with pytest.raises(ado_client.ADORequestError, match="explicitly"):
        ado_client.initialize_headers(types.SimpleNamespace(azure_devops_pat=types.SimpleNamespace(get_secret_value=lambda: "")))


def test_setup_http_session():
    """Test the setup_http_session function"""
    session = ado_client.setup_http_session()
    
    import requests
    assert isinstance(session, requests.Session)
    assert session.adapters["https://"].max_retries.total == 3


def test_make_request():
    """Test the make_request function"""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"value": [1, 2, 3]}
    
    mock_session = MagicMock()
    mock_session.get.return_value = mock_response
    
    result = ado_client.make_request("https://example.com/api", mock_session)
    
    assert result == {"value": [1, 2, 3]}
    mock_session.get.assert_called_once_with("https://example.com/api", allow_redirects=False)
    mock_response.raise_for_status.assert_called_once()


# Helper for async context manager mock
class AsyncContextManagerMock:
    def __init__(self, mock):
        self._mock = mock
    async def __aenter__(self):
        return self._mock
    async def __aexit__(self, exc_type, exc, tb):
        return None


def test_make_request_error():
    """Test make_request with an error response (sync version)"""
    import requests
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("Not found")
    mock_response.json.return_value = None
    mock_session = MagicMock()
    mock_session.get.return_value = mock_response
    with pytest.raises(ado_client.ADORequestError) as exc_info:
        ado_client.make_request("https://example.com/api", mock_session)
    assert "Error accessing https://example.com/api" in str(exc_info.value) or "Error making request to https://example.com/api" in str(exc_info.value)
    assert "Not found" in str(exc_info.value)


class TestAzureDevOpsClient:
    """Test suite for AzureDevOpsClient class"""
    
    def test_init(self, mock_settings):
        """Test AzureDevOpsClient initialization"""
        client = ado_client.AzureDevOpsClient(mock_settings)
        
        assert client.settings == mock_settings
        assert client.base_url == "https://dev.azure.com/test_org"
        assert hasattr(client, "session")
        assert "Authorization" in client.session.headers
        
        # Verify the Authorization header is set correctly
        expected_token = base64.b64encode(":test_token".encode()).decode()
        assert client.session.headers["Authorization"] == f"Basic {expected_token}"
    
    def test_get_repository_id(self, azure_client):
        """Test _get_repository_id method"""
        # Test with dictionary input
        repo_dict = {"id": "repo-123"}
        assert azure_client._get_repository_id(repo_dict) == "repo-123"
        
        # Test with string input
        assert azure_client._get_repository_id("repo-456") == "repo-456"
        
        # Test with numeric input
        assert azure_client._get_repository_id(789) == "789"
    
    @pytest.mark.asyncio
    async def test_async_context_manager(self, azure_client):
        """Test async context manager functionality"""
        async with azure_client as client:
            assert client == azure_client
            assert azure_client._aiohttp_session is not None
        
        # Session should be closed after context manager exits
        assert azure_client._aiohttp_session is None
    
    @pytest.mark.asyncio
    async def test_close(self, azure_client):
        """Test close method"""
        # Create a session first
        await azure_client._get_aiohttp_session()
        assert azure_client._aiohttp_session is not None
        
        # Close the session
        await azure_client.close()
        assert azure_client._aiohttp_session is None
    
    @pytest.mark.asyncio
    async def test_get(self, azure_client, monkeypatch):
        """Test the get method"""
        mock_response_data = {"value": [1, 2, 3]}
        mock_response_obj = MagicMock()
        mock_response_obj.json = AsyncMock(return_value=mock_response_data)
        mock_response_obj.status = 200
        mock_response_obj.raise_for_status = AsyncMock()
        mock_response_obj.headers = {}
        mock_session = MagicMock()
        mock_session.get.return_value = AsyncContextManagerMock(mock_response_obj)
        async def mock_get_session():
            return mock_session
        monkeypatch.setattr(azure_client, "_get_aiohttp_session", mock_get_session)
        result = await azure_client.get("https://dev.azure.com/test_org/_apis/test")
        assert result == mock_response_data
        mock_session.get.assert_called_once_with("https://dev.azure.com/test_org/_apis/test")
    
    @pytest.mark.asyncio
    @pytest.mark.parametrize("method_name, endpoint, params, expected_url", [
        ("get_projects", "/_apis/projects?api-version=7.0", {}, "https://dev.azure.com/test_org/_apis/projects?api-version=7.0"),
        ("get_policy_types", "/_apis/policy/types?api-version=7.0", {"project": "test-project"}, "https://dev.azure.com/test_org/test-project/_apis/policy/types?api-version=7.0"),
    ])
    async def test_simple_get_methods(self, azure_client, monkeypatch, method_name, endpoint, params, expected_url):
        """Test simple get methods that just call the base get method"""
        expected_result = {"value": ["item1", "item2"]}
        mock_response_obj = MagicMock()
        mock_response_obj.json = AsyncMock(return_value=expected_result)
        mock_response_obj.status = 200
        mock_response_obj.raise_for_status = AsyncMock()
        mock_response_obj.headers = {}
        mock_session = MagicMock()
        mock_session.get.return_value = AsyncContextManagerMock(mock_response_obj)
        async def mock_get_session():
            return mock_session
        monkeypatch.setattr(azure_client, "_get_aiohttp_session", mock_get_session)
        method = getattr(azure_client, method_name)
        result = await method(**params)
        assert result == expected_result["value"] or result == expected_result


@pytest.mark.asyncio
async def test_get_paginated_results(azure_client, monkeypatch):
    """Test the get_paginated_results method with pagination"""
    mock_response_data1 = {"value": ["item1", "item2"]}
    mock_response_data2 = {"value": ["item3", "item4"]}
    mock_response_obj1 = MagicMock()
    mock_response_obj1.json = AsyncMock(return_value=mock_response_data1)
    mock_response_obj1.status = 200
    mock_response_obj1.headers = {"continuationToken": "token123"}
    mock_response_obj1.raise_for_status = AsyncMock()
    mock_response_obj2 = MagicMock()
    mock_response_obj2.json = AsyncMock(return_value=mock_response_data2)
    mock_response_obj2.status = 200
    mock_response_obj2.headers = {}
    mock_response_obj2.raise_for_status = AsyncMock()
    mock_session = MagicMock()
    mock_session.get.side_effect = [AsyncContextManagerMock(mock_response_obj1), AsyncContextManagerMock(mock_response_obj2)]
    async def mock_get_session():
        return mock_session
    monkeypatch.setattr(azure_client, "_get_aiohttp_session", mock_get_session)
    url = "https://dev.azure.com/test_org/_apis/test"
    results = await azure_client.get_paginated_results(url)
    assert results == ["item1", "item2", "item3", "item4"]


@pytest.mark.asyncio
class DummyAiohttpResponse:
    def __init__(self, json_data=None, status=200, headers=None, raise_exc=None):
        self._json_data = json_data or {}
        self.status = status
        self.headers = headers or {}
        self._raise_exc = raise_exc
        self.closed = False
    async def __aenter__(self):
        if self._raise_exc:
            raise self._raise_exc
        return self
    async def __aexit__(self, exc_type, exc, tb):
        return None
    async def json(self):
        return self._json_data
    def raise_for_status(self):
        if self._raise_exc:
            raise self._raise_exc
        if self.status >= 400:
            import aiohttp
            raise aiohttp.ClientResponseError(None, (), status=self.status, message="error")

@pytest.mark.asyncio
async def test_get_project_success(azure_client, monkeypatch):
    mock_response = DummyAiohttpResponse(json_data={"id": "proj1", "name": "Test Project"})
    mock_session = MagicMock()
    mock_session.get.return_value = mock_response
    async def mock_get_session():
        return mock_session
    monkeypatch.setattr(azure_client, "_get_aiohttp_session", mock_get_session)
    result = await azure_client.get_project("proj1")
    assert result["id"] == "proj1"
    assert result["name"] == "Test Project"

@pytest.mark.asyncio
async def test_get_project_404(azure_client, monkeypatch):
    # Provide a mock request_info with real_url to avoid AttributeError
    class DummyRequestInfo:
        @property
        def real_url(self):
            return yarl.URL("https://dev.azure.com/test_org/_apis/projects/proj1")
    import aiohttp
    mock_response = DummyAiohttpResponse(status=404, raise_exc=aiohttp.ClientResponseError(DummyRequestInfo(), (), status=404, message="error"))
    mock_session = MagicMock()
    mock_session.get.return_value = mock_response
    async def mock_get_session():
        return mock_session
    monkeypatch.setattr(azure_client, "_get_aiohttp_session", mock_get_session)
    with pytest.raises(ado_client.ADORequestError):
        await azure_client.get_project("proj1")

@pytest.mark.asyncio
async def test_get_repositories_success(azure_client, monkeypatch):
    monkeypatch.setattr(azure_client, "get_paginated_results", AsyncMock(return_value=[{"id": "repo1"}]))
    async def mock_get_session():
        return MagicMock()
    monkeypatch.setattr(azure_client, "_get_aiohttp_session", mock_get_session)
    result = await azure_client.get_repositories("proj1")
    assert result == [{"id": "repo1"}]

@pytest.mark.asyncio
async def test_get_repositories_error(azure_client, monkeypatch):
    monkeypatch.setattr(azure_client, "get_paginated_results", AsyncMock(side_effect=Exception("fail")))
    async def mock_get_session():
        return MagicMock()
    monkeypatch.setattr(azure_client, "_get_aiohttp_session", mock_get_session)
    with pytest.raises(ado_client.ADORequestError):
        await azure_client.get_repositories("proj1")

@pytest.mark.asyncio
async def test_get_commits_success(azure_client, monkeypatch):
    monkeypatch.setattr(azure_client, "get_paginated_results", AsyncMock(return_value=[{"id": "c1"}]))
    async def mock_get_session():
        return MagicMock()
    monkeypatch.setattr(azure_client, "_get_aiohttp_session", mock_get_session)
    result = await azure_client.get_commits("repo1", "proj1")
    assert result == [{"id": "c1"}]

@pytest.mark.asyncio
async def test_get_commits_error(azure_client, monkeypatch):
    monkeypatch.setattr(azure_client, "get_paginated_results", AsyncMock(side_effect=Exception("fail")))
    async def mock_get_session():
        return MagicMock()
    monkeypatch.setattr(azure_client, "_get_aiohttp_session", mock_get_session)
    with pytest.raises(ado_client.ADORequestError):
        await azure_client.get_commits("repo1", "proj1")

@pytest.mark.asyncio
async def test_get_branches_success(azure_client, monkeypatch):
    monkeypatch.setattr(azure_client, "get_paginated_results", AsyncMock(return_value=[{"name": "main"}]))
    async def mock_get_session():
        return MagicMock()
    monkeypatch.setattr(azure_client, "_get_aiohttp_session", mock_get_session)
    result = await azure_client.get_branches("repo1", "proj1")
    assert result == [{"name": "main"}]

@pytest.mark.asyncio
async def test_get_branches_error(azure_client, monkeypatch):
    monkeypatch.setattr(azure_client, "get_paginated_results", AsyncMock(side_effect=Exception("fail")))
    async def mock_get_session():
        return MagicMock()
    monkeypatch.setattr(azure_client, "_get_aiohttp_session", mock_get_session)
    with pytest.raises(ado_client.ADORequestError):
        await azure_client.get_branches("repo1", "proj1")

@pytest.mark.asyncio
async def test_get_branch_policies_success(azure_client, monkeypatch):
    monkeypatch.setattr(azure_client, "get_paginated_results", AsyncMock(return_value=[{"id": "p1"}]))
    async def mock_get_session():
        return MagicMock()
    monkeypatch.setattr(azure_client, "_get_aiohttp_session", mock_get_session)
    result = await azure_client.get_branch_policies("proj1", "repo1")
    assert result == [{"id": "p1"}]

@pytest.mark.asyncio
async def test_get_branch_policies_error(azure_client, monkeypatch):
    monkeypatch.setattr(azure_client, "get_paginated_results", AsyncMock(side_effect=Exception("fail")))
    async def mock_get_session():
        return MagicMock()
    monkeypatch.setattr(azure_client, "_get_aiohttp_session", mock_get_session)
    with pytest.raises(ado_client.ADORequestError):
        await azure_client.get_branch_policies("proj1", "repo1")

@pytest.mark.asyncio
async def test_get_builds_success(azure_client, monkeypatch):
    build = {"id": 1}
    monkeypatch.setattr(azure_client, "_get_aiohttp_session", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(azure_client, "get_paginated_results", AsyncMock(return_value=[build]))
    async def dummy_get(*args, **kwargs):
        class Dummy:
            async def __aenter__(self): return Dummy()
            async def __aexit__(self, exc_type, exc, tb): return None
            async def json(self): return {"timeline": {}}
            def raise_for_status(self): pass
        return Dummy()
    monkeypatch.setattr(azure_client, "_get_aiohttp_session", AsyncMock(return_value=MagicMock(get=AsyncMock(side_effect=dummy_get))))
    result = await azure_client.get_builds("proj1")
    assert isinstance(result, list)
    assert result[0]["id"] == 1

@pytest.mark.asyncio
async def test_get_builds_error(azure_client, monkeypatch):
    monkeypatch.setattr(azure_client, "_get_aiohttp_session", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(azure_client, "get_paginated_results", AsyncMock(side_effect=Exception("fail")))
    with pytest.raises(ado_client.ADORequestError):
        await azure_client.get_builds("proj1")

@pytest.mark.asyncio
async def test_get_code_coverage_success(azure_client, monkeypatch):
    mock_response = DummyAiohttpResponse(json_data={"coverage": 100})
    mock_session = MagicMock()
    mock_session.get.return_value = mock_response
    async def mock_get_session():
        return mock_session
    monkeypatch.setattr(azure_client, "_get_aiohttp_session", mock_get_session)
    result = await azure_client.get_code_coverage("proj1", 1)
    assert result["coverage"] == 100

@pytest.mark.asyncio
async def test_get_code_coverage_404(azure_client, monkeypatch):
    mock_response = DummyAiohttpResponse(status=404)
    mock_session = MagicMock()
    mock_session.get.return_value = mock_response
    async def mock_get_session():
        return mock_session
    monkeypatch.setattr(azure_client, "_get_aiohttp_session", mock_get_session)
    result = await azure_client.get_code_coverage("proj1", 1)
    assert result == {}

@pytest.mark.asyncio
async def test_get_code_coverage_error(azure_client, monkeypatch):
    mock_response = DummyAiohttpResponse(raise_exc=Exception("fail"))
    mock_session = MagicMock()
    mock_session.get.return_value = mock_response
    async def mock_get_session():
        return mock_session
    monkeypatch.setattr(azure_client, "_get_aiohttp_session", mock_get_session)
    with pytest.raises(ado_client.ADORequestError):
        await azure_client.get_code_coverage("proj1", 1)

@pytest.mark.asyncio
async def test_get_test_results_success(azure_client, monkeypatch):
    monkeypatch.setattr(azure_client, "get_paginated_results", AsyncMock(return_value=[{"totalTests": 5, "passedTests": 4, "failedTests": 1, "notApplicableTests": 0, "incompleteTests": 0}]))
    async def mock_get_session():
        return MagicMock()
    monkeypatch.setattr(azure_client, "_get_aiohttp_session", mock_get_session)
    result = await azure_client.get_test_results("proj1", 1)
    assert result["results"][0]["total"] == 5
    assert result["results"][0]["passed"] == 4
    assert result["results"][0]["failed"] == 1

@pytest.mark.asyncio
async def test_get_test_results_empty(azure_client, monkeypatch):
    monkeypatch.setattr(azure_client, "get_paginated_results", AsyncMock(return_value=[]))
    async def mock_get_session():
        return MagicMock()
    monkeypatch.setattr(azure_client, "_get_aiohttp_session", mock_get_session)
    result = await azure_client.get_test_results("proj1", 1)
    assert result["results"][0]["total"] == 0

@pytest.mark.asyncio
async def test_get_test_results_error(azure_client, monkeypatch):
    monkeypatch.setattr(azure_client, "get_paginated_results", AsyncMock(side_effect=Exception("fail")))
    async def mock_get_session():
        return MagicMock()
    monkeypatch.setattr(azure_client, "_get_aiohttp_session", mock_get_session)
    with pytest.raises(ado_client.ADORequestError):
        await azure_client.get_test_results("proj1", 1)

@pytest.mark.asyncio
async def test_get_build_code_coverage_success(azure_client, monkeypatch):
    monkeypatch.setattr(azure_client, "get_code_coverage", AsyncMock(return_value={"coverage": 100}))
    result = await azure_client.get_build_code_coverage("proj1", 1)
    assert result["coverage"] == 100

@pytest.mark.asyncio
async def test_get_build_code_coverage_error(azure_client, monkeypatch):
    monkeypatch.setattr(azure_client, "get_code_coverage", AsyncMock(side_effect=Exception("fail")))
    with pytest.raises(ado_client.ADORequestError):
        await azure_client.get_build_code_coverage("proj1", 1)

@pytest.mark.asyncio
async def test_get_builds_with_coverage_success(azure_client, monkeypatch):
    monkeypatch.setattr(azure_client, "_get_aiohttp_session", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(azure_client, "get_build_code_coverage", AsyncMock(return_value={"coverage": 100}))
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value={"value": [{"id": 1}]})
    mock_response.raise_for_status = AsyncMock()
    mock_session = MagicMock()
    mock_session.get.return_value = AsyncContextManagerMock(mock_response)
    async def mock_get_session():
        return mock_session
    monkeypatch.setattr(azure_client, "_get_aiohttp_session", mock_get_session)
    result = await azure_client.get_builds_with_coverage("proj1")
    assert isinstance(result, list)
    assert result[0]["id"] == 1

@pytest.mark.asyncio
async def test_get_builds_with_coverage_error(azure_client, monkeypatch):
    monkeypatch.setattr(azure_client, "_get_aiohttp_session", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(azure_client, "get_build_code_coverage", AsyncMock(side_effect=Exception("fail")))
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value={"value": [{"id": 1}]})
    mock_response.raise_for_status = AsyncMock()
    mock_session = MagicMock()
    mock_session.get.return_value = AsyncContextManagerMock(mock_response)
    async def mock_get_session():
        return mock_session
    monkeypatch.setattr(azure_client, "_get_aiohttp_session", mock_get_session)
    result = await azure_client.get_builds_with_coverage("proj1")
    assert isinstance(result, list)

@pytest.mark.asyncio
async def test_get_policy_configurations_success(azure_client, monkeypatch):
    # Patch get_paginated_results to return a successful value
    monkeypatch.setattr(azure_client, "get_paginated_results", AsyncMock(return_value=[{"id": "p1"}]))
    async def mock_get_session():
        return MagicMock()
    monkeypatch.setattr(azure_client, "_get_aiohttp_session", mock_get_session)
    result = await azure_client.get_policy_configurations("proj1")
    assert result[0]["id"] == "p1"

@pytest.mark.asyncio
async def test_get_policy_configurations_error(azure_client, monkeypatch):
    monkeypatch.setattr(azure_client, "get", AsyncMock(side_effect=Exception("fail")))
    with pytest.raises(ado_client.ADORequestError):
        await azure_client.get_policy_configurations("proj1")

@pytest.mark.asyncio
async def test_get_policy_types_success(azure_client, monkeypatch):
    # Patch get_paginated_results to return a successful value
    monkeypatch.setattr(azure_client, "get_paginated_results", AsyncMock(return_value=[{"id": "t1"}]))
    async def mock_get_session():
        return MagicMock()
    monkeypatch.setattr(azure_client, "_get_aiohttp_session", mock_get_session)
    result = await azure_client.get_policy_types("proj1")
    assert result[0]["id"] == "t1"

@pytest.mark.asyncio
async def test_get_policy_types_error(azure_client, monkeypatch):
    monkeypatch.setattr(azure_client, "get", AsyncMock(side_effect=Exception("fail")))
    with pytest.raises(ado_client.ADORequestError):
        await azure_client.get_policy_types("proj1")
@pytest.fixture(autouse=True)
def clear_bearer_env(monkeypatch):
    """Ensure PAT path is used by removing bearer env vars during tests."""
    monkeypatch.delenv("ADO_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("SYSTEM_ACCESSTOKEN", raising=False)
