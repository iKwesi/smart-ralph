#!/usr/bin/env bash
# Fake ralph that emits envelope-formatted structured events via the
# real lib/ralph-events.sh helper, while also writing stdout. This is the
# post-#6 shape: ralph writes DIRECTLY to .smart-ralph/events.jsonl
# (using env vars the supervisor injects), not to a separate .ralph file.
set -euo pipefail
issue="${1:-?}"

root="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../../.." && pwd)"
# shellcheck disable=SC1091
source "$root/lib/ralph-events.sh"

echo "ralph: iteration begin"
emit_event ralph_iteration_started "$issue" \
  "$(jq -nc --argjson i 1 '{iteration:$i}')"
echo "ralph: working"
emit_event ralph_iteration_ended "$issue" \
  "$(jq -nc --argjson i 1 --arg o "complete" '{iteration:$i, outcome:$o}')"
echo "ralph: done"
