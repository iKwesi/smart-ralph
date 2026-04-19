#!/usr/bin/env bash
# Fake ralph that reads .smart-ralph/run.lock and echoes its contents,
# so the test can verify the supervisor had created the lockfile while
# ralph was running.
set -euo pipefail
if [[ -f .smart-ralph/run.lock ]]; then
    echo "lockfile-present:$(cat .smart-ralph/run.lock)"
else
    echo "lockfile-missing"
fi
