"""Source isolation checks use names only; no excluded resource is contacted."""

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from devops_metrics.api.models import CollectionConfig
from devops_metrics.backend.api.ado_client import AzureDevOpsClient, source_request_trace
from devops_metrics.backend.api.errors import ADORequestError
from devops_metrics.config.access import checked_path, validate_database_url, validate_source_url
from devops_metrics.config.settings import Settings


@pytest.mark.parametrize("name", ["OML", "OMEngineering", "old-mutual", "oml-sandbox", "OM-Engineering"])
def test_excluded_names_fail_before_client_or_path_access(name, tmp_path, monkeypatch):
    session_factory = Mock()
    monkeypatch.setattr("devops_metrics.backend.api.ado_client.setup_http_session", session_factory)
    with pytest.raises(ValueError, match="Excluded"):
        AzureDevOpsClient(SimpleNamespace(azure_devops_org=name))
    session_factory.assert_not_called()
    with pytest.raises(ValidationError, match="Excluded"):
        Settings(_env_file=None, azure_devops_pat="synthetic-pat", azure_devops_org=name)
    with pytest.raises(ValidationError, match="Excluded"):
        CollectionConfig(org=name, project="example")
    with pytest.raises(ValueError, match="Excluded"):
        checked_path(tmp_path / name / "report.json")
    with pytest.raises(ValueError, match="Excluded"):
        validate_database_url(f"postgresql+psycopg://user:synthetic@{name}.example.com/metrics")


@pytest.mark.asyncio
async def test_requests_are_scoped_and_redirects_rejected():
    base = "https://dev.azure.com/example-org"
    validate_source_url(base + "/project/_apis/build/builds", base)
    validate_source_url("https://vsaex.dev.azure.com/example-org/_apis/GroupEntitlements", base)
    validate_source_url("https://dev.azure.com/local-org/_apis/projects", "https://dev.azure.com/local-org")
    trace = source_request_trace(base)
    for url in (
        "https://dev.azure.com/OMEngineering/_apis/projects",
        "https://dev.azure.com/other-org/_apis/projects",
        base + "/../other-org/_apis/projects",
        base + "/%252e%252e/other-org/_apis/projects",
        "http://dev.azure.com/example-org/_apis/projects",
        "https://example.com/_apis/projects",
        base + "/OML/_apis/git/repositories",
    ):
        with pytest.raises(ValueError):
            await trace.on_request_start[0](None, None, SimpleNamespace(url=url))
    with pytest.raises(ADORequestError, match="Redirects are not permitted"):
        await trace.on_request_redirect[0](None, None, SimpleNamespace())


def test_parent_environment_is_not_discovered(tmp_path):
    (tmp_path / ".env").write_text("DORA_PARENT_SENTINEL=must-not-load\n")
    child = tmp_path / "child"
    child.mkdir()
    env = {key: value for key, value in os.environ.items() if key not in {"DORA_PARENT_SENTINEL", "PYTHON_DOTENV_DISABLED"}}
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(
        [sys.executable, "-c", "import os; import devops_metrics.config.settings; assert 'DORA_PARENT_SENTINEL' not in os.environ"],
        cwd=child, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_symlink_cannot_hide_an_excluded_path(tmp_path):
    link = tmp_path / "neutral-name"
    link.symlink_to(tmp_path / "OMEngineering", target_is_directory=True)
    with pytest.raises(ValueError, match="Excluded"):
        checked_path(link / "report.json")


@pytest.mark.asyncio
async def test_subclient_guard_runs_before_opening_a_connection(monkeypatch):
    import aiohttp

    connection = Mock(side_effect=AssertionError("No network connection is permitted"))
    monkeypatch.setattr(aiohttp.TCPConnector, "_create_connection", connection)
    client = AzureDevOpsClient(Settings(
        _env_file=None, azure_devops_pat="synthetic-pat", azure_devops_org="example-org",
    ))
    try:
        with pytest.raises(ADORequestError, match="Excluded"):
            await client.get_code_coverage("OMEngineering", 1)
        connection.assert_not_called()
    finally:
        await client.close()
