import pytest
from datetime import datetime, timezone, timedelta

from devops_metrics.backend.services.scoring.scoring_service import ScoringService


@pytest.fixture
def scoring_service():
    return ScoringService()


def test_determine_maturity_level():
    svc = ScoringService()
    assert svc.determine_maturity_level(0.0) == 1
    assert svc.determine_maturity_level(0.21) == 2
    assert svc.determine_maturity_level(0.41) == 3
    assert svc.determine_maturity_level(0.61) == 4
    assert svc.determine_maturity_level(0.81) == 5


def test_get_maturity_label():
    svc = ScoringService()
    assert svc.get_maturity_label(1) == "Initial"
    assert svc.get_maturity_label(5) == "Optimizing"
    assert svc.get_maturity_label(999) == "Unknown"


def test_apply_missing_metrics_penalty_no_penalty():
    svc = ScoringService()
    base = 0.8
    result = svc.apply_missing_metrics_penalty(base, [])
    assert result == pytest.approx(base)


def test_apply_missing_metrics_penalty_with_penalty_and_explanation():
    svc = ScoringService()
    base = 1.0
    missing = [("m1", 0.1), ("m2", 0.2)]
    explanations = []
    result = svc.apply_missing_metrics_penalty(base, missing, explanations)
    # penalty capped at 25%, adjusted = 0.75
    assert result == pytest.approx(0.75)
    assert "Applied 25.0% penalty for missing metrics" in explanations[0]


def test_apply_missing_metrics_penalty_cap_at_25_percent():
    svc = ScoringService()
    base = 1.0
    # sum weights exceed 0.25; should cap at 25%
    missing = [("m1", 0.5), ("m2", 0.6)]
    result = svc.apply_missing_metrics_penalty(base, missing)
    # penalty capped at 0.25 -> adjusted = 0.75 (and floor remains 0.1 if needed)
    assert result == pytest.approx(0.75)


def test_calculate_average():
    svc = ScoringService()
    assert svc._calculate_average([]) == 0.0
    assert svc._calculate_average([1.0, 2.0, 3.0]) == pytest.approx(2.0)


def test_calculate_weighted_average_empty():
    svc = ScoringService()
    assert svc.calculate_weighted_average({}, {}) == 0.0


def test_calculate_weighted_average_normal():
    svc = ScoringService()
    scores = {"a": 1.0, "b": 2.0}
    weights = {"a": 0.5, "b": 0.5}
    assert svc.calculate_weighted_average(scores, weights) == pytest.approx(1.5)


def test_calculate_metric_score_empty_inputs():
    svc = ScoringService()
    assert svc.calculate_metric_score([], {}, reverse=False) == 0.0
    assert svc.calculate_metric_score([], {"level1": 1.0}, reverse=True) == 0.0


def test_calculate_metric_score_time_weighted_and_thresholds():
    svc = ScoringService()
    # fix current month start
    now = datetime(2025, 1, 15, tzinfo=timezone.utc)
    svc._get_current_month_start = lambda: datetime(2025, 1, 1, tzinfo=timezone.utc)
    # values: one current, one historical
    values = [
        (0.5, now),  # current
        (0.3, now - timedelta(days=60))  # historical
    ]
    thresholds = {
        "level1": 0.0,
        "level2": 0.2,
        "level3": 0.4,
        "level4": 0.6,
        "level5": 0.8
    }
    score = svc.calculate_metric_score(values, thresholds)
    # weighted_value = 0.5*0.8 + 0.3*0.2 = 0.46 => level3 => base 0.4 + progress 0.06 = 0.46
    assert score == pytest.approx(0.46)


def test_calculate_metric_score_only_current_month():
    svc = ScoringService()
    now = datetime(2025, 2, 20, tzinfo=timezone.utc)
    svc._get_current_month_start = lambda: datetime(2025, 2, 1, tzinfo=timezone.utc)
    values = [
        (0.7, now)
    ]
    thresholds = {"level1": 0.0, "level2": 0.2, "level3": 0.4, "level4": 0.6, "level5": 0.8}
    score = svc.calculate_metric_score(values, thresholds)
    # only current -> avg 0.7 => level4 threshold => base=0.6 + progress 0.1 = 0.7
    assert score == pytest.approx(0.7)


def test_calculate_metric_score_reverse_behavior():
    svc = ScoringService()
    now = datetime(2025, 3, 10, tzinfo=timezone.utc)
    svc._get_current_month_start = lambda: datetime(2025, 3, 1, tzinfo=timezone.utc)
    values = [(2.0, now)]
    thresholds = {"level1": 1.0}
    score = svc.calculate_metric_score(values, thresholds, reverse=True)
    # value > threshold -> returns 0.1
    assert score == pytest.approx(0.1)


def test_calculate_metric_score_invalid_timestamp_skips():
    svc = ScoringService()
    svc._get_current_month_start = lambda: datetime.now(timezone.utc)
    # invalid timestamp string
    values = [(0.5, "not-a-date")]
    thresholds = {"level1": 0.1}
    assert svc.calculate_metric_score(values, thresholds) == pytest.approx(0.0)
