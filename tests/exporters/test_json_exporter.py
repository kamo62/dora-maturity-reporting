import json
from pathlib import Path
import pytest

from devops_metrics.exporters.json_exporter import JsonExporter
from devops_metrics.types.export_types import ExportOptions


def test_json_exporter_success(tmp_path):
    # Prepare sample data and path
    data = {"key1": "value1", "key2": 2}
    output_path = tmp_path / "sample.json"
    options: ExportOptions = {
        "format": "json",
        "include_charts": False,
        "include_explanations": False
    }

    exporter = JsonExporter()
    result = exporter.export(data, str(output_path), options)

    # Verify result structure
    assert isinstance(result, dict)
    assert result.get("path") == str(output_path)
    assert result.get("format") == "json"
    assert "timestamp" in result

    # Verify file was created and content matches
    assert output_path.exists()
    with open(output_path, "r") as f:
        loaded = json.load(f)
    assert loaded == data


def test_json_exporter_overwrite(tmp_path):
    # Create an existing file
    output_path = tmp_path / "existing.json"
    output_path.write_text(json.dumps({"old": "data"}))

    data = {"new": "data"}
    options: ExportOptions = {
        "format": "json",
        "include_charts": False,
        "include_explanations": False
    }

    exporter = JsonExporter()
    result = exporter.export(data, str(output_path), options)

    # Verify overwrite
    assert output_path.exists()
    with open(output_path, "r") as f:
        loaded = json.load(f)
    assert loaded == data
