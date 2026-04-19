from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Iterator


class RalphProcess:
    def __init__(self, proc: subprocess.Popen[str], cwd: Path) -> None:
        self._proc = proc
        self._cwd = cwd

    def events(self) -> Iterator[dict[str, Any]]:
        assert self._proc.stdout is not None
        for raw in self._proc.stdout:
            line = raw.rstrip("\n")
            yield {"type": "ralph_stdout", "payload": {"line": line}}
        yield from self._replay_ralph_events()

    def _replay_ralph_events(self) -> Iterator[dict[str, Any]]:
        path = self._cwd / ".ralph" / "events.jsonl"
        if not path.exists():
            return
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            yield {"type": "ralph_event", "payload": parsed}

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
    def __init__(self, ralph_path: Path, cwd: Path) -> None:
        self._ralph_path = Path(ralph_path)
        self._cwd = Path(cwd)

    def spawn(self, prd: str) -> RalphProcess:
        proc = subprocess.Popen(
            [str(self._ralph_path), prd],
            cwd=self._cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        return RalphProcess(proc, self._cwd)
