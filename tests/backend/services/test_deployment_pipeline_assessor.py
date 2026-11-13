import pytest
from src.devops_metrics.backend.services.assessors.deployment_pipeline import DeploymentPipelineAssessor
from unittest.mock import Mock
from datetime import datetime, timezone, timedelta

@pytest.fixture
def scoring_service():
    mock = Mock()
    mock.determine_maturity_level.side_effect = lambda score: 1 if score < 0.2 else 2 if score < 0.4 else 3 if score < 0.6 else 4 if score < 0.8 else 5
    mock.calculate_metric_score.side_effect = lambda values, thresholds, **kwargs: values[0][0] / 100.0 if values else 0.0
    def _penalty(base, missing, explanation=None, exempt_metrics=None):
        if exempt_metrics:
            missing = [(n, w) for (n, w) in missing if n not in exempt_metrics]
        return base * (1 - 0.5 * len(missing))
    mock.apply_missing_metrics_penalty.side_effect = _penalty
    mock.get_maturity_label.side_effect = lambda level: f"Level {level}"
    return mock

@pytest.fixture
def assessor(scoring_service):
    return DeploymentPipelineAssessor(scoring_service)

def test_assess_no_builds(assessor):
    repo_data = {}
    result = assessor.assess(repo_data, [], {}, "TestProject")
    assert result['score'] == 0.0
    assert 'No build data available' in result['explanation'][0]

def test_assess_all_builds_succeed(assessor):
    now = datetime.now(timezone.utc)
    builds = [
        {'id': '1', 'result': 'succeeded', 'finishTime': now.isoformat(), 'startTime': (now - timedelta(minutes=10)).isoformat(), 'repository': {'id': 'repo1', 'name': 'Repo1'}},
        {'id': '2', 'result': 'succeeded', 'finishTime': (now - timedelta(days=1)).isoformat(), 'startTime': (now - timedelta(days=1, minutes=10)).isoformat(), 'repository': {'id': 'repo1', 'name': 'Repo1'}},
    ]
    build_data = {
        '1': {'steps': [{'name': 'Build'}, {'name': 'Deploy'}]},
        '2': {'steps': [{'name': 'Build'}, {'name': 'Test'}, {'name': 'Deploy'}]},
    }
    repo_data = {'id': 'repo1'}
    result = assessor.assess(repo_data, builds, build_data, "TestProject")
    assert result['metrics']['pipeline_stability'] == 100.0
    assert 'deployment_frequency' in result['metrics']
    assert 'build_time' in result['metrics']
    assert result['score'] > 0.0

def test_assess_all_builds_fail(assessor):
    now = datetime.now(timezone.utc)
    builds = [
        {'id': '1', 'result': 'failed', 'finishTime': now.isoformat(), 'startTime': (now - timedelta(minutes=10)).isoformat(), 'repository': {'id': 'repo1', 'name': 'Repo1'}},
        {'id': '2', 'result': 'failed', 'finishTime': (now - timedelta(days=1)).isoformat(), 'startTime': (now - timedelta(days=1, minutes=10)).isoformat(), 'repository': {'id': 'repo1', 'name': 'Repo1'}},
    ]
    build_data = {
        '1': {'steps': [{'name': 'Build'}]},
        '2': {'steps': [{'name': 'Build'}]},
    }
    repo_data = {'id': 'repo1'}
    result = assessor.assess(repo_data, builds, build_data, "TestProject")
    assert result['metrics']['pipeline_stability'] == 0.0
    assert result['score'] >= 0.0

def test_assess_mixed_builds(assessor):
    now = datetime.now(timezone.utc)
    builds = [
        {'id': '1', 'result': 'succeeded', 'finishTime': now.isoformat(), 'startTime': (now - timedelta(minutes=10)).isoformat(), 'repository': {'id': 'repo1', 'name': 'Repo1'}},
        {'id': '2', 'result': 'failed', 'finishTime': (now - timedelta(days=1)).isoformat(), 'startTime': (now - timedelta(days=1, minutes=10)).isoformat(), 'repository': {'id': 'repo1', 'name': 'Repo1'}},
    ]
    build_data = {
        '1': {'steps': [{'name': 'Build'}, {'name': 'Deploy'}]},
        '2': {'steps': [{'name': 'Build'}]},
    }
    repo_data = {'id': 'repo1'}
    result = assessor.assess(repo_data, builds, build_data, "TestProject")
    assert 0.0 < result['metrics']['pipeline_stability'] < 100.0
    assert result['score'] >= 0.0

