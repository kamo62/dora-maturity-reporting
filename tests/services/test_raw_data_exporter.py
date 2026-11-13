import json
from pathlib import Path

import pandas as pd
import pytest

from devops_metrics.backend.services.raw_data_exporter import RawDataExporter


@pytest.fixture
def raw_data():
    return {
        "project": {"name": "TestProject", "id": "proj1"},
        "repositories": [{"project": {"name": "TestProject", "id": "proj1"}, "id": "repo1"}],
        "repository_data": {},
        "builds": [],
        "build_data": {}
    }


@pytest.fixture
def exporter():
    return RawDataExporter(None)


def test_safe_sheet_name_truncate_and_replace(exporter):
    name = "Long/Name\\With/Invalid\\Chars_1234567890abcdef"
    safe = exporter._safe_sheet_name(name)
    assert "/" not in safe and "\\" not in safe
    assert len(safe) <= 31


def test_flatten_if_needed(exporter):
    data = {"a": 1, "b": {"x": "y"}, "c": [1, 2, 3]}
    flat = exporter._flatten_if_needed(data)
    assert flat["a"] == 1
    assert isinstance(flat["b"], str)
    assert isinstance(flat["c"], str)
    assert '"x": "y"' in flat["b"]


def test_export_data_creates_files(tmp_path, raw_data, exporter):
    output_file = tmp_path / "raw_output.xlsx"
    result = exporter.export_data(raw_data, str(output_file))
    assert "excel" in result and "json" in result

    excel_path = Path(result["excel"])
    json_path = Path(result["json"])
    assert excel_path.exists()
    assert json_path.exists()

    # Verify JSON content matches input raw_data
    with open(json_path, "r") as f:
        loaded = json.load(f)
    assert loaded == raw_data

    # Verify Excel contains expected sheets
    sheets = pd.read_excel(excel_path, sheet_name=None)
    assert "Project" in sheets
    assert "Repositories" in sheets
    assert "All Builds" in sheets
