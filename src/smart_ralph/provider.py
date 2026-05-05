"""Provider adapters — headless invokers for model runners.

v1 ships ClaudeProvider, which spawns `claude -p --permission-mode dontAsk`.
The Protocol is defined alongside so v2 adapters (Codex, Gemini, local
models) can plug in without touching the router.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

# Default subprocess timeout for a single headless skill invocation.
# Large enough that diagnose-ralph can read several files via Read/Grep,
# small enough that a hung claude doesn't block the supervisor forever.
_DEFAULT_TIMEOUT_SECONDS = 300


class ClaudeProvider:
    """v1 Provider implementation: spawns `claude -p` with the dontAsk
    permission mode and a per-skill allowlist."""

    def __init__(
        self,
        claude_path: Path | None = None,
        *,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if claude_path is None:
            override = os.environ.get("SMART_RALPH_CLAUDE_PATH")
            if override:
                claude_path = Path(override)
            else:
                found = shutil.which("claude")
                if found is None:
                    raise RuntimeError(
                        "claude not found on PATH and SMART_RALPH_CLAUDE_PATH "
                        "is not set"
                    )
                claude_path = Path(found)
        self._claude_path = Path(claude_path)
        self._timeout_seconds = timeout_seconds

    def run_headless(self, prompt: str, *, allowed_tools: list[str]) -> str:
        argv = [
            str(self._claude_path),
            "-p",
            "--permission-mode", "dontAsk",
        ]
        if allowed_tools:
            # claude --allowed-tools accepts a comma-separated list; pass
            # exactly the allowlist the caller asked for.
            argv += ["--allowed-tools", ",".join(allowed_tools)]
        argv.append(prompt)

        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=self._timeout_seconds,
        )
        return result.stdout