def test_assess_no_deployments(assessor):
    now = datetime.now(timezone.utc)
    builds = [
        {'id': '1', 'result': 'failed', 'finishTime': now.isoformat(), 'startTime': (now - timedelta(minutes=10)).isoformat(), 'repository': {'id': 'repo1', 'name': 'Repo1'}},
    ]
    build_data = {
        '1': {'steps': [{'name': 'Build'}]},
    }
    repo_data = {'id': 'repo1'}
    result = assessor.assess(repo_data, builds, build_data, "TestProject")
    assert 'deployment_frequency' not in result['metrics'] or result['metrics']['deployment_frequency'] is None
    assert result['score'] >= 0.0

def test_assess_pipeline_complexity(assessor):
    now = datetime.now(timezone.utc)
    builds = [
        {'id': '1', 'result': 'succeeded', 'finishTime': now.isoformat(), 'startTime': (now - timedelta(minutes=10)).isoformat(), 'repository': {'id': 'repo1', 'name': 'Repo1'}},
    ]
    build_data = {
        '1': {'steps': [{'name': f'Step{i}'} for i in range(10)]},
    }
    repo_data = {'id': 'repo1'}
    result = assessor.assess(repo_data, builds, build_data, "TestProject")
    assert 'build_time' in result['metrics']
    assert result['score'] >= 0.0

def test_calculate_pipeline_stability_edge_cases(assessor):
    # No builds
    assert assessor._calculate_pipeline_stability([]) == []
    # All succeeded
    now = datetime.now(timezone.utc)
    builds = [
        {'result': 'succeeded', 'finishTime': now.isoformat()},
        {'result': 'succeeded', 'finishTime': now.isoformat()},
    ]
    stabilities = assessor._calculate_pipeline_stability(builds)
    assert all(rate == 100.0 for rate, _ in stabilities)
    # All failed
    builds = [
        {'result': 'failed', 'finishTime': now.isoformat()},
        {'result': 'failed', 'finishTime': now.isoformat()},
    ]
    stabilities = assessor._calculate_pipeline_stability(builds)
    assert all(rate == 0.0 for rate, _ in stabilities)
    # Mixed
    builds = [
        {'result': 'succeeded', 'finishTime': now.isoformat()},
        {'result': 'failed', 'finishTime': now.isoformat()},
    ]
    stabilities = assessor._calculate_pipeline_stability(builds)
    rates = [rate for rate, _ in stabilities]
    assert 0.0 in rates and 100.0 in rates

def test_calculate_deployment_frequency_edge_cases(assessor):
    # No builds
    assert assessor._calculate_deployment_frequency([]) is None
    # One deployment
    now = datetime.now(timezone.utc)
    builds = [
        {'finishTime': now.isoformat(), 'result': 'succeeded'},
    ]
    assert assessor._calculate_deployment_frequency(builds) == 1.0
    # Multiple deployments in 30 days
    builds = [
        {'finishTime': (now - timedelta(days=i)).isoformat(), 'result': 'succeeded'} for i in range(5)
    ]
    freq = assessor._calculate_deployment_frequency(builds)
    assert freq > 0.0

def test_calculate_build_times_edge_cases(assessor):
    # No builds
    assert assessor._calculate_build_times([]) == []
    # Build with times
    now = datetime.now(timezone.utc)
    builds = [
        {'startTime': (now - timedelta(minutes=10)).isoformat(), 'finishTime': now.isoformat()},
        {'startTime': (now - timedelta(minutes=20)).isoformat(), 'finishTime': now.isoformat()},
    ]
    times = assessor._calculate_build_times(builds)
    assert len(times) == 2
    assert all(isinstance(t[0], float) for t in times) 
