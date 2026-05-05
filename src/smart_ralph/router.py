"""Diagnostic routing — invokes the diagnose-ralph skill on an anomaly,
parses the <diagnosis>{...}</diagnosis> XML block returned by Claude, and
yields a Decision the supervisor can act on.

This module is observe-only in the v1 slice that introduces it: it parses
and logs the proposal but never applies any fix. The repair pathway lands
in a follow-up slice.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from smart_ralph.anomaly import Anomaly
from smart_ralph.eventlog import EventLog


class Provider(Protocol):
    """Headless model-runner adapter. v1 ships ClaudeProvider; the Protocol
    exists so v2 can plug in Codex / Gemini / local models without touching
    the router."""

    def run_headless(self, prompt: str, *, allowed_tools: list[str]) -> str: ...


@dataclass(frozen=True)
class Decision:
    """Parsed diagnosis output. needs_human is set by the router when it
    has exhausted retries against malformed provider output, so the
    supervisor doesn't have to re-derive it."""
    scope: str
    fix_type: str | None
    action: dict[str, Any] | None
    confidence: str
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)
    needs_human: bool = False


_DIAGNOSIS_BLOCK = re.compile(r"<diagnosis>(.*?)</diagnosis>", re.DOTALL)

# Allowed tools the supervisor exposes to the diagnose-ralph skill. Mirrors
# the SKILL.md frontmatter; defined here as the source of truth for the
# headless provider invocation.
_ALLOWED_TOOLS = [
    "Read", "Grep", "Glob",
    "Bash(jq:*)", "Bash(git log:*)", "Bash(git status:*)",
    "Bash(git diff:*)", "Bash(cat .ralph/*)",
    "Edit(.ralph/state.json)",
]


_SKILL_SOURCE = "skill:diagnose-ralph"
EXPECTED_SKILL_VERSION = 1

# Canonical on-disk location of the diagnose-ralph SKILL.md, relative to
# the repo root. Used as the default for DiagnosticRouter.skill_path so
# callers cannot accidentally bypass the version check by forgetting to
# pass it.
_DEFAULT_SKILL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / ".claude" / "skills" / "diagnose-ralph" / "SKILL.md"
)


# Sentinel for "skip the skill version check entirely" — distinct from
# default (canonical SKILL.md) and from a user-supplied custom path.
class _SkipSkillCheck:
    pass


SKIP_SKILL_CHECK = _SkipSkillCheck()


class SkillVersionError(RuntimeError):
    """Raised when the on-disk skill's frontmatter version does not match
    the version this code was written against. Forces a deliberate
    contract bump rather than silent drift."""


_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def read_skill_version(skill_path: Path) -> int:
    """Parse the `version:` field out of SKILL.md frontmatter without
    pulling in a YAML dependency. Frontmatter is YAML by convention but
    we only need one integer field."""
    text = skill_path.read_text()
    match = _FRONTMATTER.match(text)
    if match is None:
        raise SkillVersionError(
            f"{skill_path}: missing YAML frontmatter (expected `version:` key)"
        )
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        if key.strip() == "version":
            try:
                return int(value.strip())
            except ValueError as e:
                raise SkillVersionError(
                    f"{skill_path}: `version:` must be an integer, "
                    f"got {value.strip()!r}"
                ) from e
    raise SkillVersionError(
        f"{skill_path}: frontmatter is missing the `version:` key"
    )


_DEFAULT_PATH_SENTINEL = object()


