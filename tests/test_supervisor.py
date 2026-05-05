import json
import os
import subprocess
from pathlib import Path

import pytest

from smart_ralph.anomaly import Anomaly, AnomalyDetector
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

    exit_code, events = supervisor.run(issue=9)

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
        supervisor.run(issue=10)
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
        supervisor.run(issue=11)

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

    supervisor.run(issue=12)

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

    # run_started payload does NOT duplicate the envelope's issue field —
    # issue is already a top-level envelope key.
    started = next(e for e in entries if e["type"] == "run_started")
    assert started["issue"] == 12
    assert "issue" not in started["payload"]


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


def test_retention_prunes_old_runs_on_startup(tmp_path):
    _init_repo(tmp_path)
    events_path = tmp_path / ".smart-ralph" / "events.jsonl"
    events_path.parent.mkdir()

    # simulate 3 historical runs already on disk
    lines = []
    for rid in ["run-old-1", "run-old-2", "run-old-3"]:
        lines.append(json.dumps({
            "schema_version": 1, "ts": "2026-01-01T00:00:00.000Z",
            "run_id": rid, "type": "run_started", "source": "supervisor",
            "issue": None, "payload": {},
        }))
        lines.append(json.dumps({
            "schema_version": 1, "ts": "2026-01-01T00:00:01.000Z",
            "run_id": rid, "type": "run_ended", "source": "supervisor",
            "issue": None, "payload": {},
        }))
    events_path.write_text("\n".join(lines) + "\n")

    supervisor = Supervisor(
        ralph_path=FIXTURES / "echo_stdout.sh",
        cwd=tmp_path,
        required_tools=_all_tools_present(),
        retention_runs=2,
    )

    supervisor.run(issue=16)

    entries = [json.loads(line) for line in events_path.read_text().splitlines()]
    run_ids = list({e["run_id"]: None for e in entries}.keys())
    # run-old-1 pruned; run-old-2, run-old-3, and the new run remain
    # (prune happens on startup BEFORE the new run_id is appended, so the
    # new run_id is in addition to whatever kept=2 retained).
    assert "run-old-1" not in run_ids
    assert "run-old-2" in run_ids
    assert "run-old-3" in run_ids
    # the new supervisor run's events are present
    current_types = [e["type"] for e in entries if e["run_id"] not in {
        "run-old-1", "run-old-2", "run-old-3"
    }]
    assert "run_started" in current_types
    assert "run_ended" in current_types


def test_supervisor_and_ralph_share_events_jsonl(tmp_path):
    """Supervisor and the patched ralph must share .smart-ralph/events.jsonl.
    Both sources' events must be present, each with its own `source` tag and
    matching run_id."""
    _init_repo(tmp_path)
    supervisor = Supervisor(
        ralph_path=FIXTURES / "writes_events.sh",
        cwd=tmp_path,
        required_tools=_all_tools_present(),
    )

    supervisor.run(issue=55)

    events_path = tmp_path / ".smart-ralph" / "events.jsonl"
    assert events_path.exists()
    entries = [json.loads(line) for line in events_path.read_text().splitlines()]

    sources = {e["source"] for e in entries}
    assert "supervisor" in sources
    assert "ralph" in sources

    # All events must carry the same run_id (supervisor-assigned).
    run_ids = {e["run_id"] for e in entries}
    assert len(run_ids) == 1, (
        f"supervisor and ralph events must share run_id, got {run_ids}"
    )

    # Ralph-source events carry issue=55 (propagated from the spawn call).
    ralph_events = [e for e in entries if e["source"] == "ralph"]
    assert ralph_events, "expected at least one ralph-source event"
    ralph_types = [e["type"] for e in ralph_events]
    assert "ralph_iteration_started" in ralph_types
    assert "ralph_iteration_ended" in ralph_types


