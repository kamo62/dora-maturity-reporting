import pytest
from devops_metrics.types import RawProjectData


def test_raw_project_data_instantiation():
    data = RawProjectData(
        project={"id": 1, "name": "Test"},
        repositories=[{"id": "r1"}],
        repository_data={"r1": {"metric": 42}},
        builds=[{"id": 100}],
        build_data={"100": {"status": "succeeded"}},
    )
    assert isinstance(data, dict)
    assert "project" in data
    assert "repositories" in data
    assert "repository_data" in data
    assert "builds" in data
    assert "build_data" in data
    assert data["project"]["id"] == 1
    assert data["repositories"][0]["id"] == "r1"
    assert data["repository_data"]["r1"]["metric"] == 42
    assert data["builds"][0]["id"] == 100
    assert data["build_data"]["100"]["status"] == "succeeded"


def test_raw_project_data_missing_fields():
    # TypedDict allows missing fields at runtime unless total=False, but type checkers will complain
    data = RawProjectData(
        project={},
        repositories=[],
        repository_data={},
        builds=[],
        build_data={},
    )
    assert isinstance(data, dict)
    # All keys present, but empty
    for key in ["project", "repositories", "repository_data", "builds", "build_data"]:
        assert key in data


def test_raw_project_data_wrong_types():
    # At runtime, TypedDict does not enforce types, but type checkers will
    data = RawProjectData(
        project="not a dict",  # type: ignore
        repositories="not a list",  # type: ignore
        repository_data=42,  # type: ignore
        builds=None,  # type: ignore
        build_data=None,  # type: ignore
    )
    assert isinstance(data, dict)
    assert data["project"] == "not a dict"
    assert data["repositories"] == "not a list"
    assert data["repository_data"] == 42
    assert data["builds"] is None
    assert data["build_data"] is None


def test_raw_project_data_nested_structure():
    data = RawProjectData(
        project={"id": 1},
        repositories=[{"id": "r1", "nested": {"foo": "bar"}}],
        repository_data={"r1": {"nested": {"foo": "bar"}}},
        builds=[{"id": 100, "steps": [{"name": "build"}]}],
        build_data={"100": {"steps": [{"name": "build"}]}},
    )
    assert data["repositories"][0]["nested"]["foo"] == "bar"
    assert data["repository_data"]["r1"]["nested"]["foo"] == "bar"
    assert data["builds"][0]["steps"][0]["name"] == "build"
    assert data["build_data"]["100"]["steps"][0]["name"] == "build"
