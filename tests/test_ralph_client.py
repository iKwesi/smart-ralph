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


def test_spawn_yields_stdout_lines_as_events(tmp_path):
    client = RalphClient(
        ralph_path=FIXTURES / "echo_stdout.sh",
        cwd=tmp_path,
    )

    process = client.spawn(prd="2")
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


def test_events_merges_ralph_events_jsonl(tmp_path):
    client = RalphClient(
        ralph_path=FIXTURES / "writes_events.sh",
        cwd=tmp_path,
    )

    process = client.spawn(prd="7")
    events = list(process.events())
    process.wait()

    # stdout events present
    stdout_lines = [
        e["payload"]["line"] for e in events if e["type"] == "ralph_stdout"
    ]
    assert stdout_lines == ["ralph: iteration begin", "ralph: working", "ralph: done"]

    # ralph's own structured events are merged in as ralph_event
    ralph_events = [e for e in events if e["type"] == "ralph_event"]
    types = [e["payload"]["type"] for e in ralph_events]
    assert types == ["ralph_iteration_started", "ralph_iteration_ended"]
    assert ralph_events[0]["payload"]["payload"]["prd"] == "7"
    assert ralph_events[1]["payload"]["payload"]["ok"] is True

    # sanity: .ralph/events.jsonl actually got written
    written = (tmp_path / ".ralph" / "events.jsonl").read_text().splitlines()
    assert len(written) == 2
    assert json.loads(written[0])["type"] == "ralph_iteration_started"


def test_kill_terminates_process_and_stashes_uncommitted(tmp_path):
    _init_repo(tmp_path)
    # create an uncommitted change so stash has something to save
    (tmp_path / "wip.txt").write_text("unfinished")

    client = RalphClient(
        ralph_path=FIXTURES / "long_running.sh",
        cwd=tmp_path,
    )
    process = client.spawn(prd="8")

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
