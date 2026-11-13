import os
import subprocess
import sys
from pathlib import Path


def test_main_entrypoint_displays_help():
    env = os.environ.copy()
    source_dir = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [source_dir, env.get("PYTHONPATH", "")])
    )

    result = subprocess.run(
        [sys.executable, "-m", "devops_metrics", "--help"],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    assert "Usage:" in result.stdout
    assert "collect-raw" in result.stdout
