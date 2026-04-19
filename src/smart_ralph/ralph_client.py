from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any, Iterator


class RalphProcess:
    def __init__(self, proc: subprocess.Popen[str], cwd: Path) -> None:
        self._proc = proc
        self._cwd = cwd

    def events(self) -> Iterator[dict[str, Any]]:
        """Yield ralph's human-readable stdout wrapped as ralph_stdout events.

        Structured events from ralph are written directly to the shared
        .smart-ralph/events.jsonl by ralph itself (via lib/ralph-events.sh)
        using the run_id the supervisor injects at spawn time, so there is
        no in-memory merge step here.
        """
        assert self._proc.stdout is not None
        for raw in self._proc.stdout:
            line = raw.rstrip("\n")
            yield {"type": "ralph_stdout", "payload": {"line": line}}

    def wait(self) -> int:
        return self._proc.wait()

    @property
    def pid(self) -> int:
        return self._proc.pid

    def kill(self, issue: int) -> None:
        stash_name = f"smart-ralph-issue-{issue}-{int(time.time())}"
        subprocess.run(
            ["git", "stash", "push", "--include-untracked", "-m", stash_name],
            cwd=self._cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        self._proc.terminate()
        try:
            self._proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()


class RalphClient:
    def __init__(
        self,
        ralph_path: Path,
        cwd: Path,
        *,
        run_id: str | None = None,
        events_path: Path | None = None,
    ) -> None:
        self._ralph_path = Path(ralph_path)
        self._cwd = Path(cwd)
        self._run_id = run_id
        self._events_path = Path(events_path) if events_path is not None else None

    def spawn(self, issue: int) -> RalphProcess:
        env = os.environ.copy()
        if self._run_id is not None:
            env["SMART_RALPH_RUN_ID"] = self._run_id
        if self._events_path is not None:
            env["SMART_RALPH_EVENTS_PATH"] = str(self._events_path)

        proc = subprocess.Popen(
            [str(self._ralph_path), str(issue)],
            cwd=self._cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        return RalphProcess(proc, self._cwd)
