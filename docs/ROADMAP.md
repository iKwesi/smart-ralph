# Roadmap

smart-ralph is sliced by risk reduction, then capability. Ship v1 to prove the thesis. v2 makes it reliable across providers. v3 is speed.

## v1 — Single supervised ralph (MVP)

The user runs `./smart-ralph <prd>` against a PRD issue and gets a self-correcting TDD loop with live observability. Claude subscription only — no API keys, no Docker.

- Python single-file executable (`./smart-ralph 5`)
- Supervisor: spawn ralph, tail stdout, watch `.ralph/state.json`, emit events
- Anomaly detector (rule-based, no LLM): exit code, duplicate-line, stall timeout, merge/review exhaustion, git conflict, rate limits
- Two diagnostic skills invoked via `claude -p` headless:
  - `diagnose-ralph` — orchestrator bugs (state patch, restart, worktree reset, rebase, escalate)
  - `auto-triage` — product bugs (file new issue / amend current / dead-end)
- Cascade routing: always invoke `diagnose-ralph` first; its JSON output includes `scope: orchestrator | product | unknown` which routes to self-fix, `auto-triage`, or `needs_human` escalation
- Permission model: `--permission-mode dontAsk` + declarative allowlist in `.claude/settings.json`. Token-neutral vs `--dangerously-skip-permissions`. Each skill carries its own narrow allowlist
- Checkpoint + rollback: snapshot state.json + git SHA before every repair; revert if repair worsens things
- Diagnostic loop budget: max 3 diagnoses per issue before forced `needs_human`
- Lockfile + stash-on-kill: prevent concurrent runs in same repo; never lose uncommitted work
- Event log at `.smart-ralph/events.jsonl`, append-only, O_APPEND atomic, ≤4KB lines, sidecar blobs for oversize payloads, retain last 50 runs
- Dashboard: rich-based live split-pane TUI (attached default), `--quiet` for CI, `--detach` for daemon mode, `smart-ralph watch` for read-only remote view, TTY auto-fallback
- Notifications: terminal bell + TUI banner + macOS desktop (webhook/Slack/email are v2)
- Rate limit handling (Claude and GitHub): sleep → notify → auto-resume
- Subcommands: `init`, `init --upgrade`, `init --force`, `uninit`, `status`, `log tail/prune`, `doctor`, `version`, `watch`
- Exit codes: 0 (merged), 1 (partial), 2 (usage), 42 (rate-limited timeout), 43 (auth), 44 (needs_human), 130 (Ctrl-C)
- Provider abstraction interface defined in v1 (critical seam for v2). Only ClaudeProvider implemented
- Ralph patches bundled with v1:
  1. Streaming fix (stdbuf primary, PTY fallback, Python-helper escalation if flaky)
  2. Structured events emission
  3. Distinct exit codes (43 auth, 44 git, 45 missing tool)
  4. Permissions migration (`dontAsk` + allowlist)
  5. Worktree + branch cleanup on verified merge
  6. Pre-slice dependency install (bug 9)
  7. CLAUDE.md enforcement in TDD prompt (bug 10)
- SWE principles travel into every target repo via `smart-ralph init`: a minimal CLAUDE.md section + updated `tdd` skill intro. Non-destructive merge via `<!-- BEGIN smart-ralph:principles -->` / `<!-- END -->` markers
- `smart-ralph init` writes only to local repo, never to `~/.claude/`
- Full event taxonomy (~30 types): run lifecycle, ralph lifecycle, issue lifecycle, iteration lifecycle, anomaly, diagnosis, repair, checkpoint, external actions, notifications
- Skill output contract: XML block wrapping JSON (`<diagnosis>{...}</diagnosis>` and `<triage-result>{...}</triage-result>`). Supervisor extracts via regex
- Skills are versioned: `version: 1` in frontmatter. Supervisor refuses to run on mismatch
- Malformed skill output: one retry with explicit "emit valid X block" instruction, second failure escalates

## v2 — Hardening + multi-provider + interactive dashboard

- Provider adapters for any model runner, not just coding CLIs: Codex, Gemini CLI, OpenCode, local models via Ollama / LM Studio / llama.cpp, direct OpenAI / Anthropic API (if affordable)
- Local-model adapter may need tool-use translation: model returns JSON fix proposal, smart-ralph executes (most local models lack native tool calling)
- Dashboard interactivity: `q`-to-detach from attached run (hand supervisor to daemon mid-flight), textual migration (click-to-expand issues, pause/resume, inline approval of risky fixes)
- Unattended notifications: desktop (macOS + Linux), webhook, Slack, email
- Pattern library from `events.jsonl` (prime diagnostic prompts with recurring failures)
- Subscription budget awareness (pause near 5-hour session limits)
- Hooks for project-specific anomaly rules in `.smart-ralph/hooks/`
- Claude CLI version-drift check on startup

## v3 — Parallelism

- Supervise N ralphs across independent unblocked issues
- Rate-limit-aware scheduler (serialise near cap, parallelise when fresh)
- Optional Sandcastle integration as sandbox provider layer. Sandcastle handles parallelism + Docker isolation; smart-ralph handles self-correction. Complementary, not competitor

## Why this slicing

- v1 proves the core thesis (self-correction on subscription, no Docker, no API keys) in a usable form
- v2 makes it reliable across projects and avoids lock-in to a single AI provider
- v3 is speed. Parallelism amplifies bugs, so only after v1 + v2 are rock-solid

## How to apply

Any feature suggestion should be placed in this roadmap. If it doesn't fit v1, don't build it in v1. Provider abstraction is the only "design-now, ship-later" exception because retrofitting it is expensive.

## Version boundary → issue generation

When v1 ships (all v1 slices merged to main), proactively run `/to-issues` against the v2 scope to file GitHub issues for every v2 line item. Repeat for v3 when v2 ships. Having issues pre-staged at version boundaries removes the planning step and preserves momentum.

## Verified facts about current ralph (as of 2026-04-18)

- Ralph is sequential, not parallel. Its orchestrator loop picks unblocked issues via Kahn's algorithm but processes them one at a time. No parallelism to preserve.
