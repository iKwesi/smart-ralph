"""Tests for AnomalyDetector (issue #7).

The detector observes lifecycle events and returns Anomaly records. Rules
are pluggable; the v1 default rule set fires on ralph non-zero exits
(excluding 42 = rate limit and 130 = SIGINT).
"""
from __future__ import annotations

from smart_ralph.anomaly import AnomalyDetector


def _ralph_exited(exit_code: int, issue: int = 7) -> dict:
    """Build a ralph_exited envelope event the way the supervisor writes it."""
    return {
        "schema_version": 1,
        "ts": "2026-05-04T00:00:00.000Z",
        "run_id": "run-test",
        "type": "ralph_exited",
        "source": "supervisor",
        "issue": issue,
        "payload": {"exit_code": exit_code},
    }


# ── Slice 1: tracer ────────────────────────────────────────

def test_observe_emits_anomaly_for_nonzero_exit():
    detector = AnomalyDetector()

    anomalies = detector.observe(_ralph_exited(exit_code=1))

    assert len(anomalies) == 1
    assert anomalies[0].rule == "ralph_nonzero_exit"


# ── Slice 2: exclude 0, 42, 130 ────────────────────────────

def test_normal_exit_codes_do_not_trigger_anomaly():
    """Exit 0 = success. 42 = rate limit (handled by its own rule path).
    130 = SIGINT (user-initiated, not an anomaly)."""
    detector = AnomalyDetector()

    for code in (0, 42, 130):
        assert detector.observe(_ralph_exited(exit_code=code)) == [], (
            f"exit_code={code} should not trigger ralph_nonzero_exit"
        )


# ── Slice 3: evidence carries exit_code ────────────────────

def test_anomaly_evidence_carries_exit_code():
    detector = AnomalyDetector()

    anomaly = detector.observe(_ralph_exited(exit_code=44))[0]

    assert anomaly.evidence["exit_code"] == 44
    assert anomaly.issue == 7


# ── Slice 4: evidence carries log_tail ─────────────────────

def _ralph_stdout(line: str) -> dict:
    return {
        "schema_version": 1,
        "ts": "2026-05-04T00:00:00.000Z",
        "run_id": "run-test",
        "type": "ralph_stdout",
        "source": "supervisor",
        "issue": 7,
        "payload": {"line": line},
    }


def test_anomaly_evidence_carries_last_100_log_lines():
    """Detector accumulates ralph_stdout events into a bounded buffer.
    On ralph_exited, the nonzero-exit rule snapshots the last 100 lines
    into evidence.log_tail."""
    detector = AnomalyDetector()

    # Feed 150 stdout lines — only the trailing 100 should survive.
    for i in range(150):
        detector.observe(_ralph_stdout(f"line {i}"))

    anomaly = detector.observe(_ralph_exited(exit_code=1))[0]

    assert "log_tail" in anomaly.evidence
    log_tail = anomaly.evidence["log_tail"]
    assert len(log_tail) == 100
    assert log_tail[0] == "line 50"
    assert log_tail[-1] == "line 149"


def test_anomaly_log_tail_is_empty_when_no_stdout_seen():
    detector = AnomalyDetector()

    anomaly = detector.observe(_ralph_exited(exit_code=1))[0]

    assert anomaly.evidence["log_tail"] == []


# ── register(rule): pluggable rule registration ────────────

def test_register_adds_a_custom_rule_that_fires_alongside_defaults():
    """Custom rules registered via register() must be invoked on every
    observe() call alongside the built-in rule set."""
    from smart_ralph.anomaly import Anomaly

    detector = AnomalyDetector()

    def fires_on_run_started(event, _ctx):
        if event.get("type") == "run_started":
            return Anomaly(rule="custom_run_started", issue=event.get("issue"))
        return None

    detector.register(fires_on_run_started)

    event = {"type": "run_started", "issue": 5, "payload": {}}
    anomalies = detector.observe(event)

    rules = {a.rule for a in anomalies}
    assert "custom_run_started" in rules
