"""Tests for DiagnosticRouter (issue #8).

The router takes an Anomaly + supervisor context, invokes a Provider
headlessly, parses the <diagnosis>{...}</diagnosis> XML block, and
returns a Decision. This slice is observe-only — no fix application.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from smart_ralph.anomaly import Anomaly
from smart_ralph.eventlog import EventLog
from smart_ralph.router import (
    SKIP_SKILL_CHECK,
    DiagnosticRouter,
    SkillVersionError,
)


class FakeProvider:
    """In-memory provider stub. Records every prompt it receives and
    replays canned stdout strings in order. Tests use it to drive the
    router without touching the real `claude` CLI."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    def run_headless(self, prompt: str, *, allowed_tools: list[str]) -> str:
        self.prompts.append(prompt)
        if not self._responses:
            raise AssertionError("FakeProvider exhausted responses")
        return self._responses.pop(0)


_VALID_DIAGNOSIS = (
    '<diagnosis>'
    '{"scope":"orchestrator",'
    '"fix_type":"state_patch",'
    '"action":{"path":".ralph/state.json","key":"foo","value":"bar"},'
    '"confidence":"high",'
    '"summary":"stale state on issue 7",'
    '"evidence":{"exit_code":1}}'
    '</diagnosis>'
)


def _ctx() -> dict[str, Any]:
    return {
        "issue": 7,
        "state_snapshot": {},
        "log_tail": [],
        "iterations": 0,
    }


def _anomaly() -> Anomaly:
    return Anomaly(
        rule="ralph_nonzero_exit",
        issue=7,
        evidence={"exit_code": 1, "log_tail": ["err"]},
    )


# ── Slice 1: tracer ────────────────────────────────────────

def test_router_returns_parsed_decision_from_canned_diagnosis():
    provider = FakeProvider(responses=[_VALID_DIAGNOSIS])
    router = DiagnosticRouter(provider=provider)

    decision = router.route(_anomaly(), context=_ctx())

    assert decision.scope == "orchestrator"
    assert decision.fix_type == "state_patch"
    assert decision.action == {
        "path": ".ralph/state.json", "key": "foo", "value": "bar",
    }
    assert decision.confidence == "high"
    assert decision.summary == "stale state on issue 7"
    assert decision.evidence == {"exit_code": 1}
    assert decision.needs_human is False
    # Provider was called exactly once with a prompt that referenced the rule.
    assert len(provider.prompts) == 1
    assert "ralph_nonzero_exit" in provider.prompts[0]


# ── Slice 2: lifecycle event emission ──────────────────────

