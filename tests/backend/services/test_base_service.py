"""Tests for the base metrics service."""

import pytest
from typing import Dict, Any, Optional
from devops_metrics.backend.services.base_service import MetricsService


def test_cannot_instantiate_abstract_base():
    """Test that MetricsService cannot be instantiated directly."""
    with pytest.raises(TypeError):
        MetricsService()


class MockMetricsService(MetricsService):
    """Mock implementation of MetricsService for testing."""

    def __init__(self):
        self._cache = None

    async def collect_metrics(self) -> Dict[str, Any]:
        """Mock implementation of collect_metrics."""
        metrics = {"test_metric": 42}
        self._cache = metrics
        return metrics

    async def get_cached_metrics(self) -> Optional[Dict[str, Any]]:
        """Mock implementation of get_cached_metrics."""
        return self._cache


@pytest.mark.asyncio
async def test_mock_metrics_service():
    """Test that a concrete implementation works as expected."""
    service = MockMetricsService()

    # Initially cache should be None
    assert await service.get_cached_metrics() is None

    # Collect metrics should return expected data
    metrics = await service.collect_metrics()
    assert metrics == {"test_metric": 42}

    # Cache should now contain the collected metrics
    cached = await service.get_cached_metrics()
    assert cached == {"test_metric": 42}


def test_missing_implementation():
    """Test that missing abstract method implementations raise TypeError."""

    with pytest.raises(TypeError):
        # Missing both methods
        class InvalidService1(MetricsService):
            pass

        InvalidService1()

    with pytest.raises(TypeError):
        # Missing collect_metrics
        class InvalidService2(MetricsService):
            async def get_cached_metrics(self) -> Optional[Dict[str, Any]]:
                return None

        InvalidService2()

    with pytest.raises(TypeError):
        # Missing get_cached_metrics
        class InvalidService3(MetricsService):
            async def collect_metrics(self) -> Dict[str, Any]:
                return {}

        InvalidService3()
