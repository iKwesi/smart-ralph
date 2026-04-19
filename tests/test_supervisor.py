import json
import os
import subprocess
from pathlib import Path

import pytest

from smart_ralph.supervisor import ConcurrentRunError, HealthCheckError, Supervisor

FIXTURES = Path(__file__).parent / "fixtures" / "fake_ralph"


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    # smart-ralph's own meta dirs must be gitignored so stash-on-kill
    # doesn't sweep up events.jsonl itself — matches what `smart-ralph init`
    # writes into a real target repo.
    (path / ".gitignore").write_text(".smart-ralph/\n.ralph/\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit",
         "-q", "-m", "init"],
        cwd=path, check=True,
    )


def _all_tools_present() -> list[str]:
    # Supervisor health check will ask for these; the test environment has them
    # under /usr/bin, /opt/homebrew/bin, etc. — use a non-empty subset we know exists.
    return ["git"]


def test_lockfile_is_created_during_run_and_removed_on_exit(tmp_path):
    _init_repo(tmp_path)
    supervisor = Supervisor(
        ralph_path=FIXTURES / "checks_lockfile.sh",
        cwd=tmp_path,
        required_tools=_all_tools_present(),
    )

    exit_code, events = supervisor.run(prd="9")

    assert exit_code == 0
    lock_path = tmp_path / ".smart-ralph" / "run.lock"
    assert not lock_path.exists(), "lockfile should be removed after run"

    stdout_lines = [
        e["payload"]["line"]
        for e in events
        if e.get("type") == "ralph_stdout"
    ]
    # ralph stub observed the lockfile and wrote its PID contents
    assert len(stdout_lines) == 1
    line = stdout_lines[0]
    assert line.startswith("lockfile-present:"), line
    pid_in_lock = int(line.split(":", 1)[1])
    assert pid_in_lock == os.getpid()


def test_second_run_errors_when_lockfile_exists(tmp_path):
    _init_repo(tmp_path)
    # pretend another smart-ralph is running — a lockfile with a live PID
    meta = tmp_path / ".smart-ralph"
    meta.mkdir()
    # use PID 1 (init) — guaranteed alive on any POSIX system
    (meta / "run.lock").write_text("1")

    supervisor = Supervisor(
        ralph_path=FIXTURES / "checks_lockfile.sh",
        cwd=tmp_path,
        required_tools=_all_tools_present(),
    )

    with pytest.raises(ConcurrentRunError) as exc:
        supervisor.run(prd="10")
    assert "already running" in str(exc.value).lower()

    # the other run's lockfile is NOT clobbered
    assert (meta / "run.lock").read_text() == "1"


def test_missing_required_tool_aborts_before_spawn(tmp_path):
    _init_repo(tmp_path)
    supervisor = Supervisor(
        ralph_path=FIXTURES / "checks_lockfile.sh",
        cwd=tmp_path,
        required_tools=["git", "definitely-not-a-real-tool-xyzzy"],
    )

    with pytest.raises(HealthCheckError) as exc:
        supervisor.run(prd="11")

    msg = str(exc.value)
    assert "definitely-not-a-real-tool-xyzzy" in msg
    assert "not found" in msg.lower() or "missing" in msg.lower()

    # supervisor must NOT have created the lockfile (aborted before spawn)
    assert not (tmp_path / ".smart-ralph" / "run.lock").exists()


def test_lifecycle_events_written_in_order(tmp_path):
    _init_repo(tmp_path)
    supervisor = Supervisor(
        ralph_path=FIXTURES / "echo_stdout.sh",
        cwd=tmp_path,
        required_tools=_all_tools_present(),
    )

    supervisor.run(prd="12")

    events_path = tmp_path / ".smart-ralph" / "events.jsonl"
    assert events_path.exists()
    lines = events_path.read_text().splitlines()
    entries = [json.loads(line) for line in lines]
    types = [e["type"] for e in entries]

    lifecycle = [t for t in types if t in {
        "run_started", "ralph_spawned", "ralph_exited", "run_ended",
    }]
    assert lifecycle == [
        "run_started", "ralph_spawned", "ralph_exited", "run_ended",
    ]

    run_ids = {e["run_id"] for e in entries}
    assert len(run_ids) == 1, "all events in one run share a run_id"

    spawned = next(e for e in entries if e["type"] == "ralph_spawned")
    assert isinstance(spawned["payload"].get("pid"), int)
    exited = next(e for e in entries if e["type"] == "ralph_exited")
    assert exited["payload"]["exit_code"] == 0


def test_sigint_shuts_down_cleanly_and_writes_run_ended(tmp_path):
    import signal
    import sys
    import time as _time

    _init_repo(tmp_path)

    helper = Path(__file__).parent / "_helpers" / "run_supervisor.py"
    proc = subprocess.Popen(
        [sys.executable, str(helper), str(tmp_path),
         str(FIXTURES / "long_running.sh"), "13"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # wait for ralph to be up and lockfile to exist
    lock = tmp_path / ".smart-ralph" / "run.lock"
    deadline = _time.time() + 5.0
    while _time.time() < deadline and not lock.exists():
        _time.sleep(0.05)
    assert lock.exists(), "supervisor never created the lockfile"

    proc.send_signal(signal.SIGINT)
    try:
        exit_code = proc.wait(timeout=10.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise
    out, err = proc.communicate()

    assert exit_code == 130, (
        f"expected 130, got {exit_code}\n"
        f"stdout={out!r}\nstderr={err!r}"
    )
    assert not lock.exists(), "lockfile should be removed on clean shutdown"

    events_path = tmp_path / ".smart-ralph" / "events.jsonl"
    assert events_path.exists()
    types = [
        json.loads(line)["type"]
        for line in events_path.read_text().splitlines()
    ]
    # run_ended is always the final lifecycle event
    lifecycle = [
        t for t in types
        if t in {"run_started", "ralph_spawned", "ralph_exited", "run_ended"}
    ]
    assert lifecycle[0] == "run_started"
    assert lifecycle[-1] == "run_ended"
