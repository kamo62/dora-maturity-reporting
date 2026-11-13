import pytest
from src.devops_metrics.backend.services.assessors.quality_assurance import QualityAssuranceAssessor
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
    mock.get_maturity_label.side_effect = lambda level: f"Level {level}"
    return mock

@pytest.fixture
def assessor(scoring_service):
    return QualityAssuranceAssessor(scoring_service)

def test_assess_no_builds(assessor):
    repo_data = {}
    result = assessor.assess(repo_data, [], {}, "TestProject")
    assert result['score'] == 0.0
    assert 'No build or coverage data available' in result['explanation'][0]

def test_assess_builds_with_coverage_and_tests(assessor):
    builds = [
        {'id': '1', 'result': 'succeeded', 'finishTime': datetime.now(timezone.utc).isoformat(), 'repository': {'id': 'repo1', 'name': 'Repo1'}},
        {'id': '2', 'result': 'failed', 'finishTime': datetime.now(timezone.utc).isoformat(), 'repository': {'id': 'repo1', 'name': 'Repo1'}},
    ]
    build_data = {
        '1': {
            'coverage': {'value': [{'modules': [{'statistics': {'linesCovered': 80, 'linesNotCovered': 20}}]}]},
            'tests': {'results': [{'total': 100, 'passed': 90, 'failed': 10}]}
        },
        '2': {
            'coverage': {'value': [{'modules': [{'statistics': {'linesCovered': 60, 'linesNotCovered': 40}}]}]},
            'tests': {'results': [{'total': 50, 'passed': 25, 'failed': 25}]}
        }
    }
    repo_data = {'id': 'repo1'}
    result = assessor.assess(repo_data, builds, build_data, "TestProject")
    assert 'code_coverage' in result['metrics']
    assert 'test_pass_rate' in result['metrics']
    assert 'build_success_rate' in result['metrics']
    assert result['score'] > 0.0

def test_assess_builds_no_coverage(assessor):
    builds = [
        {'id': '1', 'result': 'succeeded', 'finishTime': datetime.now(timezone.utc).isoformat(), 'repository': {'id': 'repo1', 'name': 'Repo1'}},
    ]
    build_data = {
        '1': {
            'coverage': {'value': []},
            'tests': {'results': [{'total': 100, 'passed': 100, 'failed': 0}]}
        }
    }
    repo_data = {'id': 'repo1'}
    result = assessor.assess(repo_data, builds, build_data, "TestProject")
    # With no actual coverage modules, code_coverage should be missing
    assert 'code_coverage' not in result['metrics']
    # Test pass rate is still computed independently
    assert 'test_pass_rate' in result['metrics']
    assert result['score'] >= 0.0

def test_assess_builds_no_tests(assessor):
    builds = [
        {'id': '1', 'result': 'succeeded', 'finishTime': datetime.now(timezone.utc).isoformat(), 'repository': {'id': 'repo1', 'name': 'Repo1'}},
    ]
    build_data = {
        '1': {
            'coverage': {'value': [{'modules': [{'statistics': {'linesCovered': 10, 'linesNotCovered': 90}}]}]},
            'tests': {'results': []}
        }
    }
    repo_data = {'id': 'repo1'}
    result = assessor.assess(repo_data, builds, build_data, "TestProject")
    assert 'test_pass_rate' not in result['metrics'] or result['metrics']['test_pass_rate'] == 0.0
    assert 'code_coverage' in result['metrics']
    assert result['score'] >= 0.0

def test_assess_all_builds_pass(assessor):
    builds = [
        {'id': '1', 'result': 'succeeded', 'finishTime': datetime.now(timezone.utc).isoformat(), 'repository': {'id': 'repo1', 'name': 'Repo1'}},
        {'id': '2', 'result': 'succeeded', 'finishTime': datetime.now(timezone.utc).isoformat(), 'repository': {'id': 'repo1', 'name': 'Repo1'}},
    ]
    build_data = {
        '1': {'coverage': {'value': []}, 'tests': {'results': []}},
        '2': {'coverage': {'value': []}, 'tests': {'results': []}},
    }
    repo_data = {'id': 'repo1'}
    result = assessor.assess(repo_data, builds, build_data, "TestProject")
    assert result['metrics']['build_success_rate'] == 100.0

def test_assess_all_builds_fail(assessor):
    builds = [
        {'id': '1', 'result': 'failed', 'finishTime': datetime.now(timezone.utc).isoformat(), 'repository': {'id': 'repo1', 'name': 'Repo1'}},
        {'id': '2', 'result': 'failed', 'finishTime': datetime.now(timezone.utc).isoformat(), 'repository': {'id': 'repo1', 'name': 'Repo1'}},
    ]
    build_data = {
        '1': {'coverage': {'value': []}, 'tests': {'results': []}},
        '2': {'coverage': {'value': []}, 'tests': {'results': []}},
    }
    repo_data = {'id': 'repo1'}
    result = assessor.assess(repo_data, builds, build_data, "TestProject")
    assert result['metrics']['build_success_rate'] == 0.0

def test_calculate_coverage_edge_cases(assessor):
    # No builds
    assert assessor._calculate_coverage([], {}) == []
    # Build with no coverage
    builds = [{'id': '1', 'finishTime': datetime.now(timezone.utc).isoformat(), 'repository': {'id': 'repo1', 'name': 'Repo1'}}]
    build_data = {'1': {'coverage': {'value': []}, 'tests': {'results': []}}}
    assert assessor._calculate_coverage(builds, build_data) == []
    # Build with coverage
    builds = [{'id': '1', 'finishTime': datetime.now(timezone.utc).isoformat(), 'repository': {'id': 'repo1', 'name': 'Repo1'}}]
    build_data = {'1': {'coverage': {'value': [{'modules': [{'statistics': {'linesCovered': 10, 'linesNotCovered': 0}}]}], 'tests': {'results': []}}}}
    result = assessor._calculate_coverage(builds, build_data)
    assert result and result[0][0] == 100.0

def test_calculate_test_pass_rate_edge_cases(assessor):
    # No builds
    assert assessor._calculate_test_pass_rate([], {}, []) is None
    # Build with test results
    builds = [{'id': '1'}]
    build_data = {'1': {'tests': {'results': [{'total': 10, 'failed': 2}]}}}
    assert assessor._calculate_test_pass_rate(builds, build_data, []) == 80.0

def test_calculate_build_success_rate_edge_cases(assessor):
    # No builds
    assert assessor._calculate_build_success_rate([], []) is None
    # All succeeded
    builds = [{'result': 'succeeded'}, {'result': 'succeeded'}]
    assert assessor._calculate_build_success_rate(builds, []) == 100.0
    # All failed
    builds = [{'result': 'failed'}, {'result': 'failed'}]
    assert assessor._calculate_build_success_rate(builds, []) == 0.0
    # Mixed
    builds = [{'result': 'succeeded'}, {'result': 'failed'}]
    assert assessor._calculate_build_success_rate(builds, []) == 50.0 
