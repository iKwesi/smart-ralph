"""Tests for ralph's structured event emission (issue #6).

Unit tests source lib/ralph-events.sh directly and call emit_event.
Integration tests run the real ralph script with mocked claude/gh/git.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LIB = ROOT / "lib" / "ralph-events.sh"
RALPH = ROOT / "ralph"
FAKE_TOOLS = Path(__file__).parent / "fixtures" / "fake_tools"


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit",
         "--allow-empty", "-q", "-m", "init"],
        cwd=path, check=True,
    )


def _run_ralph(
    repo: Path,
    args: list[str],
    *,
    run_id: str = "run-e2e",
    events_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    events_path = events_path or (repo / ".smart-ralph" / "events.jsonl")
    env = os.environ.copy()
    env["PATH"] = f"{FAKE_TOOLS}:{env['PATH']}"
    env["SMART_RALPH_RUN_ID"] = run_id
    env["SMART_RALPH_EVENTS_PATH"] = str(events_path)
    env["RALPH_ITERATIONS"] = "2"
    return subprocess.run(
        [str(RALPH), *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _emit(
    tmp_path: Path,
    event_type: str,
    issue: str,
    payload_json: str,
    *,
    run_id: str | None = "run-test-1",
    events_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke emit_event via a subshell that sources the helper lib."""
    env = {"PATH": os.environ["PATH"]}
    if run_id is not None:
        env["SMART_RALPH_RUN_ID"] = run_id
    if events_path is None:
        events_path = tmp_path / ".smart-ralph" / "events.jsonl"
    env["SMART_RALPH_EVENTS_PATH"] = str(events_path)

    script = (
        f"source {LIB} && "
        f"emit_event {event_type} {issue!r} {payload_json!r}"
    )
    return subprocess.run(
        ["bash", "-c", script],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )


# ── Slice 1: envelope shape ────────────────────────────────

def test_emit_event_writes_envelope_with_ralph_source(tmp_path):
    result = _emit(
        tmp_path,
        "ralph_issue_started",
        "42",
        '{"title":"do the thing"}',
    )
    assert result.returncode == 0, result.stderr

    events_path = tmp_path / ".smart-ralph" / "events.jsonl"
    lines = events_path.read_text().splitlines()
    assert len(lines) == 1

    evt = json.loads(lines[0])
    assert evt["schema_version"] == 1
    assert evt["run_id"] == "run-test-1"
    assert evt["type"] == "ralph_issue_started"
    assert evt["source"] == "ralph"
    assert evt["issue"] == 42
    assert evt["payload"] == {"title": "do the thing"}
    assert evt["ts"].endswith("Z")


# ── Slice 2: no-op without env ────────────────────────────

def test_emit_event_is_noop_when_run_id_unset(tmp_path):
    events_path = tmp_path / ".smart-ralph" / "events.jsonl"
    result = _emit(
        tmp_path,
        "ralph_issue_started",
        "42",
        "{}",
        run_id=None,
        events_path=events_path,
    )
    assert result.returncode == 0, result.stderr
    assert not events_path.exists()
    assert not events_path.parent.exists()


# ── Slice 3: oversized payload → sidecar blob ─────────────

def test_oversized_payload_written_to_blob_sidecar(tmp_path):
    big_text = "x" * 5000  # forces the envelope past 4KB
    payload_json = json.dumps({"log": big_text})

    result = _emit(
        tmp_path,
        "ralph_error",
        "9",
        payload_json,
    )
    assert result.returncode == 0, result.stderr

    events_path = tmp_path / ".smart-ralph" / "events.jsonl"
    lines = events_path.read_text().splitlines()
    assert len(lines) == 1

    # Envelope line itself stays ≤4KB (including newline).
    assert len(lines[0]) + 1 <= 4096

    evt = json.loads(lines[0])
    assert evt["type"] == "ralph_error"
    assert evt["source"] == "ralph"
    # Payload references a sidecar blob instead of carrying the data inline.
    assert "blob_ref" in evt["payload"]
    assert evt["payload"].get("oversized") is True

    blob_rel = evt["payload"]["blob_ref"]
    # blob_ref is relative to events_root (the events.jsonl parent), so
    # readers can resolve it without needing to know ralph's cwd.
    events_root = (tmp_path / ".smart-ralph").resolve()
    blob_path = events_root / blob_rel
    assert blob_path.exists(), f"blob not at {blob_path}"
    assert blob_rel.startswith("blobs/run-test-1/"), blob_rel
    # Full original payload recoverable from the blob.
    recovered = json.loads(blob_path.read_text())
    assert recovered == {"log": big_text}


