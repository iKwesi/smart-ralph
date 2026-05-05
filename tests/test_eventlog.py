import json

from smart_ralph.eventlog import EventLog, SCHEMA_VERSION


def test_append_writes_single_jsonl_line_with_envelope(tmp_path):
    log = EventLog(tmp_path / "events.jsonl", run_id="run-abc")

    log.append(event_type="run_started", source="supervisor", issue=2, payload={"prd": "2"})

    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    assert len(lines) == 1
    evt = json.loads(lines[0])
    assert evt["schema_version"] == SCHEMA_VERSION
    assert evt["run_id"] == "run-abc"
    assert evt["type"] == "run_started"
    assert evt["source"] == "supervisor"
    assert evt["issue"] == 2
    assert evt["payload"] == {"prd": "2"}
    assert "ts" in evt and evt["ts"].endswith("Z")


def test_multiple_appends_preserve_order(tmp_path):
    log = EventLog(tmp_path / "events.jsonl", run_id="run-1")

    log.append(event_type="run_started", source="supervisor", issue=1, payload={})
    log.append(event_type="ralph_spawned", source="supervisor", issue=1, payload={"pid": 42})
    log.append(event_type="ralph_exited", source="supervisor", issue=1, payload={"code": 0})

    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    types = [json.loads(line)["type"] for line in lines]
    assert types == ["run_started", "ralph_spawned", "ralph_exited"]


def test_tail_returns_last_n_parsed_events(tmp_path):
    log = EventLog(tmp_path / "events.jsonl", run_id="run-1")
    for i in range(5):
        log.append(event_type="tick", source="supervisor", issue=None, payload={"i": i})

    result = log.tail(3)

    assert [e["payload"]["i"] for e in result] == [2, 3, 4]
    assert all(e["type"] == "tick" for e in result)


def test_tail_on_empty_log_returns_empty(tmp_path):
    log = EventLog(tmp_path / "events.jsonl", run_id="run-1")
    assert log.tail(5) == []


def test_prune_runs_keeps_only_last_n_runs(tmp_path):
    path = tmp_path / "events.jsonl"
    # write 4 runs, 2 events each, directly into the same file
    for run_id in ["run-a", "run-b", "run-c", "run-d"]:
        log = EventLog(path, run_id=run_id)
        log.append(event_type="run_started", source="supervisor", issue=None, payload={})
        log.append(event_type="run_ended", source="supervisor", issue=None, payload={})

    EventLog(path, run_id="run-e").prune_runs(keep=2)

    lines = path.read_text().splitlines()
    run_ids = [json.loads(line)["run_id"] for line in lines]
    assert set(run_ids) == {"run-c", "run-d"}
    # order preserved within kept runs
    assert run_ids == ["run-c", "run-c", "run-d", "run-d"]


def test_prune_runs_on_missing_file_is_noop(tmp_path):
    EventLog(tmp_path / "events.jsonl", run_id="run-1").prune_runs(keep=50)


# ── Oversized payload offload (issue #7 — anomaly evidence may be huge) ──

def test_oversized_payload_offloaded_to_blob_sidecar(tmp_path):
    """Match the ralph-events.sh contract on the supervisor side: any line
    that would exceed the 4KB PIPE_BUF window writes its payload to a
    sidecar blob and replaces the inline payload with a blob_ref."""
    log = EventLog(tmp_path / "events.jsonl", run_id="run-big")

    big_payload = {"evidence": {"log_tail": ["x" * 200] * 50}}  # ~10KB inline
    log.append(
        event_type="anomaly_detected", source="supervisor",
        issue=7, payload=big_payload,
    )

    line = (tmp_path / "events.jsonl").read_text().splitlines()[0]
    assert len(line.encode("utf-8")) + 1 <= 4096

    evt = json.loads(line)
    assert evt["payload"]["oversized"] is True
    blob_rel = evt["payload"]["blob_ref"]
    assert blob_rel.startswith("blobs/run-big/")

    blob_path = tmp_path / blob_rel
    assert blob_path.exists()
    recovered = json.loads(blob_path.read_text())
    assert recovered == big_payload


def test_small_payload_not_offloaded(tmp_path):
    log = EventLog(tmp_path / "events.jsonl", run_id="run-small")

    log.append(
        event_type="anomaly_detected", source="supervisor",
        issue=7, payload={"rule": "x", "evidence": {"exit_code": 1}},
    )

    evt = json.loads((tmp_path / "events.jsonl").read_text().splitlines()[0])
    assert "blob_ref" not in evt["payload"]
    assert "oversized" not in evt["payload"]
    assert evt["payload"]["evidence"]["exit_code"] == 1
