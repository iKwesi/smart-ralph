---
name: diagnose-ralph
version: 1
description: Programmatic-use only. Invoked headlessly by the smart-ralph supervisor when an anomaly fires. Reads the anomaly + supervisor context, classifies the failure, and emits a single <diagnosis>{...}</diagnosis> JSON block.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash(jq:*)
  - Bash(git log:*)
  - Bash(git status:*)
  - Bash(git diff:*)
  - Bash(cat .ralph/*)
  - Edit(.ralph/state.json)
---

# diagnose-ralph

You are the diagnose-ralph skill. The smart-ralph supervisor has detected
an anomaly during a ralph orchestration run and is invoking you headlessly
via `claude -p`. **Do not produce conversational output.** Your single job
is to classify the failure and emit one valid `<diagnosis>` block per the
output contract below. The supervisor parses your output deterministically.

## Input contract

The supervisor sends a single JSON envelope on stdin (in the prompt):

```json
{
  "anomaly": {
    "rule": "<rule name, e.g. ralph_nonzero_exit>",
    "issue": <int or null>,
    "evidence": { "exit_code": ..., "log_tail": [...], ... }
  },
  "context": {
    "issue": <int>,
    "state_snapshot": { ... },
    "log_tail": [ ... ],
    "iterations": <int>
  }
}
```

You may use your `allowed-tools` to read further state — `gh issue view
<n>`, `cat .ralph/state.json`, `git log/status/diff`, file reads, etc. —
but do not edit anything other than `.ralph/state.json`.

## Output contract

Emit exactly one XML block, on its own. No prose before or after.

```
<diagnosis>{
  "scope": "orchestrator" | "product" | "unknown",
  "fix_type": "state_patch" | "restart" | "worktree_reset" | "rebase" | "escalate" | null,
  "action": { ... fix-type-specific payload ... } | null,
  "confidence": "low" | "medium" | "high",
  "summary": "<one-line human-readable explanation>",
  "evidence": { ... structured citations from your investigation ... }
}</diagnosis>
```

### Field semantics

- **scope** — Where the bug lives.
  - `orchestrator`: ralph itself is stuck (stale state, dead loop, missed
    transition). The supervisor will apply your fix directly.
  - `product`: the thing ralph is building is broken. The supervisor will
    hand off to the auto-triage skill to file/amend a GitHub issue.
  - `unknown`: you cannot determine the scope. The supervisor escalates
    to `needs_human`.
- **fix_type** — What action the supervisor should take. Required when
  `scope == "orchestrator"`. May be `null` when `scope` is `product` or
  `unknown` (the supervisor routes elsewhere). Set to `"escalate"` if
  the orchestrator is unrepairable from your seat.
- **action** — Fix-type-specific payload. Examples:
  - `state_patch`: `{ "path": ".ralph/state.json", "key": "...", "value": ... }`
  - `restart`: `{}` (supervisor will respawn ralph fresh)
  - `worktree_reset`: `{ "branch": "..." }`
  - `rebase`: `{ "onto": "origin/main" }`
- **confidence** — Calibrate honestly. The supervisor treats `low` as a
  signal to mark `needs_human` rather than auto-applying.
- **summary** — One sentence. Used in event logs and dashboards.
- **evidence** — Structured citations: file paths read, git SHAs
  observed, log lines that drove the conclusion. Aids audit.

## Hard rules

1. Output exactly one `<diagnosis>` block. The supervisor parses
   non-greedily; multiple blocks lead to undefined behavior.
2. The block must be valid JSON. No trailing commas, no comments, no
   unquoted keys.
3. If you cannot reach a confident classification, return
   `scope: "unknown"` with `confidence: "low"`. The supervisor will
   handle escalation. Do not invent fixes you are not sure about.
4. Never edit anything outside `.ralph/state.json`. The supervisor's
   permission allowlist enforces this; do not work around it.
