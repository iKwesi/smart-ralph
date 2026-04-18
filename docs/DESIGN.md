# Future Project: Self-Correcting Meta-Agent

A project-agnostic wrapper that sits on top of orchestrators (ralph, Sandcastle, or any custom script), monitors them in real-time, and self-corrects when they fail.

## The Problem

During the Ethos website build, ralph hit an infinite loop - a merge_failed state with no retry limit caused it to repeat the same action forever. We had to manually diagnose the bug, fix the state file, patch the code, and restart. A meta-agent automates this entire cycle.

## How It Works

1. Spawns the orchestrator as a child process
2. Streams and analyzes stdout/stderr in real-time
3. Detects anomalies: repeated log lines, stalled state, exit code patterns, infinite loops
4. When a failure is detected:
   - Kills the orchestrator
   - Reads the state file, logs, and orchestrator source code
   - Sends the context to an LLM to diagnose the root cause
   - LLM proposes a fix (state reset, code patch, config change)
   - If the fix is safe (state file update, restart) - applies it automatically
   - If the fix is risky (branch deletion, force push) - asks the user first
   - Restarts the orchestrator
   - Notifies the user: "Detected infinite loop in merge retry for #6. PR was already merged but state was stale. Fixed state and resumed."

## Key Design Points

- Project-agnostic: works with any orchestrator, any codebase
- Safe by default: only applies fixes it classifies as low-risk
- Human-in-the-loop for dangerous operations
- Learns from past failures (logs fixes for pattern matching)

## Complexity: 6/10

| Layer | Difficulty | Description |
|-------|-----------|-------------|
| Process supervision | 3/10 | Spawn, monitor, kill, restart child processes |
| Anomaly detection | 5/10 | Pattern matching on logs, state file watching |
| Intelligence layer | 8/10 | Diagnose root cause, decide correct fix, verify it worked |
| Trust/safety layer | 6/10 | Classify fixes as safe vs risky, enforce boundaries |

## Skills to Learn

### 1. Process Supervision
How to spawn, monitor, kill, and restart child processes programmatically.
- **Node.js:** `child_process.spawn()`, `child_process.fork()`
- **Python:** `subprocess.Popen`, `asyncio.create_subprocess_exec`
- **Key concepts:** stdin/stdout piping, signal handling (SIGTERM, SIGKILL), exit codes, process groups

### 2. Log Parsing and Anomaly Detection
Recognizing patterns like infinite loops, stalled progress, and repeated errors in real-time log streams.
- **Simple:** Regex pattern matching, sliding window duplicate detection
- **Intermediate:** Streaming line analysis with debounce (e.g., "same line 5+ times in 10 seconds")
- **Advanced:** Token-based log classification, rate-of-change analysis on state files
- **Tools:** Node.js readline, Python watchdog (file system monitoring)

### 3. State Machine Design
Understanding valid state transitions and detecting when something is stuck in an invalid or looping state.
- **Key concepts:** Finite state machines, transition tables, guard conditions, timeout states
- **For this project:** Model the orchestrator's states (pending, in_progress, complete, reviewed, merged, failed) and define legal transitions. The meta-agent detects illegal transitions or states that haven't changed within a timeout.
- **Library:** XState (JavaScript) for formal state machine modeling

### 4. LLM-as-Reasoning-Engine
Using an LLM as the diagnostic brain - feeding it error context and getting back a structured fix.
- **Key skill:** Writing diagnostic prompts that include: the error, recent logs, state file content, relevant source code, and expected behavior
- **Output format:** Structured JSON with fields like `{ diagnosis, fix_type, fix_action, risk_level, confidence }`
- **Tools:** Anthropic Claude API, Vercel AI SDK, structured output / tool_use for reliable JSON responses

### 5. Idempotency and Safe Retries
Understanding which operations are safe to retry and which can cause damage if repeated.
- **Safe:** Resetting a state file value, restarting a process, re-running a test
- **Unsafe:** Force pushing, deleting branches, dropping database tables, sending duplicate notifications
- **Key principle:** Every automated fix must be idempotent - applying it twice should have the same effect as applying it once

### 6. Anthropic Agent SDK
The official SDK for building custom AI agents in TypeScript/Python.
- **Why:** Cleanest way to build the intelligence layer rather than raw API calls
- **What it provides:** Agent loop management, tool definitions, conversation state, structured outputs
- **Docs:** https://docs.anthropic.com (Agent SDK section)

## Possible Architecture

```
meta-agent (TypeScript/Python)
  |
  |-- spawns orchestrator (ralph, Sandcastle, etc.) as child process
  |-- streams stdout/stderr through anomaly detector
  |-- watches state file for changes (fs.watch / watchdog)
  |
  |-- on anomaly detected:
  |     |-- collects context (last 50 log lines, state file, orchestrator source)
  |     |-- sends to LLM for diagnosis
  |     |-- receives structured fix proposal
  |     |-- classifies risk level
  |     |-- if safe: apply fix, restart orchestrator
  |     |-- if risky: notify user, wait for approval
  |
  |-- on orchestrator exit:
        |-- if exit code 0: done
        |-- if exit code != 0: diagnose and attempt recovery
```