def test_multibyte_payload_offloaded_when_bytes_exceed_4kb(tmp_path):
    """Char count can understate size for multi-byte UTF-8 content.
    The guard must count bytes so the atomicity window is never exceeded."""
    # 1500 em-dashes = 1500 chars, 4500 UTF-8 bytes (3 per char).
    # Well under 4096 chars, well over 4096 bytes.
    payload_json = json.dumps({"log": "\u2014" * 1500}, ensure_ascii=False)
    assert len(payload_json) < 4000  # char count below threshold
    assert len(payload_json.encode("utf-8")) > 4096  # byte count above

    result = _emit(
        tmp_path,
        "ralph_error",
        "9",
        payload_json,
    )
    assert result.returncode == 0, result.stderr

    events_path = tmp_path / ".smart-ralph" / "events.jsonl"
    line = events_path.read_text().splitlines()[0]

    # The emitted envelope must fit in the atomicity window by BYTES.
    assert len(line.encode("utf-8")) + 1 <= 4096, (
        f"envelope line is {len(line.encode('utf-8')) + 1} bytes"
    )

    evt = json.loads(line)
    assert "blob_ref" in evt["payload"]
    assert evt["payload"].get("oversized") is True


def test_non_integer_issue_coerced_to_null(tmp_path):
    """Guard against malformed issue values reaching jq --argjson —
    they must be coerced to null, not parsed as raw JSON."""
    result = _emit(
        tmp_path,
        "ralph_issue_started",
        "not-a-number",
        "{}",
    )
    assert result.returncode == 0, result.stderr

    events_path = tmp_path / ".smart-ralph" / "events.jsonl"
    evt = json.loads(events_path.read_text().splitlines()[0])
    assert evt["issue"] is None