def _read_jsonl(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_router_logs_diagnosis_started_and_completed(tmp_path):
    provider = FakeProvider(responses=[_VALID_DIAGNOSIS])
    log = EventLog(tmp_path / "events.jsonl", run_id="run-r1")
    router = DiagnosticRouter(provider=provider, event_log=log)

    router.route(_anomaly(), context=_ctx())

    entries = _read_jsonl(tmp_path / "events.jsonl")
    types = [e["type"] for e in entries]
    assert types == ["diagnosis_started", "diagnosis_completed"]

    started = entries[0]
    assert started["source"] == "skill:diagnose-ralph"
    assert started["issue"] == 7
    assert started["payload"]["rule"] == "ralph_nonzero_exit"

    completed = entries[1]
    assert completed["source"] == "skill:diagnose-ralph"
    assert completed["issue"] == 7
    assert completed["payload"]["scope"] == "orchestrator"
    assert completed["payload"]["fix_type"] == "state_patch"
    assert completed["payload"]["confidence"] == "high"


# ── Slice 3: corrective retry on malformed output ──────────

def test_malformed_first_response_triggers_one_corrective_retry(tmp_path):
    provider = FakeProvider(responses=["nope, no diagnosis here", _VALID_DIAGNOSIS])
    log = EventLog(tmp_path / "events.jsonl", run_id="run-retry")
    router = DiagnosticRouter(provider=provider, event_log=log)

    decision = router.route(_anomaly(), context=_ctx())

    # The router called the provider twice — once with the original prompt,
    # once with a corrective prompt that referenced the first malformed
    # output and asked for a valid <diagnosis> block.
    assert len(provider.prompts) == 2
    retry_prompt = provider.prompts[1]
    assert "<diagnosis>" in retry_prompt
    assert "nope, no diagnosis here" in retry_prompt or "previous" in retry_prompt.lower()

    # The successful retry produces a normal decision, not needs_human.
    assert decision.scope == "orchestrator"
    assert decision.needs_human is False

    # diagnosis_started fires once; diagnosis_completed fires once.
    types = [e["type"] for e in _read_jsonl(tmp_path / "events.jsonl")]
    assert types.count("diagnosis_started") == 1
    assert types.count("diagnosis_completed") == 1


# ── Slice 4: two malformed → needs_human + diagnosis_failed ──

def test_two_malformed_responses_escalate_to_needs_human(tmp_path):
    """If both the original call and its corrective retry return malformed
    output, the router emits diagnosis_failed and returns a Decision with
    needs_human=True. No third provider call is ever made."""
    provider = FakeProvider(responses=["junk one", "still junk"])
    log = EventLog(tmp_path / "events.jsonl", run_id="run-bad")
    router = DiagnosticRouter(provider=provider, event_log=log)

    decision = router.route(_anomaly(), context=_ctx())

    assert decision.needs_human is True
    assert decision.scope == "unknown"
    # Provider called exactly twice — never a third time.
    assert len(provider.prompts) == 2

    types = [e["type"] for e in _read_jsonl(tmp_path / "events.jsonl")]
    assert "diagnosis_started" in types
    assert "diagnosis_failed" in types
    # diagnosis_completed must NOT fire when escalating.
    assert "diagnosis_completed" not in types

    failed = next(
        e for e in _read_jsonl(tmp_path / "events.jsonl")
        if e["type"] == "diagnosis_failed"
    )
    assert failed["payload"]["reason"] == "malformed_output"
    assert failed["payload"]["attempts"] == 2


# ── Slice 5: skill version mismatch refuses to run ─────────

def _write_skill(path, *, version: int) -> None:
    path.write_text(
        f"---\n"
        f"version: {version}\n"
        f"description: programmatic-use only\n"
        f"---\n"
        f"# diagnose-ralph body\n"
    )


def test_router_refuses_to_run_on_skill_version_mismatch(tmp_path):
    skill_md = tmp_path / "SKILL.md"
    _write_skill(skill_md, version=99)  # supervisor expects v1

    with pytest.raises(SkillVersionError) as exc:
        DiagnosticRouter(
            provider=FakeProvider([]),
            skill_path=skill_md,
        )
    msg = str(exc.value).lower()
    assert "version" in msg
    assert "99" in msg


def test_router_accepts_matching_skill_version(tmp_path):
    skill_md = tmp_path / "SKILL.md"
    _write_skill(skill_md, version=1)

    DiagnosticRouter(
        provider=FakeProvider([_VALID_DIAGNOSIS]),
        skill_path=skill_md,
    )  # constructs without raising


def test_router_default_skill_path_runs_version_check_against_canonical(monkeypatch):
    """Constructing the router without skill_path must not silently skip
    the check. It loads the canonical project SKILL.md and validates
    against EXPECTED_SKILL_VERSION."""
    monkeypatch.setattr("smart_ralph.router.EXPECTED_SKILL_VERSION", 999)

    with pytest.raises(SkillVersionError) as exc:
        DiagnosticRouter(provider=FakeProvider([]))

    msg = str(exc.value)
    assert "999" in msg  # the bumped expected version
    assert "diagnose-ralph" in msg
    assert "SKILL.md" in msg


# ── Parser robustness: arrays and multi-block ──────────────

def test_parser_returns_needs_human_when_diagnosis_body_is_json_array():
    """If the model emits a JSON array (or any non-object), the parser
    must not crash. Two malformed responses → needs_human."""
    arr = '<diagnosis>[1,2,3]</diagnosis>'
    provider = FakeProvider(responses=[arr, arr])
    router = DiagnosticRouter(provider=provider, skill_path=SKIP_SKILL_CHECK)

    decision = router.route(_anomaly(), context=_ctx())

    assert decision.needs_human is True
    assert decision.scope == "unknown"


def test_parser_picks_first_when_multiple_diagnosis_blocks_present():
    """The SKILL.md hard-rule says exactly one block; if the model emits
    more than one, the non-greedy regex picks the first. Pin that
    tie-break so future parser changes can't silently shift behaviour."""
    two_blocks = (
        '<diagnosis>{"scope":"orchestrator","fix_type":"restart",'
        '"action":{},"confidence":"high","summary":"first","evidence":{}}'
        '</diagnosis>\n'
        '<diagnosis>{"scope":"product","fix_type":null,'
        '"action":null,"confidence":"low","summary":"second","evidence":{}}'
        '</diagnosis>'
    )
    provider = FakeProvider(responses=[two_blocks])
    router = DiagnosticRouter(provider=provider, skill_path=SKIP_SKILL_CHECK)

    decision = router.route(_anomaly(), context=_ctx())

    assert decision.summary == "first"
    assert decision.scope == "orchestrator"
