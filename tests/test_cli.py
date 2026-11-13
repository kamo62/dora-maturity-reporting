from typer.testing import CliRunner

from devops_metrics.cli import app


runner = CliRunner()


def test_cli_help_lists_collection_command():
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "collect-raw" in result.stdout


def test_cli_rejects_unknown_collection_option():
    result = runner.invoke(app, ["collect-raw", "--not-a-real-option"])

    assert result.exit_code == 2
    assert "No such option" in result.output
