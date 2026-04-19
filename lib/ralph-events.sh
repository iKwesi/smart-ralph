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

# _ralph_ts — emit ISO 8601 UTC timestamp.
#
# Prefers GNU date / gdate for millisecond precision; falls back to bash's
# printf '%(...)T' builtin (no fork) on platforms without %N-aware date.
# The backend is probed once and memoized in _RALPH_TS_IMPL.
_ralph_ts() {
  if [[ -z "${_RALPH_TS_IMPL:-}" ]]; then
    if command -v gdate >/dev/null 2>&1; then
      _RALPH_TS_IMPL="gdate"
    elif [[ "$(date -u +%N 2>/dev/null)" =~ ^[0-9]{9}$ ]]; then
      _RALPH_TS_IMPL="gnu-date"
    else
      _RALPH_TS_IMPL="bash-builtin"
    fi
    export _RALPH_TS_IMPL
  fi
  case "$_RALPH_TS_IMPL" in
    gdate)        gdate -u +"%Y-%m-%dT%H:%M:%S.%3NZ" ;;
    gnu-date)     date -u +"%Y-%m-%dT%H:%M:%S.%3NZ" ;;
    bash-builtin) printf '%(%Y-%m-%dT%H:%M:%S.000Z)T' -1 ;;
  esac
}

# _ralph_byte_len — byte count of stdin, locale-independent.
_ralph_byte_len() {
  wc -c
}

# emit_event <type> <issue|""> [<payload_json>]
#
# Writes one envelope JSON line via O_APPEND (shell `>>` is POSIX O_APPEND,
# atomic for writes ≤ PIPE_BUF ≈ 4KB). issue may be empty, "null", or any
# non-integer (all coerced to null); otherwise pass a bare integer. payload
# defaults to an empty object when omitted.
emit_event() {
  [[ -z "${SMART_RALPH_RUN_ID:-}" ]] && return 0

  local type="$1"
  local issue="${2:-}"
  local payload_json="${3:-}"
  [[ -z "$payload_json" ]] && payload_json="{}"

  local events_path="${SMART_RALPH_EVENTS_PATH:-.smart-ralph/events.jsonl}"

  local ts
  ts=$(_ralph_ts)

  # Coerce anything that isn't a plain integer (or "null"/empty) to null so
  # jq --argjson never ingests unvalidated strings as raw JSON.
  local issue_arg
  if [[ -z "$issue" || "$issue" == "null" || ! "$issue" =~ ^-?[0-9]+$ ]]; then
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

  # Byte-accurate size check: UTF-8 multi-byte chars can push a line past
  # PIPE_BUF even when char count is under the limit. ${#line} is chars;
  # wc -c is bytes.
  local line_bytes
  line_bytes=$(printf '%s\n' "$line" | _ralph_byte_len)
  if (( line_bytes > 4096 )); then
    local events_root blob_dir blob_rel blob_path blob_name
    events_root=$(dirname "$events_path")
    blob_dir="$events_root/blobs/$SMART_RALPH_RUN_ID"
    mkdir -p "$blob_dir"
    blob_name="$(date -u +%s%N 2>/dev/null || date -u +%s)-$$.json"
    blob_path="$blob_dir/$blob_name"
    printf '%s' "$payload_json" > "$blob_path"

    # blob_ref is relative to events_root so readers can resolve it
    # regardless of the process cwd at read time.
    blob_rel="${blob_path#${events_root}/}"
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
