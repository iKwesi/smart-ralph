"""Tests for ClaudeProvider (issue #8) — the v1 Provider Protocol impl
that spawns `claude -p --permission-mode dontAsk` headlessly."""
from __future__ import annotations

import os
from pathlib import Path

from smart_ralph.provider import ClaudeProvider

FIXTURES = Path(__file__).parent / "fixtures"


def _write_claude_shim(tmp_path: Path, body: str) -> Path:
    shim = tmp_path / "claude"
    shim.write_text(f"#!/usr/bin/env bash\n{body}\n")
    shim.chmod(0o755)
    return shim


# ── Slice 6: ClaudeProvider spawns claude -p with dontAsk ──

def test_run_headless_invokes_claude_p_with_dontask(tmp_path):
    """The provider must invoke `claude -p` with `--permission-mode dontAsk`,
    pass the prompt, and return stdout. We inject a stub `claude` via the
    SMART_RALPH_CLAUDE_PATH env override so we don't depend on the real CLI."""
    shim = _write_claude_shim(
        tmp_path,
        # echoes argv so we can assert flag presence; final line returns the
        # canned diagnosis stdout the router would receive.
        'echo "ARGS:$@" >&2\n'
        'echo "<diagnosis>{}</diagnosis>"\n',
    )

    provider = ClaudeProvider(claude_path=shim)
    out = provider.run_headless(
        "do the diagnosis", allowed_tools=["Read", "Grep"],
    )

    assert "<diagnosis>" in out


def test_run_headless_passes_prompt_to_claude(tmp_path):
    """The prompt the router builds must reach the claude binary."""
    out_path = tmp_path / "captured-prompt.txt"
    shim = _write_claude_shim(
        tmp_path,
        # the prompt is the LAST positional arg per `claude -p <prompt>`
        f'echo "$@" > {out_path}\n'
        'echo "<diagnosis>{}</diagnosis>"\n',
    )

    provider = ClaudeProvider(claude_path=shim)
    provider.run_headless("PROMPT_MARKER_xyz", allowed_tools=[])

    captured = out_path.read_text()
    assert "PROMPT_MARKER_xyz" in captured
    assert "-p" in captured.split()
    assert "--permission-mode" in captured
    assert "dontAsk" in captured


def test_provider_uses_env_override_for_claude_path(tmp_path, monkeypatch):
    """Default constructor honours SMART_RALPH_CLAUDE_PATH so callers can
    pin a specific binary without threading it through every config layer."""
    shim = _write_claude_shim(
        tmp_path,
        'echo "<diagnosis>{}</diagnosis>"\n',
    )
    monkeypatch.setenv("SMART_RALPH_CLAUDE_PATH", str(shim))

    provider = ClaudeProvider()  # no claude_path arg — picks up the env
    out = provider.run_headless("x", allowed_tools=[])

    assert "<diagnosis>" in out
