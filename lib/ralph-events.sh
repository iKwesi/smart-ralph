# ralph-events.sh — structured event emission for ralph.
#
# Emits envelope-shaped JSONL events to .smart-ralph/events.jsonl so the
# smart-ralph supervisor can observe ralph's internal lifecycle alongside
# its own events. Source this file; do not execute it directly.
#
# Controlled by environment:
#   SMART_RALPH_RUN_ID       — supervisor-assigned run id (required to emit)
#   SMART_RALPH_EVENTS_PATH  — absolute path to events.jsonl
#
# When SMART_RALPH_RUN_ID is unset, emit_event is a silent no-op so ralph
# runs standalone without polluting the filesystem.

# Envelope schema version — must match src/smart_ralph/eventlog.py.
RALPH_EVENTS_SCHEMA_VERSION=1

# emit_event <type> <issue|""> <payload_json>
#
# Writes one envelope JSON line via O_APPEND (shell `>>` is POSIX O_APPEND,
# atomic for writes ≤ PIPE_BUF ≈ 4KB). issue may be empty or "null" for
# nullable; otherwise pass a bare integer.
emit_event() {
  [[ -z "${SMART_RALPH_RUN_ID:-}" ]] && return 0

  local type="$1"
  local issue="${2:-}"
  local payload_json="${3:-{\}}"

  local events_path="${SMART_RALPH_EVENTS_PATH:-.smart-ralph/events.jsonl}"

  # ts in ISO 8601 UTC with millisecond precision.
  local ts
  ts=$(python3 -c "from datetime import datetime, timezone; n = datetime.now(timezone.utc); print(n.strftime('%Y-%m-%dT%H:%M:%S.') + f'{n.microsecond // 1000:03d}Z')")

  local issue_arg
  if [[ -z "$issue" || "$issue" == "null" ]]; then
    issue_arg="null"
  else
    issue_arg="$issue"
  fi

  local line
  line=$(jq -nc \
    --argjson sv "$RALPH_EVENTS_SCHEMA_VERSION" \
    --arg ts "$ts" \
    --arg rid "$SMART_RALPH_RUN_ID" \
    --arg type "$type" \
    --argjson issue "$issue_arg" \
    --argjson payload "$payload_json" \
    '{schema_version:$sv, ts:$ts, run_id:$rid, type:$type, source:"ralph", issue:$issue, payload:$payload}')

  # POSIX atomic-append guarantee is ≤ PIPE_BUF (~4KB including newline).
  # Oversized payloads are offloaded to a sidecar blob; the line keeps only
  # a reference so concurrent writers never interleave partial data.
  if (( ${#line} + 1 > 4096 )); then
    local events_root blob_dir blob_rel blob_path
    events_root=$(dirname "$events_path")
    blob_dir="$events_root/blobs/$SMART_RALPH_RUN_ID"
    mkdir -p "$blob_dir"
    local blob_name="$(date -u +%s%N)-$$.json"
    blob_path="$blob_dir/$blob_name"
    printf '%s' "$payload_json" > "$blob_path"

    # blob_ref is relative to the cwd so supervisor can resolve it.
    blob_rel="${blob_path#$PWD/}"
    line=$(jq -nc \
      --argjson sv "$RALPH_EVENTS_SCHEMA_VERSION" \
      --arg ts "$ts" \
      --arg rid "$SMART_RALPH_RUN_ID" \
      --arg type "$type" \
      --argjson issue "$issue_arg" \
      --arg blob_ref "$blob_rel" \
      '{schema_version:$sv, ts:$ts, run_id:$rid, type:$type, source:"ralph", issue:$issue, payload:{oversized:true, blob_ref:$blob_ref}}')
  fi

  mkdir -p "$(dirname "$events_path")"
  printf '%s\n' "$line" >> "$events_path"
}
