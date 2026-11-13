"""Tests for the types module"""

import pytest
from typing import Dict, Any
from devops_metrics.types import RawProjectData


def test_raw_project_data_type():
    """Test the RawProjectData TypedDict with valid data"""
    # Create a valid instance of RawProjectData
    data = RawProjectData(
        project={"id": "123", "name": "Test Project"},
        repositories=[{"id": "456", "name": "Test Repo"}],
        repository_data={"456": {"commits": [{"id": "789"}]}},
        builds=[{"id": "101", "name": "Test Build"}],
        build_data={"101": {"result": "succeeded"}}
    )
    
    # Verify the structure
    assert "project" in data
    assert "repositories" in data
    assert "repository_data" in data
    assert "builds" in data
    assert "build_data" in data
    
    # Verify data content
    assert data["project"]["name"] == "Test Project"
    assert data["repositories"][0]["name"] == "Test Repo"
    assert data["repository_data"]["456"]["commits"][0]["id"] == "789"
    assert data["builds"][0]["name"] == "Test Build"
    assert data["build_data"]["101"]["result"] == "succeeded"


def test_raw_project_data_with_empty_fields():
    """Test RawProjectData with empty collections"""
    # Create an instance with empty lists and dicts
    data = RawProjectData(
        project={},
        repositories=[],
        repository_data={},
        builds=[],
        build_data={}
    )
    
    # Verify the structure
    assert isinstance(data["project"], dict) and not data["project"]
    assert isinstance(data["repositories"], list) and not data["repositories"]
    assert isinstance(data["repository_data"], dict) and not data["repository_data"]
    assert isinstance(data["builds"], list) and not data["builds"]
    assert isinstance(data["build_data"], dict) and not data["build_data"]


def test_raw_project_data_with_nested_structures():
    """Test RawProjectData with nested data structures"""
    # Create an instance with nested data
    data = RawProjectData(
        project={"id": "123", "name": "Nested Test", "metadata": {"key": "value"}},
        repositories=[
            {"id": "456", "name": "Repo 1"},
            {"id": "789", "name": "Repo 2"}
        ],
        repository_data={
            "456": {"commits": [{"id": "c1"}, {"id": "c2"}]},
            "789": {"commits": [{"id": "c3"}]}
        },
        builds=[
            {"id": "b1", "name": "Build 1", "status": "completed"},
            {"id": "b2", "name": "Build 2", "status": "inProgress"}
        ],
        build_data={
            "b1": {"result": "succeeded", "duration": 120},
            "b2": {"result": "unknown", "duration": 60}
        }
    )
    
    # Verify the nested structures
    assert data["project"]["metadata"]["key"] == "value"
    assert len(data["repositories"]) == 2
    assert len(data["repository_data"]["456"]["commits"]) == 2
    assert data["builds"][1]["status"] == "inProgress"
    assert data["build_data"]["b1"]["duration"] == 120
