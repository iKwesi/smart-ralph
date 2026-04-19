from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from smart_ralph.dashboard import Dashboard
from smart_ralph.supervisor import (
    ConcurrentRunError,
    HealthCheckError,
    Supervisor,
)

DEFAULT_REQUIRED_TOOLS = ["ralph", "claude", "gh", "jq", "git"]


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if len(argv) != 1:
        print("usage: smart-ralph <issue-number>", file=sys.stderr)
        return 2
    raw = argv[0]
    try:
        issue = int(raw)
    except ValueError:
        issue = 0
    if issue <= 0:
        print(
            f"error: issue number must be a positive integer; got {raw!r}",
            file=sys.stderr,
        )
        return 2

    ralph_override = os.environ.get("SMART_RALPH_RALPH_PATH")
    if ralph_override:
        ralph_path = Path(ralph_override)
    else:
        found = shutil.which("ralph")
        if not found:
            print("error: ralph not found on PATH", file=sys.stderr)
            return 2
        ralph_path = Path(found)

    tools_env = os.environ.get("SMART_RALPH_REQUIRED_TOOLS")
    if tools_env is not None:
        required_tools = [t.strip() for t in tools_env.split(",") if t.strip()]
    else:
        required_tools = DEFAULT_REQUIRED_TOOLS

    supervisor = Supervisor(
        ralph_path=ralph_path,
        cwd=Path.cwd(),
        required_tools=required_tools,
    )
    dashboard = Dashboard(stream=sys.stdout)

    try:
        exit_code, events = supervisor.run(issue=issue)
    except HealthCheckError as e:
        print(f"error: health check failed: {e}", file=sys.stderr)
        return 2
    except ConcurrentRunError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    for evt in events:
        dashboard.emit(evt)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