def test_missing_payload_defaults_to_empty_object(tmp_path):
    """emit_event called with no payload argument must default to {}."""
    events_path = tmp_path / ".smart-ralph" / "events.jsonl"
    env = {
        "PATH": os.environ["PATH"],
        "SMART_RALPH_RUN_ID": "run-default",
        "SMART_RALPH_EVENTS_PATH": str(events_path),
    }
    script = f"source {LIB} && emit_event ralph_issue_started 7"
    result = subprocess.run(
        ["bash", "-c", script],
        env=env, cwd=tmp_path, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr

    evt = json.loads(events_path.read_text().splitlines()[0])
    assert evt["payload"] == {}


# ── Slice 5: ralph emits issue + iteration + exit ─────────

def _read_events(events_path: Path) -> list[dict]:
    if not events_path.exists():
        return []
    out = []
    for line in events_path.read_text().splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def test_ralph_run_emits_issue_iteration_and_exit_events(tmp_path):
    _init_repo(tmp_path)
    events_path = tmp_path / ".smart-ralph" / "events.jsonl"

    result = _run_ralph(tmp_path, ["run", "42", "1"], events_path=events_path)
    assert result.returncode == 0, (
        f"ralph failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    events = _read_events(events_path)
    types = [e["type"] for e in events]

    assert "ralph_issue_started" in types
    assert "ralph_iteration_started" in types
    assert "ralph_iteration_ended" in types
    assert "ralph_issue_ended" in types
    assert "ralph_exit" in types

    # Success path must close the issue with outcome=complete (symmetric
    # with the iterations-exhausted path).
    issue_ended = next(e for e in events if e["type"] == "ralph_issue_ended")
    assert issue_ended["payload"]["outcome"] == "complete"
    assert issue_ended["payload"]["iterations"] == 1

    # Every ralph-emitted event has source: "ralph" and matching run_id.
    for evt in events:
        assert evt["source"] == "ralph"
        assert evt["run_id"] == "run-e2e"
        assert evt["schema_version"] == 1

    # ralph_issue_started should reference the issue number
    started = next(e for e in events if e["type"] == "ralph_issue_started")
    assert started["issue"] == 42

    # The iteration_started event that fired first carries iteration 1
    iter_started = next(e for e in events if e["type"] == "ralph_iteration_started")
    assert iter_started["payload"]["iteration"] == 1

    # ralph_exit should be last and carry exit_code 0 (claude emitted COMPLETE)
    assert types[-1] == "ralph_exit"
    assert events[-1]["payload"]["exit_code"] == 0


# ── Slice 6: state_transition + error events ──────────────

def _source_ralph_and_run(
    repo: Path,
    snippet: str,
    *,
    run_id: str = "run-state",
) -> subprocess.CompletedProcess[str]:
    """Source ralph in a subshell so ralph's functions are callable directly."""
    events_path = repo / ".smart-ralph" / "events.jsonl"
    env = os.environ.copy()
    env["PATH"] = f"{FAKE_TOOLS}:{env['PATH']}"
    env["SMART_RALPH_RUN_ID"] = run_id
    env["SMART_RALPH_EVENTS_PATH"] = str(events_path)
    # Sourcing ralph without args hits the usage branch; that's harmless.
    script = f"source '{RALPH}' >/dev/null 2>&1; set -e; {snippet}"
    return subprocess.run(
        ["bash", "-c", script],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_set_state_emits_ralph_state_transition(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / ".ralph").mkdir()
    (tmp_path / ".ralph" / "state.json").write_text(
        '{"issues":{"42":"pending"}}'
    )

    result = _source_ralph_and_run(
        tmp_path,
        "set_state 42 in_progress; set_state 42 complete",
    )
    assert result.returncode == 0, result.stderr

    events = _read_events(tmp_path / ".smart-ralph" / "events.jsonl")
    transitions = [e for e in events if e["type"] == "ralph_state_transition"]
    assert len(transitions) == 2

    assert transitions[0]["issue"] == 42
    assert transitions[0]["source"] == "ralph"
    assert transitions[0]["payload"] == {"from": "pending", "to": "in_progress"}
    assert transitions[1]["payload"] == {
        "from": "in_progress", "to": "complete"
    }


def test_ralph_run_emits_ralph_error_on_iteration_exhaustion(tmp_path):
    """When claude never emits COMPLETE, ralph must mark the failure as
    a ralph_error so the supervisor can react."""
    _init_repo(tmp_path)

    # Override fake claude with one that never emits COMPLETE.
    shim_dir = tmp_path / "shims"
    shim_dir.mkdir()
    claude_shim = shim_dir / "claude"
    claude_shim.write_text(
        '#!/usr/bin/env bash\n'
        'printf \'%s\\n\' \'{"type":"result","result":"not done yet"}\'\n'
    )
    claude_shim.chmod(0o755)
    # Copy fake gh alongside
    import shutil as _shutil
    _shutil.copy(FAKE_TOOLS / "gh", shim_dir / "gh")
    (shim_dir / "gh").chmod(0o755)

    events_path = tmp_path / ".smart-ralph" / "events.jsonl"
    env = os.environ.copy()
    env["PATH"] = f"{shim_dir}:{env['PATH']}"
    env["SMART_RALPH_RUN_ID"] = "run-err"
    env["SMART_RALPH_EVENTS_PATH"] = str(events_path)
    env["RALPH_ITERATIONS"] = "1"

    result = subprocess.run(
        [str(RALPH), "run", "9", "1"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    # ralph exits 1 when iterations are exhausted without COMPLETE
    assert result.returncode != 0, (
        f"expected failure, got success. stdout:\n{result.stdout}"
    )

    events = _read_events(events_path)
    errors = [e for e in events if e["type"] == "ralph_error"]
    assert len(errors) >= 1, (
        f"expected ralph_error event. events seen: {[e['type'] for e in events]}"
    )
    err_evt = errors[0]
    assert err_evt["source"] == "ralph"
    assert err_evt["issue"] == 9
    assert "iterations_exhausted" in err_evt["payload"].get("reason", "")

    # ralph_exit must carry the non-zero exit code so supervisor readers
    # can distinguish failure from success without re-deriving it.
    exit_events = [e for e in events if e["type"] == "ralph_exit"]
    assert len(exit_events) == 1
    assert exit_events[0]["payload"]["exit_code"] != 0


# ── Slice 7: merge events ─────────────────────────────────

def test_merge_issue_success_emits_attempted_and_succeeded(tmp_path):
    _init_repo(tmp_path)

    result = _source_ralph_and_run(
        tmp_path,
        "merge_issue 7",
        run_id="run-merge-ok",
    )
    # merge_issue may return non-zero because `gh issue close` path is noisy,
    # but the events should still have been emitted.
    events = _read_events(tmp_path / ".smart-ralph" / "events.jsonl")
    types = [e["type"] for e in events]

    assert "ralph_merge_attempted" in types, (
        f"attempt missing. events: {types}. stderr:\n{result.stderr}"
    )
    assert "ralph_merge_succeeded" in types, (
        f"success missing. events: {types}. stderr:\n{result.stderr}"
    )

    attempted = next(e for e in events if e["type"] == "ralph_merge_attempted")
    assert attempted["issue"] == 7
    assert attempted["source"] == "ralph"
    assert attempted["payload"].get("pr") == 99

    succeeded = next(e for e in events if e["type"] == "ralph_merge_succeeded")
    assert succeeded["issue"] == 7
    assert succeeded["payload"].get("pr") == 99


def test_merge_issue_failure_emits_merge_failed(tmp_path):
    _init_repo(tmp_path)
    events_path = tmp_path / ".smart-ralph" / "events.jsonl"
    env = os.environ.copy()
    env["PATH"] = f"{FAKE_TOOLS}:{env['PATH']}"
    env["SMART_RALPH_RUN_ID"] = "run-merge-bad"
    env["SMART_RALPH_EVENTS_PATH"] = str(events_path)
    env["FAKE_GH_PR_STATE"] = "OPEN"  # simulate merge didn't actually happen

    script = f"source '{RALPH}' >/dev/null 2>&1; merge_issue 7 || true"
    subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    events = _read_events(events_path)
    types = [e["type"] for e in events]
    assert "ralph_merge_attempted" in types
    assert "ralph_merge_failed" in types
    failed = next(e for e in events if e["type"] == "ralph_merge_failed")
    assert failed["issue"] == 7
    assert failed["payload"].get("pr") == 99
    assert failed["payload"].get("state") == "OPEN"


def test_merge_issue_no_pr_emits_merge_failed(tmp_path):
    _init_repo(tmp_path)
    events_path = tmp_path / ".smart-ralph" / "events.jsonl"
    env = os.environ.copy()
    env["PATH"] = f"{FAKE_TOOLS}:{env['PATH']}"
    env["SMART_RALPH_RUN_ID"] = "run-merge-nopr"
    env["SMART_RALPH_EVENTS_PATH"] = str(events_path)
    env["FAKE_GH_PR_NONE"] = "1"  # simulate no open PR

    script = f"source '{RALPH}' >/dev/null 2>&1; merge_issue 7 || true"
    subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    events = _read_events(events_path)
    types = [e["type"] for e in events]
    assert "ralph_merge_failed" in types
    failed = next(e for e in events if e["type"] == "ralph_merge_failed")
    assert failed["issue"] == 7
    assert failed["payload"].get("reason") == "no_pr_found"
