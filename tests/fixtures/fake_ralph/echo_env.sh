#!/usr/bin/env bash
# Fake ralph that echoes the smart-ralph env vars so tests can assert
# they were injected into the child process.
set -euo pipefail
echo "SMART_RALPH_RUN_ID=${SMART_RALPH_RUN_ID:-<unset>}"
echo "SMART_RALPH_EVENTS_PATH=${SMART_RALPH_EVENTS_PATH:-<unset>}"
