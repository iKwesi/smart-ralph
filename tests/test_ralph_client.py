import json
import subprocess
import threading
import time
from pathlib import Path

from smart_ralph.ralph_client import RalphClient

FIXTURES = Path(__file__).parent / "fixtures" / "fake_ralph"


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit",
         "--allow-empty", "-q", "-m", "init"],
        cwd=path, check=True,
    )


def test_spawn_injects_smart_ralph_env_vars(tmp_path):
    events_path = tmp_path / ".smart-ralph" / "events.jsonl"
    client = RalphClient(
        ralph_path=FIXTURES / "echo_env.sh",
        cwd=tmp_path,
        run_id="run-xyz",
        events_path=events_path,
    )

    process = client.spawn(issue=2)
    events = list(process.events())
    process.wait()

    stdout_lines = [
        e["payload"]["line"] for e in events if e["type"] == "ralph_stdout"
    ]
    assert f"SMART_RALPH_RUN_ID=run-xyz" in stdout_lines
    assert f"SMART_RALPH_EVENTS_PATH={events_path}" in stdout_lines


def test_spawn_yields_stdout_lines_as_events(tmp_path):
    client = RalphClient(
        ralph_path=FIXTURES / "echo_stdout.sh",
        cwd=tmp_path,
    )

    process = client.spawn(issue=2)
    events = list(process.events())
    exit_code = process.wait()

    assert exit_code == 0
    stdout_events = [e for e in events if e["type"] == "ralph_stdout"]
    lines = [e["payload"]["line"] for e in stdout_events]
    assert lines == [
        "iteration: start prd=2",
        "iteration: step 1",
        "iteration: done prd=2",
    ]


def test_ralph_structured_events_land_in_shared_events_jsonl(tmp_path):
    """Post-#6: ralph writes structured events directly to the shared
    .smart-ralph/events.jsonl using supervisor-injected env vars. Stdout is
    still surfaced in-memory as ralph_stdout; structured events are not
    duplicated into the in-memory stream."""
    events_path = tmp_path / ".smart-ralph" / "events.jsonl"
    client = RalphClient(
        ralph_path=FIXTURES / "writes_events.sh",
        cwd=tmp_path,
        run_id="run-shared",
        events_path=events_path,
    )

    process = client.spawn(issue=7)
    events = list(process.events())
    process.wait()

    # stdout still surfaces in-memory
    stdout_lines = [
        e["payload"]["line"] for e in events if e["type"] == "ralph_stdout"
    ]
    assert stdout_lines == ["ralph: iteration begin", "ralph: working", "ralph: done"]

    # Structured events are NOT duplicated into the in-memory stream.
    assert not [e for e in events if e["type"].startswith("ralph_iteration")]

    # They are written directly to the shared events.jsonl with envelope shape.
    entries = [
        json.loads(line)
        for line in events_path.read_text().splitlines() if line.strip()
    ]
    types = [e["type"] for e in entries]
    assert types == ["ralph_iteration_started", "ralph_iteration_ended"]
    for entry in entries:
        assert entry["source"] == "ralph"
        assert entry["run_id"] == "run-shared"
        assert entry["issue"] == 7
        assert entry["schema_version"] == 1


def test_kill_terminates_process_and_stashes_uncommitted(tmp_path):
    _init_repo(tmp_path)
    # create an uncommitted change so stash has something to save
    (tmp_path / "wip.txt").write_text("unfinished")

    client = RalphClient(
        ralph_path=FIXTURES / "long_running.sh",
        cwd=tmp_path,
    )
    process = client.spawn(issue=8)

    # drain events in the background so the pipe doesn't block
    events: list = []
    t = threading.Thread(target=lambda: events.extend(process.events()), daemon=True)
    t.start()
    time.sleep(0.2)  # let ralph actually start and emit first line

    process.kill(issue=8)
    t.join(timeout=2.0)

    # process exited (not still running)
    assert process.wait() != 0

    # stash was created with smart-ralph naming
    stash_list = subprocess.run(
        ["git", "stash", "list"], cwd=tmp_path, capture_output=True, text=True, check=True
    ).stdout
    assert "smart-ralph-issue-8-" in stash_list
    # working tree is clean (stash took wip.txt)
    porcelain = subprocess.run(
        ["git", "status", "--porcelain"], cwd=tmp_path, capture_output=True, text=True, check=True
    ).stdout
    assert porcelain.strip() == ""
