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
readonly RALPH_EVENTS_SCHEMA_VERSION=1

# Char-count threshold above which we fall through to a byte-accurate
# check. UTF-8 is at most 4 bytes/char. 1023 chars × 4 bytes + 1 byte for
# the trailing newline = 4093 bytes, safely under the 4096-byte PIPE_BUF
# atomicity window even in the adversarial all-4-byte-UTF-8 case.
readonly RALPH_EVENTS_BYTE_CHECK_CHAR_THRESHOLD=1023

# _ralph_ts — emit ISO 8601 UTC timestamp.
#
# Prefers GNU date / gdate for millisecond precision; falls back to bash's
# printf '%(...)T' builtin (no fork) on platforms without %N-aware date.
# The backend is probed once and memoized in _RALPH_TS_IMPL.
_ralph_ts() {
  if [[ -z "${_RALPH_TS_IMPL:-}" ]]; then
    # Probe the exact format we'll use (+%3N) rather than +%N — some
    # BSD-derived dates accept %N but reject precision modifiers like %3N.
    if command -v gdate >/dev/null 2>&1; then
      _RALPH_TS_IMPL="gdate"
    elif [[ "$(date -u +%3N 2>/dev/null)" =~ ^[0-9]{3}$ ]]; then
      _RALPH_TS_IMPL="gnu-date"
    else
      _RALPH_TS_IMPL="bash-builtin"
    fi
  fi
  case "$_RALPH_TS_IMPL" in
    gdate)        gdate -u +"%Y-%m-%dT%H:%M:%S.%3NZ" ;;
    gnu-date)     date -u +"%Y-%m-%dT%H:%M:%S.%3NZ" ;;
    bash-builtin) printf '%(%Y-%m-%dT%H:%M:%S.000Z)T' -1 ;;
  esac
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

  # Fast path: if char count is well under 4KB, byte count cannot exceed
  # 4KB either (UTF-8 is at most 4 bytes/char and almost all payloads are
  # mostly ASCII). Only fork wc -c when we're close enough that multi-byte
  # content could matter.
  local needs_offload=0
  if (( ${#line} + 1 > 4096 )); then
    needs_offload=1
  elif (( ${#line} + 1 > RALPH_EVENTS_BYTE_CHECK_CHAR_THRESHOLD )); then
    local line_bytes
    line_bytes=$(printf '%s\n' "$line" | wc -c)
    (( line_bytes > 4096 )) && needs_offload=1
  fi

  if (( needs_offload )); then
    local events_root blob_dir blob_rel blob_path blob_name
    events_root=$(dirname "$events_path")
    blob_dir="$events_root/blobs/$SMART_RALPH_RUN_ID"
    mkdir -p "$blob_dir"
    # Uniqueness comes from (ts digits) + pid + 30-bit random. Portable
    # across BSD and GNU date since we reuse _ralph_ts and strip non-digits.
    # Two $RANDOM values give 30 bits of entropy, not 15, so collisions
    # within the same ms from the same process are ~1/10^9 instead of
    # ~1/32k.
    local ts_digits
    ts_digits=$(_ralph_ts | tr -dc '0-9')
    blob_name="${ts_digits}-$$-${RANDOM}${RANDOM}.json"
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
