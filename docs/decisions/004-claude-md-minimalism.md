# ADR 004 — CLAUDE.md minimalism for scaffolded repos

**Status:** Accepted.

## Context

`smart-ralph init` scaffolds a `CLAUDE.md` section into every target repo it's run against, so that SWE principles (KISS / YAGNI / DRY / SOLID / 12-factor / Clean Code) and the `tdd` skill reference travel with smart-ralph wherever it's installed.

CLAUDE.md is read by Claude Code on every turn. Every line is recurring context cost — tokens, attention, cost. Recent Anthropic guidance (echoed by OpenAI for equivalents) is explicit: CLAUDE.md should be minimal. A bloated CLAUDE.md eats the context window, degrades attention to what matters, and slows every invocation.

## Decision

The scaffold rule: the injected CLAUDE.md section must stay small — target ~60 tokens, one short paragraph.

Specifically:

- Never redefine well-known concepts (KISS, DRY, SOLID, TDD, REST, 12-factor, etc.). Claude knows them from training. Naming them activates them — that is enough.
- Include only project-specific overrides and behavioral invariants ("use the `tdd` skill for code", "no emojis in logs", etc.).
- Detailed rationale lives in `docs/`, not CLAUDE.md.
- The same rule applies to smart-ralph's own dev-repo CLAUDE.md. If a section balloons past ~100 tokens, it's signaling that content belongs in a docs file, not the auto-loaded file.

The specific injected section (~60 tokens):

```markdown
<!-- BEGIN smart-ralph:principles -->
## Engineering standards

Apply KISS, YAGNI, DRY, SOLID, 12-factor, Clean Code. Use the `tdd` skill for all code work — red-green-refactor, vertical slices. If a test is hard to write, refactor toward testable shape first. No emojis in code or logs unless explicitly requested.
<!-- END smart-ralph:principles -->
```

## Consequences

- Every Claude invocation in a scaffolded repo pays ~60 tokens. Acceptable: the alternative is many multi-thousand-token "please refactor" review cycles.
- Existing CLAUDE.md files in target repos are preserved. smart-ralph only owns content inside the BEGIN/END markers. User content outside the markers stays untouched and is never overwritten by `--upgrade`.
- Same non-destructive merge strategy applies to `.claude/settings.json`: deep-merge `permissions.allow` arrays; never clobber user entries.
- This rule is load-bearing for all future tools we build that scaffold into user repos. If we add another scaffolder later, it must respect the same minimalism.