def test_nonzero_ralph_exit_writes_anomaly_detected_event(tmp_path):
    """Supervisor must feed events through the AnomalyDetector and write
    any returned anomalies as anomaly_detected events on events.jsonl with
    matching run_id and source: supervisor."""
    _init_repo(tmp_path)
    supervisor = Supervisor(
        ralph_path=FIXTURES / "exits_nonzero.sh",
        cwd=tmp_path,
        required_tools=_all_tools_present(),
    )

    exit_code, _ = supervisor.run(issue=21)
    assert exit_code == 1

    events_path = tmp_path / ".smart-ralph" / "events.jsonl"
    entries = [json.loads(line) for line in events_path.read_text().splitlines()]

    anomaly_events = [e for e in entries if e["type"] == "anomaly_detected"]
    assert len(anomaly_events) == 1, (
        f"expected one anomaly_detected, got types={[e['type'] for e in entries]}"
    )
    evt = anomaly_events[0]
    assert evt["source"] == "supervisor"
    assert evt["issue"] == 21
    assert evt["payload"]["rule"] == "ralph_nonzero_exit"
    assert evt["payload"]["evidence"]["exit_code"] == 1
    # log_tail came from the stdout the supervisor saw before exit.
    log_tail = evt["payload"]["evidence"]["log_tail"]
    assert "ralph: starting" in log_tail
    assert "ralph: hitting an error" in log_tail

    # All events share the supervisor's run_id.
    run_ids = {e["run_id"] for e in entries}
    assert len(run_ids) == 1


def test_stdout_stream_anomalies_are_recorded_not_just_at_exit(tmp_path):
    """Anomalies returned while the supervisor is draining ralph's stdout
    must be written to events.jsonl too — not silently dropped because the
    loop only records on ralph_exited."""
    detector = AnomalyDetector()

    def stdout_marker_rule(event, _ctx):
        if event.get("type") != "ralph_stdout":
            return None
        if "BANG" not in event.get("payload", {}).get("line", ""):
            return None
        return Anomaly(
            rule="stdout_marker_seen",
            issue=event.get("issue"),
            evidence={"line": event["payload"]["line"]},
        )

    detector.register(stdout_marker_rule)

    # fixture stub that prints a stdout line containing "BANG", then exits 0
    bang = tmp_path / "bang.sh"
    bang.write_text(
        '#!/usr/bin/env bash\n'
        'echo "BANG goes the issue"\n'
        'echo "ok"\n'
    )
    bang.chmod(0o755)

    _init_repo(tmp_path)
    Supervisor(
        ralph_path=bang,
        cwd=tmp_path,
        required_tools=_all_tools_present(),
        detector=detector,
    ).run(issue=99)

    events_path = tmp_path / ".smart-ralph" / "events.jsonl"
    entries = [json.loads(line) for line in events_path.read_text().splitlines()]
    anomalies = [e for e in entries if e["type"] == "anomaly_detected"]
    assert any(a["payload"]["rule"] == "stdout_marker_seen" for a in anomalies), (
        f"expected stdout_marker_seen anomaly, got {[a['payload']['rule'] for a in anomalies]}"
    )


def test_zero_exit_does_not_write_anomaly_detected(tmp_path):
    _init_repo(tmp_path)
    supervisor = Supervisor(
        ralph_path=FIXTURES / "echo_stdout.sh",
        cwd=tmp_path,
        required_tools=_all_tools_present(),
    )

    supervisor.run(issue=22)

    events_path = tmp_path / ".smart-ralph" / "events.jsonl"
    entries = [json.loads(line) for line in events_path.read_text().splitlines()]
    assert not [e for e in entries if e["type"] == "anomaly_detected"]


def test_kill_exception_on_sigint_is_logged_as_repair_failed(tmp_path):
    """If RalphClient.kill raises during SIGINT, the failure must be audit-logged
    (not silently swallowed) and the run must still exit cleanly."""
    import signal
    import sys
    import time as _time

    _init_repo(tmp_path)

    helper = Path(__file__).parent / "_helpers" / "run_supervisor_broken_kill.py"
    proc = subprocess.Popen(
        [sys.executable, str(helper), str(tmp_path),
         str(FIXTURES / "long_running.sh"), "42"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    lock = tmp_path / ".smart-ralph" / "run.lock"
    deadline = _time.time() + 5.0
    while _time.time() < deadline and not lock.exists():
        _time.sleep(0.05)
    assert lock.exists()

    proc.send_signal(signal.SIGINT)
    exit_code = proc.wait(timeout=10.0)
    assert exit_code == 130

    events = [
        json.loads(line)
        for line in (tmp_path / ".smart-ralph" / "events.jsonl")
        .read_text()
        .splitlines()
    ]
    repair_failed = [e for e in events if e["type"] == "repair_failed"]
    assert len(repair_failed) == 1
    assert repair_failed[0]["payload"]["op"] == "kill_and_stash"
    assert "git binary missing" in repair_failed[0]["payload"]["error"]
    # run_ended still written
    assert events[-1]["type"] == "run_ended"
