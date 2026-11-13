import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from devops_metrics import runner
from devops_metrics.api.models import CollectionConfig
from devops_metrics.config.settings import Settings


class FakeAdoClient:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class FakeRawCollector:
    def __init__(self, error=None):
        self.error = error

    async def collect_all_raw_data(self, project):
        if self.error:
            raise self.error
        return {"project": project, "repositories": []}


def make_settings(database_url=None):
    return Settings(
        azure_devops_pat="test-pat",
        azure_devops_org="test-org",
        database_url=database_url,
        exemptions_file=None,
    )


def make_config(output_path, **overrides):
    values = {
        "project": "test-project",
        "write_files": False,
        "write_db": False,
        "include_maturity": False,
        "include_segments": False,
        "output_path": str(output_path),
    }
    values.update(overrides)
    return CollectionConfig(**values)


def install_fake_collection(monkeypatch, collector):
    client = FakeAdoClient()
    monkeypatch.setattr(runner, "AzureDevOpsClient", lambda settings: client)
    monkeypatch.setattr(
        runner.MetricsServiceFactory,
        "_services",
        {"raw": lambda ado_client, **kwargs: collector},
    )
    return client


@pytest.mark.asyncio
async def test_collection_success_closes_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    client = install_fake_collection(monkeypatch, FakeRawCollector())

    result = await runner.execute_collection_run(
        make_config(tmp_path / "metrics"),
        make_settings(),
        run_id="test-run",
    )

    assert result["status"] == "succeeded"
    assert result["id"] == "test-run"
    assert client.closed is True


@pytest.mark.asyncio
async def test_org_override_is_used_by_the_actual_client(tmp_path, monkeypatch):
    install_fake_collection(monkeypatch, FakeRawCollector())
    selected_orgs = []

    def make_client(settings):
        selected_orgs.append(settings.azure_devops_org)
        return FakeAdoClient()

    monkeypatch.setattr(runner, "AzureDevOpsClient", make_client)
    result = await runner.execute_collection_run(
        make_config(tmp_path / "metrics", org="example-override"), make_settings(),
    )
    assert result["status"] == "succeeded"
    assert selected_orgs == ["example-override"]


@pytest.mark.asyncio
async def test_collection_failure_closes_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    collector = FakeRawCollector(RuntimeError("collection failed"))
    client = install_fake_collection(monkeypatch, collector)

    result = await runner.execute_collection_run(
        make_config(tmp_path / "metrics"),
        make_settings(),
        run_id="test-run",
    )

    assert result["status"] == "failed"
    assert result["error"] == "collection failed"
    assert client.closed is True


@pytest.mark.asyncio
async def test_segment_collection_exports_only_source_derived_groups(tmp_path, monkeypatch):
    """Removing private owner mappings must leave group-based reporting working."""
    from devops_metrics.backend.services.segment_maturity_assessment import (
        SegmentMaturityAssessmentService,
    )

    monkeypatch.setenv("DATABASE_URL", "")
    client = install_fake_collection(monkeypatch, FakeRawCollector())
    maturity = {
        "repositories": {
            "example-repo": {
                "name": "Example Repository",
                "project": {"id": "example-project", "name": "Example Project"},
                "overall": {"score": 0.8},
            }
        }
    }
    segment_service = SegmentMaturityAssessmentService(client)
    segment_service.get_group_entitlements = AsyncMock(
        return_value=[{"displayName": "Example Group", "projects": ["Example Project"]}]
    )
    services = dict(runner.MetricsServiceFactory._services)
    services.update({
        "raw_maturity": lambda *args, **kwargs: SimpleNamespace(
            assess_raw_data=lambda raw: maturity
        ),
        "segment_maturity": lambda *args, **kwargs: segment_service,
    })
    monkeypatch.setattr(runner.MetricsServiceFactory, "_services", services)

    result = await runner.execute_collection_run(
        make_config(
            tmp_path / "metrics", write_files=True,
            include_maturity=True, include_segments=True,
        ),
        make_settings(),
        run_id="example-run",
    )

    assert result["status"] == "succeeded"
    assert client.closed is True
    payload = json.loads((tmp_path / "metrics_segments.json").read_text())
    assert set(payload) == {"project_scores", "segment_scores", "raw_maturity"}
    assert set(payload["segment_scores"]) == {"Example Group"}
    assert payload["segment_scores"]["Example Group"]["maturity_score"] == 0.8
    assert (tmp_path / "metrics_segments.xlsx").exists()
    assert not list(tmp_path.glob("*exec_summary*"))


@pytest.mark.asyncio
async def test_strict_db_write_fails_early(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setattr(
        runner,
        "AzureDevOpsClient",
        lambda settings: pytest.fail("client must not be created"),
    )

    result = await runner.execute_collection_run(
        make_config(tmp_path / "metrics", write_db=True, db_strict=True),
        make_settings(),
        run_id="test-run",
    )

    assert result["status"] == "failed"
    assert result["error"] == "DATABASE_URL must be set for --write-db"


@pytest.mark.asyncio
async def test_backfill_is_rejected_without_connection(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    init_calls = []

    def fake_init_engine(database_url, allow_create_all):
        init_calls.append((database_url, allow_create_all))
        return None

    monkeypatch.setattr(runner, "init_engine_and_tables", fake_init_engine)
    monkeypatch.setattr(
        runner,
        "AzureDevOpsClient",
        lambda settings: pytest.fail("client must not be created"),
    )

    result = await runner.execute_collection_run(
        CollectionConfig(
            backfill_from=str(tmp_path / "historical"),
            write_files=False,
            include_maturity=False,
            include_segments=False,
            output_path=str(tmp_path / "metrics"),
        ),
        make_settings(database_url="postgresql://unused"),
        run_id="test-run",
    )

    assert init_calls == [("postgresql://unused", False)]
    assert result["status"] == "failed"
    assert result["error"] == "Backfill not implemented in runner (MVP+1)"
