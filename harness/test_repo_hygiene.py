import subprocess
from pathlib import Path


def test_runtime_workspace_state_is_not_tracked():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["git", "ls-files", "workspaces/"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    tracked_runtime = [
        line
        for line in result.stdout.splitlines()
        if line and not line.endswith(".gitkeep") and not line.endswith("workspace.yaml")
    ]
    assert tracked_runtime == []