## Feature: Live Terminal Dashboard

Ralph currently dumps raw text with no sense of progress. The dashboard gives an at-a-glance view of the entire orchestration while still showing Claude's output.

### Design: Split-pane in one terminal

A fixed panel at the top shows live progress. Claude's raw output scrolls below it. One terminal, two zones - no second terminal needed.

```
┌─ ralph - PRD #1 ──────────────────────────────────────────────┐
│                                                                │
│  Progress: ████████░░░░░░░░░░░░░░░░  3/13 (23%)              │
│                                                                │
│  #6  Scaffolding          ✓ merged                             │
│  #7  Layout + Navbar      ✓ merged                             │
│  #8  Hero + Trust bar     ● building [iter 3/10]               │
│  #9  Services + Why       ○ blocked by #8                      │
│  #10 Testimonials + CTA   ○ blocked by #9                      │
│  #11 About page           ○ waiting                            │
│  #12 Services page        ○ waiting                            │
│  #13 Quotation wizard     ○ waiting                            │
│  #14 Become an Agent      ○ waiting                            │
│  #15 Contact page         ○ waiting                            │
│  #16 AI Chat              ○ waiting                            │
│  #17 Page transitions     ○ blocked by #10-#16                 │
│  #18 SEO + deploy         ○ blocked by #17                     │
│                                                                │
│  Current: Building hero parallax animation...                  │
│  Time elapsed: 24m | Est. remaining: ~1h 12m                   │
├────────────────────────────────────────────────────────────────┤
│  Claude output:                                                │
│  > Reading GitHub issue #8...                                  │
│  > Creating hero section component with parallax...            │
│  > Writing test: hero headline renders correctly...            │
│  > Test passed. Committing...                                  │
│  > ...                                                         │
└────────────────────────────────────────────────────────────────┘
```

### How it works

- Top panel is fixed (doesn't scroll). Reads ralph's state file in real-time.
- Bottom panel scrolls with Claude's streaming output.
- Progress bar calculates from merged/total issues.
- Issue statuses update live as the state file changes.
- "Current" line parses Claude's latest output for a human-readable summary.
- Time estimates based on average time per completed slice.

### Tech options

- **Ink (React for terminal)** - cleanest if building in TypeScript. Components, state, re-renders.
- **rich (Python)** - excellent live display, tables, progress bars. Less setup.
- **blessed / blessed-contrib (Node.js)** - full terminal UI widgets, charts, gauges.
- **Raw ANSI escape codes** - no dependencies, but harder to maintain.

### Relationship to meta-agent

The dashboard is the **visual layer**. The meta-agent is the **intelligence layer**. They share the same state file monitoring infrastructure. Build them as one project:

```
smart-ralph (or whatever we name it)
  |
  |-- dashboard (visual) - renders live TUI from state file + logs
  |-- meta-agent (intelligence) - detects failures, diagnoses, fixes
  |-- orchestrator (execution) - the actual ralph build/review/merge loop
```

## Known Ralph Bugs to Fix in This Project

Issues discovered during the Ethos website build. All of these should be solved in the smart-ralph rewrite.

### 1. Output buffering (not streaming)
The pipe chain (claude | grep | tee | jq) causes output to batch instead of stream in real-time. Each pipe step adds its own buffer. Need to use `stdbuf -oL` or rewrite the output handling to avoid buffered pipes.

### 2. Rate limit detection
Ralph should detect "hit your limit" immediately and stop. Currently burns through all iterations and all remaining issues uselessly. Fixed with `exit 42` but should be smarter - detect any non-recoverable error pattern and stop.

### 3. Worktree name length
Claude Code limits worktree names to 64 characters. Long issue titles overflow. Ralph truncates slugs but the prefix + slug can still exceed the limit. Fixed by reducing slug to 40 chars, but should dynamically calculate available space.

### 4. "failed" state not terminal
If an issue hits "failed", the outer loop re-processes it as a full rebuild, creating potential infinite loops. Fixed by skipping "failed" alongside "merged".

### 5. Merge exit code vs actual state
`gh pr merge --delete-branch` returns non-zero when branch cleanup fails even though the merge succeeded. Ralph now verifies PR state on GitHub instead of trusting exit codes.

### 6. "complete" state skips review on resume
When ralph is interrupted after build but before review, the state is "complete". On resume, the retry block merges directly, skipping review. Review fixes are lost.

### 7. Review fix mode missing (now added)
TDD Claude rebuilt from scratch instead of reading review comments. Fixed by adding a `fix_review` mode with a targeted prompt.

### 8. No post-merge build verification
Ralph doesn't run `npm run build` after merging to verify the codebase compiles. Missing dependencies or broken imports are not caught.

### 9. No dependency install before each slice
New slices may need packages added by previous slices. Ralph should run package install before each TDD run.

### 10. CLAUDE.md rules not enforced proactively
Rules like "no em dashes" are written in CLAUDE.md but TDD Claude doesn't always follow them. The review Claude catches them, but the fix cycle adds unnecessary round-trips.

## When to Build

After the Ethos website is complete. This is a standalone, project-agnostic tool for any coding project. The user plans to use this for major projects going forward.
