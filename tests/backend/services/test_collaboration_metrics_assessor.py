import pytest
from src.devops_metrics.backend.services.assessors.collaboration_metrics import CollaborationMetricsAssessor
from unittest.mock import Mock
from datetime import datetime, timezone

@pytest.fixture
def scoring_service():
    mock = Mock()
    mock.determine_maturity_level.side_effect = lambda score: 1 if score < 0.2 else 2 if score < 0.4 else 3 if score < 0.6 else 4 if score < 0.8 else 5
    mock.calculate_metric_score.side_effect = lambda values, thresholds: values[0][0] / 100.0 if values else 0.0
    def _penalty(base, missing, explanation=None, exempt_metrics=None):
        if exempt_metrics:
            missing = [(n, w) for (n, w) in missing if n not in exempt_metrics]
        return base * (1 - 0.5 * len(missing))
    mock.apply_missing_metrics_penalty.side_effect = _penalty
    return mock

@pytest.fixture
def assessor(scoring_service):
    return CollaborationMetricsAssessor(scoring_service)

def test_assess_no_pull_requests(assessor):
    repo_data = {}
    result = assessor.assess(repo_data, [], {})
    assert result['score'] == 0.0
    assert 'No pull request data available' in result['explanation'][0]

def test_assess_all_merged(assessor):
    repo_data = {'pull_requests': [
        {'status': 'completed', 'reviewers': [{'id': 1}]},
        {'status': 'completed', 'reviewers': [{'id': 2}]}
    ]}
    result = assessor.assess(repo_data, [], {})
    assert result['metrics']['merge_rate'] == 100.0
    assert result['metrics']['review_coverage'] == 100.0
    assert result['score'] > 0.0

def test_assess_none_merged(assessor):
    repo_data = {'pull_requests': [
        {'status': 'active', 'reviewers': [{'id': 1}]},
        {'status': 'abandoned', 'reviewers': [{'id': 2}]}
    ]}
    result = assessor.assess(repo_data, [], {})
    assert result['metrics']['merge_rate'] == 0.0
    assert result['metrics']['review_coverage'] == 100.0
    assert result['score'] >= 0.0

def test_assess_none_reviewed(assessor):
    repo_data = {'pull_requests': [
        {'status': 'completed', 'reviewers': []},
        {'status': 'completed', 'reviewers': []}
    ]}
    result = assessor.assess(repo_data, [], {})
    assert result['metrics']['merge_rate'] == 100.0
    assert result['metrics']['review_coverage'] == 0.0
    assert result['score'] >= 0.0

def test_assess_mixed_cases(assessor):
    repo_data = {'pull_requests': [
        {'status': 'completed', 'reviewers': [{'id': 1}]},
        {'status': 'active', 'reviewers': []},
        {'status': 'completed', 'reviewers': []},
        {'status': 'abandoned', 'reviewers': [{'id': 2}]}
    ]}
    result = assessor.assess(repo_data, [], {})
    assert 0.0 < result['metrics']['merge_rate'] < 100.0
    assert 0.0 < result['metrics']['review_coverage'] < 100.0
    assert result['score'] >= 0.0

def test_calculate_merge_rate_edge_cases(assessor):
    # No PRs
    assert assessor._calculate_merge_rate([]) is None
    # All merged
    prs = [{'status': 'completed'} for _ in range(5)]
    assert assessor._calculate_merge_rate(prs) == 100.0
    # None merged
    prs = [{'status': 'active'} for _ in range(5)]
    assert assessor._calculate_merge_rate(prs) == 0.0
    # Mixed
    prs = [{'status': 'completed'} for _ in range(3)] + [{'status': 'active'} for _ in range(2)]
    assert assessor._calculate_merge_rate(prs) == 60.0

def test_calculate_review_coverage_edge_cases(assessor):
    # No PRs
    assert assessor._calculate_review_coverage([]) is None
    # All reviewed
    prs = [{'reviewers': [{}]} for _ in range(4)]
    assert assessor._calculate_review_coverage(prs) == 100.0
    # None reviewed
    prs = [{'reviewers': []} for _ in range(4)]
    assert assessor._calculate_review_coverage(prs) == 0.0
    # Mixed
    prs = [{'reviewers': [{}]} for _ in range(2)] + [{'reviewers': []} for _ in range(2)]
    assert assessor._calculate_review_coverage(prs) == 50.0 
