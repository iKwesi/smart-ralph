"""Run Supervisor with RalphProcess.kill monkey-patched to raise.

Used by test_kill_exception_on_sigint_is_logged_as_repair_failed to verify
the SIGINT handler audit-logs kill failures. Needs its own subprocess so
SIGINT can be delivered to a specific PID.

Usage: python run_supervisor_broken_kill.py <cwd> <ralph_path> <issue>
"""

import sys
from pathlib import Path

from smart_ralph import ralph_client
from smart_ralph.supervisor import Supervisor


def _broken_kill(self, issue: int) -> None:
    raise RuntimeError("git binary missing")


def main() -> int:
    cwd = Path(sys.argv[1])
    ralph_path = Path(sys.argv[2])
    issue = int(sys.argv[3])

    ralph_client.RalphProcess.kill = _broken_kill

    supervisor = Supervisor(
        ralph_path=ralph_path,
        cwd=cwd,
        required_tools=["git"],
    )
    exit_code, _ = supervisor.run(issue=issue)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