class DiagnosticRouter:
    def __init__(
        self,
        provider: Provider,
        *,
        event_log: EventLog | None = None,
        skill_path: "Path | _SkipSkillCheck | object" = _DEFAULT_PATH_SENTINEL,
    ) -> None:
        # Resolve skill_path policy:
        #   - default sentinel  → canonical project SKILL.md (version-check ON)
        #   - SKIP_SKILL_CHECK  → caller explicitly opts out (e.g., test harness)
        #   - any Path          → caller supplies a specific file
        # Forces the version check to be the default; callers must opt
        # out via SKIP_SKILL_CHECK rather than by silently omitting it.
        resolved: Path | None
        if skill_path is _DEFAULT_PATH_SENTINEL:
            resolved = _DEFAULT_SKILL_PATH if _DEFAULT_SKILL_PATH.exists() else None
        elif isinstance(skill_path, _SkipSkillCheck):
            resolved = None
        else:
            resolved = skill_path  # type: ignore[assignment]

        if resolved is not None:
            actual = read_skill_version(resolved)
            if actual != EXPECTED_SKILL_VERSION:
                raise SkillVersionError(
                    f"diagnose-ralph SKILL.md version {actual} does not "
                    f"match supervisor expected version "
                    f"{EXPECTED_SKILL_VERSION} ({resolved})"
                )
        self._provider = provider
        self._event_log = event_log

    def route(self, anomaly: Anomaly, *, context: dict[str, Any]) -> Decision:
        self._emit("diagnosis_started", anomaly.issue, {"rule": anomaly.rule})
        prompt = self._build_prompt(anomaly, context)
        output = self._provider.run_headless(prompt, allowed_tools=list(_ALLOWED_TOOLS))

        decision = self._try_parse(output)
        if decision is None:
            corrective = self._build_corrective_prompt(prompt, output)
            output = self._provider.run_headless(
                corrective, allowed_tools=list(_ALLOWED_TOOLS),
            )
            decision = self._try_parse(output)
        if decision is None:
            self._emit("diagnosis_failed", anomaly.issue, {
                "reason": "malformed_output",
                "attempts": 2,
            })
            return Decision(
                scope="unknown",
                fix_type=None,
                action=None,
                confidence="low",
                summary="diagnose-ralph emitted malformed output twice",
                evidence={"rule": anomaly.rule},
                needs_human=True,
            )

        self._emit("diagnosis_completed", anomaly.issue, {
            "scope": decision.scope,
            "fix_type": decision.fix_type,
            "confidence": decision.confidence,
            "summary": decision.summary,
        })
        return decision

    def _emit(self, event_type: str, issue: int | None, payload: dict[str, Any]) -> None:
        if self._event_log is None:
            return
        self._event_log.append(
            event_type=event_type, source=_SKILL_SOURCE,
            issue=issue, payload=payload,
        )

    def _build_prompt(self, anomaly: Anomaly, context: dict[str, Any]) -> str:
        envelope = {
            "anomaly": {
                "rule": anomaly.rule,
                "issue": anomaly.issue,
                "evidence": anomaly.evidence,
            },
            "context": context,
        }
        return (
            "You are the diagnose-ralph skill. Read the anomaly and "
            "context below and emit a single <diagnosis>{...}</diagnosis> "
            "JSON block per the SKILL.md output contract.\n\n"
            f"{json.dumps(envelope, separators=(',', ':'))}"
        )

    def _try_parse(self, output: str) -> Decision | None:
        match = _DIAGNOSIS_BLOCK.search(output)
        if not match:
            return None
        try:
            body = json.loads(match.group(1))
            # Guard against arrays / non-object bodies before indexing —
            # body["scope"] on a list raises TypeError, on a string raises
            # TypeError, neither of which json.loads catches.
            if not isinstance(body, dict):
                return None
            return Decision(
                scope=body["scope"],
                fix_type=body.get("fix_type"),
                action=body.get("action"),
                confidence=body["confidence"],
                summary=body["summary"],
                evidence=body.get("evidence", {}),
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    def _build_corrective_prompt(self, original: str, bad_output: str) -> str:
        return (
            "Your previous response did not contain a valid "
            "<diagnosis>{...}</diagnosis> JSON block. Re-emit the diagnosis "
            "now, exactly per the SKILL.md output contract.\n\n"
            "Original prompt:\n"
            f"{original}\n\n"
            "Your previous (invalid) output:\n"
            f"{bad_output}"
        )
