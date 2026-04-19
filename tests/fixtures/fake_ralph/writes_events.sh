#!/usr/bin/env bash
# Fake ralph that writes structured events to .ralph/events.jsonl
# *while* also writing stdout lines. Mimics the real patched ralph.
set -euo pipefail
prd="${1:-?}"
mkdir -p .ralph
echo "ralph: iteration begin"
printf '{"type":"ralph_iteration_started","payload":{"prd":"%s"}}\n' "$prd" >> .ralph/events.jsonl
echo "ralph: working"
printf '{"type":"ralph_iteration_ended","payload":{"ok":true}}\n' >> .ralph/events.jsonl
echo "ralph: done"
