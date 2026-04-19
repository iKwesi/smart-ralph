# ADR 002 — Language: Python for v1, TypeScript port as a future option

**Status:** Accepted.

## Context

smart-ralph is a supervisor that spawns a subprocess (ralph), tails stdout, watches a JSON state file, invokes `claude -p` for diagnostics, and renders a live TUI. That work profile is not CPU-bound — it spends most of its time waiting on I/O.

Two viable languages exist: Python and TypeScript. TypeScript aligns with the user's broader stack (Vercel AI SDK, Next.js, React Native) and would enable a shared web dashboard later. Python has a stronger TUI ecosystem (`rich`, `textual`), mature subprocess and file-watching tools (`asyncio.create_subprocess_exec`, `watchdog`), and `pydantic` for typed structured IO.

## Decision

v1 is Python. This is not a forever commitment.

Design v1 with clean language-neutral seams so a later TypeScript port is a supervisor rewrite, not a ground-up rebuild:

- Provider abstraction is already a Protocol + adapter pattern (language-neutral by design)
- `events.jsonl` schema is plain JSON
- Skill files are markdown with XML-wrapped JSON outputs
- Ralph is bash, untouched by language choice
- Configs are YAML
- The only Python-specific surface is the supervisor binary itself (~1000 LOC target) and its helpers

A future TS port would reimplement supervisor / anomaly / events / dashboard modules in TypeScript against the same file-level contracts. Candidate tools: Bun (faster startup), Ink (TUI), execa (subprocess), zod (schemas). Distribution via `bun build --compile`.

## Consequences

- v1 velocity: faster because `rich` + `asyncio` + `pydantic` is a mature story for our feature set
- v1 user experience: single-file executable via shiv bundle — user never sees Python
- Porting cost later: moderate, not catastrophic. Only the supervisor modules need translation
- We accept that users who want a web dashboard will have to wait for the port or use `smart-ralph watch` in a terminal
- Realistically, a TS port is v4+ concern, behind v2 (multi-provider) and v3 (parallelism). Raise it as an option when Python maintenance cost becomes visible, or when a web dashboard becomes desirable

## Do not

Start designing for a TS port now. Keep v1 Python clean and idiomatic. The seams above emerge naturally from good v1 design — no extra work is required.
