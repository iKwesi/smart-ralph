"""Tests for the diagnose-ralph SKILL.md contract.

The skill is invoked headlessly via Claude. Its frontmatter (version,
allowed-tools, programmatic-use marker) and body (input/output contract)
are what the supervisor and Claude agree on, so they get pinned by tests.
"""
from __future__ import annotations

from pathlib import Path

from smart_ralph.router import (
    EXPECTED_SKILL_VERSION,
    DiagnosticRouter,
    SkillVersionError,
    _read_skill_version,
)

SKILL = Path(__file__).parent.parent / ".claude" / "skills" / "diagnose-ralph" / "SKILL.md"


def test_skill_file_exists():
    assert SKILL.is_file(), f"missing skill file: {SKILL}"


def test_skill_frontmatter_version_matches_supervisor_expectation():
    """Reading the version through the same parser the router uses keeps
    the on-disk skill and the in-code expected_version in lockstep."""
    actual = _read_skill_version(SKILL)
    assert actual == EXPECTED_SKILL_VERSION


def test_skill_frontmatter_marks_programmatic_use_only():
    """The skill is invoked by the supervisor headlessly, never from the
    interactive Skill picker. The frontmatter description must say so so
    a human running /skill won't accidentally trigger it."""
    text = SKILL.read_text()
    assert "programmatic" in text.lower()


def test_skill_frontmatter_lists_allowed_tools():
    """The supervisor invokes Claude with --allowed-tools matching the
    skill's frontmatter. The list must include Read/Grep/Glob plus the
    Edit-scoped-to-state.json + targeted Bash entries the router code
    sends along."""
    text = SKILL.read_text()
    # Cheap text checks — the YAML parser is intentionally tiny in
    # router.py so we just verify each tool name appears in the file.
    for tool in ["Read", "Grep", "Glob", "Edit", "Bash", "state.json"]:
        assert tool in text, f"allowed-tools missing entry referencing {tool!r}"


def test_skill_body_documents_diagnosis_xml_output_contract():
    """The body must tell Claude how to format its response so the
    router's regex-based parser can pick it up."""
    text = SKILL.read_text()
    assert "<diagnosis>" in text
    assert "</diagnosis>" in text
    for required_field in ["scope", "fix_type", "action", "confidence", "summary"]:
        assert required_field in text, f"output contract missing field {required_field!r}"


def test_router_accepts_the_real_skill_file():
    """End-to-end: the router's version check passes against the real
    on-disk SKILL.md."""

    class _NullProvider:
        def run_headless(self, prompt, *, allowed_tools):
            raise AssertionError("provider should not be called during construction")

    DiagnosticRouter(provider=_NullProvider(), skill_path=SKILL)
