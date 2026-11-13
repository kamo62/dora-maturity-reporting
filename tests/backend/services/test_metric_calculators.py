"""Tests for metric calculator classes"""

from datetime import datetime, timedelta, timezone
import pytest
from devops_metrics.backend.services.metric_calculators import (
    BuildMetrics,
    PRMetrics,
    CoverageMetrics,
)


@pytest.fixture
def sample_builds():
    now = datetime.now()
    return [
        {
            "id": "1",
            "result": "succeeded",
            "finishTime": (now - timedelta(days=1)).isoformat(),
            "steps": [{"id": "1"}, {"id": "2"}],
        },
        {
            "id": "2",
            "result": "failed",
            "finishTime": (now - timedelta(days=2)).isoformat(),
            "steps": [{"id": "1"}],
        },
        {
            "id": "3",
            "result": "succeeded",
            "finishTime": (now - timedelta(days=3)).isoformat(),
            "steps": [{"id": "1"}, {"id": "2"}, {"id": "3"}],
        },
    ]


@pytest.fixture
def sample_pull_requests():
    now = datetime.now()
    return [
        {
            "pullRequestId": "1",
            "creationDate": (now - timedelta(days=2)).isoformat(),
            "closedDate": (now - timedelta(days=1)).isoformat(),
            "additions": 100,
            "deletions": 50,
            "reviewers": [{"id": "1"}, {"id": "2"}],
            "comments": [{"id": "1"}, {"id": "2"}, {"id": "3"}],
        },
        {
            "pullRequestId": "2",
            "creationDate": (now - timedelta(days=3)).isoformat(),
            "closedDate": (now - timedelta(days=2)).isoformat(),
            "additions": 200,
            "deletions": 100,
            "reviewers": [{"id": "1"}],
            "comments": [{"id": "1"}],
        },
    ]


@pytest.fixture
def sample_build_data():
    return {
        "1": {
            "coverage": {
                "value": [
                    {"modules": [{"statistics": {"linesCovered": 80, "linesNotCovered": 20}}]}
                ]
            }
        },
        "2": {
            "coverage": {
                "value": [
                    {"modules": [{"statistics": {"linesCovered": 60, "linesNotCovered": 40}}]}
                ]
            }
        },
    }


def test_build_success_rate():
    """Test build success rate calculation"""
    now = datetime.now()
    builds = [
        {"result": "succeeded", "finishTime": now.isoformat()},
        {"result": "failed", "finishTime": (now - timedelta(days=1)).isoformat()},
        {"result": "succeeded", "finishTime": (now - timedelta(days=2)).isoformat()},
    ]

    results = BuildMetrics.calculate_success_rate(builds)
    assert len(results) == 3
    success_rates = [rate for rate, _ in results]
    assert success_rates.count(100.0) == 2  # Two succeeded builds
    assert success_rates.count(0.0) == 1  # One failed build


def test_deployment_frequency(sample_builds):
    """Test deployment frequency calculation"""
    results = BuildMetrics.calculate_deployment_frequency(sample_builds, window_days=7)
    assert len(results) == 3

    # All frequencies should be between 0 and 1 for this sample
    for freq, _ in results:
        assert 0 <= freq <= 1


def test_pr_size_metrics(sample_pull_requests):
    """Test PR size metrics calculation"""
    results = PRMetrics.calculate_size_metrics(sample_pull_requests)
    assert len(results) == 2

    sizes = [size for size, _ in results]
    assert 150 in sizes  # First PR: 100 additions + 50 deletions
    assert 300 in sizes  # Second PR: 200 additions + 100 deletions


def test_pr_review_depth(sample_pull_requests):
    """Test PR review depth calculation"""
    results = PRMetrics.calculate_review_depth(sample_pull_requests)
    assert len(results) == 2

    depths = [depth for depth, _ in results]
    # First PR: (2 reviewers * 0.6) + (3 comments * 0.4)
    assert depths[0] == pytest.approx(2.4, rel=0.1)
    # Second PR: (1 reviewer * 0.6) + (1 comment * 0.4)
    assert depths[1] == pytest.approx(1.0, rel=0.1)


def test_coverage_metrics(sample_builds, sample_build_data):
    """Test code coverage metrics calculation"""
    results = CoverageMetrics.calculate_coverage(sample_builds, sample_build_data)
    assert len(results) == 2

    coverage_values = [value for value, _ in results]
    assert 80.0 in coverage_values  # First build: 80/(80+20) * 100
    assert 60.0 in coverage_values  # Second build: 60/(60+40) * 100


def test_invalid_dates_handling():
    """Test handling of invalid dates"""
    builds = [
        {"result": "succeeded", "finishTime": "invalid-date"},
        {"result": "failed", "finishTime": None},
        {"result": "succeeded", "finishTime": datetime.now().isoformat()},
    ]

    results = BuildMetrics.calculate_success_rate(builds)
    assert len(results) == 1  # Only the valid date should be processed


def test_empty_input_handling():
    """Test handling of empty inputs"""
    assert BuildMetrics.calculate_success_rate([]) == []
    assert CoverageMetrics.calculate_coverage([], {}) == []


def test_missing_data_handling(sample_builds):
    """Test handling of missing data fields"""
    builds = [
        {"id": "1"},  # Missing result and finishTime
        {"id": "2", "result": "succeeded"},  # Missing finishTime
        {"id": "3", "finishTime": datetime.now().isoformat()},  # Missing result
    ]

    results = BuildMetrics.calculate_success_rate(builds)
    assert len(results) == 1  # Only the valid build should be processed
    assert results[0][0] == 0.0  # Success rate should be 0 for invalid builds


