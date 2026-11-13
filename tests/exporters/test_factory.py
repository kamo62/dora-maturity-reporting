import pytest
from devops_metrics.exporters.factory import ExporterFactory
from devops_metrics.exporters.base_exporter import BaseExporter

class DummyExporter(BaseExporter):
    def export(self, data, path, options):
        return {"path": path, "format": "dummy", "timestamp": "now"}

def test_create_supported_exporters():
    excel = ExporterFactory.create_exporter("excel")
    assert excel.__class__.__name__ == "ExcelExporter"
    json_exp = ExporterFactory.create_exporter("json")
    assert json_exp.__class__.__name__ == "JsonExporter"

def test_create_unsupported_raises():
    with pytest.raises(ValueError) as exc:
        ExporterFactory.create_exporter("xml")
    assert "Unsupported export format" in str(exc.value)

def test_register_and_create_exporter(monkeypatch):
    # Register dummy exporter
    ExporterFactory.register_exporter("dummy", DummyExporter)
    dummy = ExporterFactory.create_exporter("dummy")
    assert isinstance(dummy, DummyExporter)
    # Clean up by removing dummy
    ExporterFactory._exporters.pop("dummy", None)

def test_register_existing_raises():
    with pytest.raises(ValueError) as exc:
        ExporterFactory.register_exporter("excel", DummyExporter)
    assert "already registered" in str(exc.value)
