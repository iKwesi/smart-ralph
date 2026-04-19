"""Run Supervisor in a subprocess so SIGINT can be sent to it.

Usage: python run_supervisor.py <cwd> <ralph_path> <prd>
Exit code is whatever Supervisor.run would return (or 130 on SIGINT).
"""

import sys
from pathlib import Path

from smart_ralph.supervisor import Supervisor


def main() -> int:
    cwd = Path(sys.argv[1])
    ralph_path = Path(sys.argv[2])
    prd = sys.argv[3]

    supervisor = Supervisor(
        ralph_path=ralph_path,
        cwd=cwd,
        required_tools=["git"],
    )
    exit_code, _ = supervisor.run(prd=prd)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
