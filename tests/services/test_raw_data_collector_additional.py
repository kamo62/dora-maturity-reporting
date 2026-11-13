import pytest
import asyncio
from devops_metrics.backend.services.raw_data_collector import RawDataCollector

@pytest.mark.asyncio
async def test_collect_metrics_delegates_to_collect_all_projects_raw_data():
    collector = RawDataCollector(None)
    async def fake_collect_all():
        return {"foo": "bar"}
    collector.collect_all_projects_raw_data = fake_collect_all
    result = await collector.collect_metrics()
    assert result == {"foo": "bar"}

@pytest.mark.asyncio
async def test_get_cached_metrics_returns_none():
    collector = RawDataCollector(None)
    result = await collector.get_cached_metrics()
    assert result is None

@pytest.mark.asyncio
async def test_collect_all_projects_raw_data_no_projects(monkeypatch):
    collector = RawDataCollector(None)
    collector.ado_client = type("DummyClient", (), {"get_projects": lambda *args: None})()
    async def fake_get_with_retry(func, op, *args):
        if op == "projects":
            return []
        return {}
    monkeypatch.setattr(collector, "_get_with_retry", fake_get_with_retry)
    with pytest.raises(ValueError) as exc:
        await collector.collect_all_projects_raw_data()
    assert "No projects found" in str(exc.value)

@pytest.mark.asyncio
async def test_collect_all_projects_raw_data_minimal_truncates(monkeypatch):
    # Prepare more than 10 projects
    projects = [{"name": f"proj{i}", "id": f"id{i}"} for i in range(15)]
    collector = RawDataCollector(None, minimal=True)
    collector.ado_client = type("DummyClient", (), {"get_projects": lambda *args: None})()
    # Stub fetching projects
    async def fake_get_with_retry(func, op, *args):
        if op == "projects":
            return projects
        return ()
    # Stub collect_all_raw_data to return minimal structure
    async def fake_collect(project):
        return {
            "project": {"name": project["name"], "id": project["id"]},
            "repositories": [],
            "repository_data": {},
            "builds": [],
            "build_data": {}
        }
    monkeypatch.setattr(collector, "_get_with_retry", fake_get_with_retry)
    monkeypatch.setattr(collector, "collect_all_raw_data", fake_collect)
    data = await collector.collect_all_projects_raw_data()
    # Should limit to 10 projects
    assert len(data["projects"]) == 10
    # No repositories or builds in stub
    assert data["repositories"] == []
    assert data["builds"] == []

@pytest.mark.asyncio
async def test_collect_all_projects_raw_data_skips_errors(monkeypatch):
    projects = [{"name": "good", "id": "g1"}, {"name": "bad", "id": "b1"}, {"name": "good2", "id": "g2"}]
    collector = RawDataCollector(None)
    collector.ado_client = type("DummyClient", (), {"get_projects": lambda *args: None})()
    async def fake_get_with_retry(func, op, *args):
        if op == "projects":
            return projects
        return ()
    async def fake_collect(project):
        if project["name"] == "bad":
            raise Exception("failed")
        return {
            "project": {"name": project["name"], "id": project["id"]},
            "repositories": [],
            "repository_data": {},
            "builds": [],
            "build_data": {}
        }
    monkeypatch.setattr(collector, "_get_with_retry", fake_get_with_retry)
    monkeypatch.setattr(collector, "collect_all_raw_data", fake_collect)
    data = await collector.collect_all_projects_raw_data()
    # All projects listed, but error on "bad" should be skipped without raising
    assert data["projects"] == projects[:]
    # Only good repositories processed (none in stub)
    assert data["repositories"] == []
