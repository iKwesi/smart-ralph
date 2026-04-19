import json
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SMART_RALPH = REPO / "smart-ralph"
FIXTURES = Path(__file__).parent / "fixtures" / "fake_ralph"


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    (path / ".gitignore").write_text(".smart-ralph/\n.ralph/\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit",
         "-q", "-m", "init"],
        cwd=path, check=True,
    )


def test_cli_runs_end_to_end_and_writes_lifecycle_events(tmp_path):
    _init_repo(tmp_path)

    env = os.environ.copy()
    env["SMART_RALPH_RALPH_PATH"] = str(FIXTURES / "echo_stdout.sh")
    # /usr/bin/env python3 in the shebang must resolve to the venv's python
    # so the installed deps (rich, pydantic) are available.
    venv_bin = REPO / ".venv" / "bin"
    env["PATH"] = f"{venv_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [str(SMART_RALPH), "17"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, (
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )

    events_path = tmp_path / ".smart-ralph" / "events.jsonl"
    assert events_path.exists()
    types = [
        json.loads(line)["type"]
        for line in events_path.read_text().splitlines()
    ]
    lifecycle = [
        t for t in types
        if t in {"run_started", "ralph_spawned", "ralph_exited", "run_ended"}
    ]
    assert lifecycle == [
        "run_started", "ralph_spawned", "ralph_exited", "run_ended",
    ]

    # non-TTY stdout → plain-text dashboard → ralph's stdout line surfaces
    assert "iteration: start prd=17" in result.stdout
