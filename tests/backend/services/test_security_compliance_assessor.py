import pytest
from src.devops_metrics.backend.services.assessors.security_compliance import SecurityComplianceAssessor
from unittest.mock import Mock
from datetime import datetime, timezone

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
    return SecurityComplianceAssessor(scoring_service)

def test_assess_no_policies(assessor):
    repo_data = {}
    result = assessor.assess(repo_data, [], {})
    assert result['score'] == 0.0
    assert 'No policy data available' in result['explanation'][0]
    assert result['metrics'] == {}

def test_assess_policy_compliance(assessor):
    repo_data = {
        'policies': {
            'statistics': {
                'total_policies': 5,
                'enabled_policies': 4,
                'policy_types': ['Branch', 'Build', 'Security']
            }
        }
    }
    result = assessor.assess(repo_data, [], {})
    assert 'policy_compliance' in result['metrics']
    assert result['metrics']['policy_compliance'] == 80.0
    assert result['score'] > 0.0

def test_assess_policy_compliance_none_enabled(assessor):
    repo_data = {
        'policies': {
            'statistics': {
                'total_policies': 5,
                'enabled_policies': 0,
                'policy_types': ['Branch', 'Build']
            }
        }
    }
    result = assessor.assess(repo_data, [], {})
    assert 'policy_compliance' in result['metrics']
    assert result['metrics']['policy_compliance'] == 0.0
    assert result['score'] >= 0.0

def test_assess_security_scanning(assessor):
    repo_data = {
        'policies': {
            'statistics': {
                'total_policies': 3,
                'enabled_policies': 2,
                'policy_types': ['Security', 'Vulnerability', 'Build']
            }
        }
    }
    builds = [
        {'id': '1', 'steps': [{'name': 'Security Scan'}]},
        {'id': '2', 'steps': [{'name': 'Build'}]}
    ]
    build_data = {
        '1': {'security_scans': [{'name': 'CodeQL.sarif'}]},
        '2': {'security_scans': []}
    }
    result = assessor.assess(repo_data, builds, build_data)
    assert 'security_scanning' in result['metrics']
    assert result['metrics']['security_scanning'] > 0.0
    assert result['score'] > 0.0

def test_assess_branch_protection(assessor):
    repo_data = {
        'policies': {
            'statistics': {
                'total_policies': 2,
                'enabled_policies': 2,
                'policy_types': ['Branch', 'Build']
            }
        },
        'branches': [
            {
                'protected': True,
                'protection_rules': {
                    'required_reviews': 2,
                    'required_status_checks': True,
                    'enforce_admins': True,
                    'linear_history': True
                }
            },
            {
                'protected': False
            }
        ]
    }
    result = assessor.assess(repo_data, [], {})
    assert 'branch_protection' in result['metrics']
    assert result['metrics']['branch_protection'] > 0.0
    assert result['score'] > 0.0

def test_assess_missing_fields(assessor):
    repo_data = {
        'policies': {
            # missing statistics
        },
        'branches': [
            # missing protection_rules
            {'protected': True},
            {}
        ]
    }
    result = assessor.assess(repo_data, [], {})
    # Should handle missing fields gracefully
    # All metrics may be missing or None, but should not error
    assert isinstance(result['metrics'], dict)
    assert result['score'] >= 0.0 
