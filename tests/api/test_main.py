import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from devops_metrics.api import main as api
from devops_metrics.config.settings import Settings


@pytest.fixture(autouse=True)
def no_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")


def make_settings(**overrides):
    values = {
        "azure_devops_pat": "test-pat",
        "azure_devops_org": "test-org",
        "database_url": None,
        "api_key": None,
        "run_env": "local",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.asyncio
async def test_health_counts_cached_runs_without_database(monkeypatch):
    monkeypatch.setattr(
        api,
        "RUNS_CACHE",
        {
            "running": {"status": "running"},
            "queued": {"status": "queued"},
            "done": {"status": "succeeded"},
        },
    )
    monkeypatch.setattr(api, "TASKS_CACHE", {"task": object()})

    response = await api.healthz(make_settings())

    assert response.status_code == 200
    assert json.loads(response.body) == {
        "status": "ok",
        "database": "not_configured",
        "active_runs": 1,
        "queued_runs": 1,
        "active_tasks": 1,
    }


@pytest.mark.asyncio
async def test_cached_runs_filter_by_status_and_sort_newest_first(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        api,
        "RUNS_CACHE",
        {
            "old": {
                "id": "old",
                "status": "succeeded",
                "started_at": now - timedelta(hours=3),
                "finished_at": now - timedelta(hours=2),
            },
            "new": {
                "id": "new",
                "status": "succeeded",
                "started_at": now - timedelta(minutes=10),
                "finished_at": now - timedelta(minutes=5),
            },
            "failed": {
                "id": "failed",
                "status": "failed",
                "started_at": now,
                "finished_at": now,
            },
        },
    )

    runs = await api.list_runs(
        limit=10,
        status="SUCCEEDED",
        settings=make_settings(),
    )

    assert [run["id"] for run in runs] == ["new", "old"]


@pytest.mark.asyncio
async def test_unknown_run_returns_404():
    with pytest.raises(HTTPException) as exc_info:
        await api.get_run_status(uuid4(), make_settings())

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_artifacts_outside_exports_return_403(tmp_path, monkeypatch):
    exports = (tmp_path / "exports").resolve()
    exports.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    run_id = uuid4()
    monkeypatch.setattr(api, "EXPORTS_DIR", exports)
    monkeypatch.setattr(
        api,
        "RUNS_CACHE",
        {str(run_id): {"artifacts_dir": str(outside)}},
    )

    with pytest.raises(HTTPException) as exc_info:
        await api.list_artifacts(run_id, make_settings())

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_missing_artifact_directory_returns_404(tmp_path, monkeypatch):
    exports = (tmp_path / "exports").resolve()
    exports.mkdir()
    run_id = uuid4()
    missing_dir = exports / "missing-run"
    monkeypatch.setattr(api, "EXPORTS_DIR", exports)
    monkeypatch.setattr(
        api,
        "RUNS_CACHE",
        {str(run_id): {"artifacts_dir": str(missing_dir)}},
    )

    with pytest.raises(HTTPException) as exc_info:
        await api.list_artifacts(run_id, make_settings())

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_artifact_listing_is_scoped_to_exports(tmp_path, monkeypatch):
    exports = (tmp_path / "exports").resolve()
    run_dir = exports / "run-1"
    nested_dir = run_dir / "reports"
    nested_dir.mkdir(parents=True)
    (run_dir / "metrics.json").write_text("{}", encoding="utf-8")
    (nested_dir / "segments.json").write_text("[]", encoding="utf-8")
    run_id = uuid4()
    monkeypatch.setattr(api, "EXPORTS_DIR", exports)
    monkeypatch.setattr(
        api,
        "RUNS_CACHE",
        {str(run_id): {"artifacts_dir": str(run_dir)}},
    )

    response = await api.list_artifacts(run_id, make_settings())

    assert response["id"] == str(run_id)
    assert sorted(item["name"] for item in response["files"]) == [
        "run-1/metrics.json",
        "run-1/reports/segments.json",
    ]


def test_non_local_api_key_guard_requires_and_checks_key():
    settings = make_settings(run_env="prod", api_key=SecretStr("expected-key"))

    assert api.require_api_key_non_local("expected-key", settings) is True
    for supplied_key in (None, "incorrect-key"):
        with pytest.raises(HTTPException) as exc_info:
            api.require_api_key_non_local(supplied_key, settings)
        assert exc_info.value.status_code == 403

    with pytest.raises(HTTPException) as exc_info:
        api.require_api_key_non_local(
            "expected-key",
            make_settings(run_env="prod"),
        )
    assert exc_info.value.status_code == 403