def test_time_ordering():
    """Test that results are properly time-ordered"""
    now = datetime.now()
    builds = [
        {"result": "succeeded", "finishTime": (now - timedelta(days=2)).isoformat()},
        {"result": "succeeded", "finishTime": now.isoformat()},
        {"result": "succeeded", "finishTime": (now - timedelta(days=1)).isoformat()},
    ]

    results = BuildMetrics.calculate_success_rate(builds)
    timestamps = sorted([ts for _, ts in results])
    assert all(
        a <= b for a, b in zip(timestamps[:-1], timestamps[1:])
    )  # Should be in chronological order


# Test with empty list
assert PRMetrics.calculate_review_process_score([]) is None
assert PRMetrics.calculate_size_metrics([]) == []
assert PRMetrics.calculate_time_to_first_review([]) == []

# --- BuildMetrics ---
def test_calculate_success_rate_empty():
    assert BuildMetrics.calculate_success_rate([]) == []

def test_calculate_success_rate_valid():
    dt = datetime.now(timezone.utc)
    builds = [
        {"finishTime": dt.isoformat(), "result": "succeeded"},
        {"finishTime": dt.isoformat(), "result": "failed"},
    ]
    result = BuildMetrics.calculate_success_rate(builds)
    assert result == [(100.0, dt), (0.0, dt)]

def test_calculate_pipeline_metrics_empty():
    assert BuildMetrics.calculate_pipeline_metrics([]) is None

def test_calculate_pipeline_metrics_valid():
    builds = [
        {"definition": {"continuous_integration": True, "steps": [{"type": "build"}, {"type": "test"}]}},
        {"definition": {"continuous_delivery": True, "yaml_based": True, "steps": [{"type": "deploy"}, {"type": "quality"}]}}
    ]
    score = BuildMetrics.calculate_pipeline_metrics(builds)
    assert isinstance(score, float)
    assert score > 0

def test_calculate_deployment_frequency_empty():
    assert BuildMetrics.calculate_deployment_frequency([]) == []

def test_calculate_deployment_frequency_valid():
    dt = datetime.now(timezone.utc)
    builds = [
        {"finishTime": dt.isoformat()},
        {"finishTime": (dt + timedelta(days=1)).isoformat()},
    ]
    result = BuildMetrics.calculate_deployment_frequency(builds, window_days=2)
    assert all(isinstance(freq, float) and isinstance(ts, datetime) for freq, ts in result)

# --- PRMetrics ---
def test_calculate_review_process_score_empty():
    assert PRMetrics.calculate_review_process_score([]) is None

def test_calculate_review_process_score_valid():
    prs = [
        {"auto_complete": True, "policies": {"required_reviewers": 2}, "reviews": [{"status": "approved"}, {"status": "rejected"}]},
        {"draft": True, "policies": {"required_reviewers": 3, "comment_requirements": True}, "reviews": [{"status": "approved", "comments": ["a"]}]}
    ]
    score = PRMetrics.calculate_review_process_score(prs)
    assert isinstance(score, float)
    assert score > 0

def test_calculate_size_metrics_empty():
    assert PRMetrics.calculate_size_metrics([]) == []

def test_calculate_size_metrics_valid():
    dt = datetime.now(timezone.utc)
    prs = [
        {"closedDate": dt.isoformat(), "additions": 10, "deletions": 5},
        {"closedDate": dt.isoformat(), "additions": 0, "deletions": 0},  # Should be skipped
    ]
    result = PRMetrics.calculate_size_metrics(prs)
    assert result == [(15, dt)]

def test_calculate_time_to_first_review_empty():
    assert PRMetrics.calculate_time_to_first_review([]) == []

def test_calculate_time_to_first_review_valid():
    dt = datetime.now(timezone.utc)
    review_time = dt + timedelta(hours=2)
    prs = [
        {"creationDate": dt.isoformat(), "reviews": [{"date": review_time.isoformat()}]},
        {"creationDate": dt.isoformat(), "reviews": []},  # Should be skipped
    ]
    result = PRMetrics.calculate_time_to_first_review(prs)
    assert result == [(2.0, dt)]

def test_calculate_review_depth_empty():
    assert PRMetrics.calculate_review_depth([]) == []

def test_calculate_review_depth_valid():
    dt = datetime.now(timezone.utc)
    prs = [
        {"closedDate": dt.isoformat(), "reviewers": [1, 2], "comments": [1]},
        {"closedDate": dt.isoformat(), "reviewers": [], "comments": []},
    ]
    result = PRMetrics.calculate_review_depth(prs)
    assert result[0][0] == (2 * 0.6) + (1 * 0.4)
    assert result[0][1] == dt

# --- CoverageMetrics ---
def test_calculate_coverage_empty():
    assert CoverageMetrics.calculate_coverage([], {}) == []

def test_calculate_coverage_valid():
    dt = datetime.now(timezone.utc)
    builds = [{"id": "1", "finishTime": dt.isoformat()}]
    build_data = {
        "1": {
            "coverage": {
                "value": [
                    {"modules": [
                        {"statistics": {"linesCovered": 80, "linesNotCovered": 20}}
                    ]}
                ]
            }
        }
    }
    result = CoverageMetrics.calculate_coverage(builds, build_data)
    assert result == [(80.0, dt)]
