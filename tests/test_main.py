"""Tests for the __main__ module"""

def test_main_imports():
    """Test that the main module can be imported and contains the app function"""
    # This test verifies that the module can be imported without errors
    from devops_metrics import __main__  # noqa: F401
    
    # Verify that the module has the expected attributes
    assert hasattr(__main__, 'app')


def test_example_environment_accepts_shared_compose_settings():
    """The documented shared .env must load without unknown-field errors."""
    from pathlib import Path

    from devops_metrics.config.settings import Settings

    settings = Settings(
        _env_file=Path(__file__).resolve().parents[1] / ".env.example",
        azure_devops_pat="synthetic-test-pat",
        azure_devops_org="example-org",
    )

    assert settings.azure_devops_org == "example-org"
    assert settings.azure_devops_pat.get_secret_value() == "synthetic-test-pat"
