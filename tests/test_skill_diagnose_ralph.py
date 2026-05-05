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
    ALLOWED_TOOLS,
    read_skill_version,
)

SKILL = Path(__file__).parent.parent / ".claude" / "skills" / "diagnose-ralph" / "SKILL.md"


def test_skill_file_exists():
    assert SKILL.is_file(), f"missing skill file: {SKILL}"


def test_skill_frontmatter_version_matches_supervisor_expectation():
    """Reading the version through the same parser the router uses keeps
    the on-disk skill and the in-code expected_version in lockstep."""
    actual = read_skill_version(SKILL)
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


def test_skill_allowed_tools_match_router_allowlist():
    """The skill's frontmatter `allowed-tools:` list and router.ALLOWED_TOOLS
    are two sources of truth — drift means the supervisor's --allowed-tools
    invocation diverges from what the on-disk skill advertises. Parse the
    YAML list and assert exact set equality so a missing entry on either
    side fails fast."""
    text = SKILL.read_text()

    # Slice the frontmatter (between the first pair of "---" markers).
    parts = text.split("---", 2)
    assert len(parts) >= 3, "SKILL.md is missing a YAML frontmatter block"
    frontmatter = parts[1]

    # Pull the `allowed-tools:` block. It's a YAML list of "  - <entry>"
    # lines that ends at the next top-level key (no leading whitespace).
    in_block = False
    skill_tools: list[str] = []
    for line in frontmatter.splitlines():
        stripped = line.strip()
        if stripped == "allowed-tools:":
            in_block = True
            continue
        if in_block:
            if line.startswith("  - "):
                skill_tools.append(line[4:].strip())
            elif stripped == "" or line.startswith("  "):
                continue
            else:
                # next top-level key
                break

    assert set(skill_tools) == set(ALLOWED_TOOLS), (
        f"SKILL.md allowed-tools and router.ALLOWED_TOOLS have drifted.\n"
        f"  in SKILL only: {set(skill_tools) - set(ALLOWED_TOOLS)}\n"
        f"  in router only: {set(ALLOWED_TOOLS) - set(skill_tools)}"
    )
