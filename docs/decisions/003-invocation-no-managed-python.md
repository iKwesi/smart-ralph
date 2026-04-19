# ADR 003 — Invocation: `./smart-ralph 5`, never managed-Python runtimes

**Status:** Accepted.

## Context

smart-ralph is written in Python but must feel as frictionless as the bash `ralph` script it wraps. Python packaging tooling has multiple invocation conventions:

- `python -m smart_ralph 5` (stdlib module runner)
- `uv run smart-ralph 5` (uv-managed)
- `poetry run smart-ralph 5` (poetry-managed)
- `source .venv/bin/activate && smart-ralph 5` (venv-activated)
- `./smart-ralph 5` (shebang script)

Each adds ceremony for the user. The goal is a tool, not a Python project.

## Decision

smart-ralph must be invokable as a plain command: `./smart-ralph 5` or `smart-ralph 5` if on PATH. The rule separates install-time from invocation-time:

- **Install time (one-time): `uv pip install` is acceptable.** It's a drop-in for `pip` and doesn't alter invocation.
- **Invocation time (every use): `./smart-ralph 5` only.** Never `uv run`, never `source .venv/bin/activate`, never `python -m`.

v1 development:
- Single executable file with `#!/usr/bin/env python3` shebang, `chmod +x`. Treat like a script, not a package.
- Dev install via `uv pip install rich watchdog pydantic` (deps go to the current Python).

v1.0 release:
- Bundle as a shiv `.pyz` zipapp. Users download one file, `chmod +x`, run. No uv, no pip, no deps management at runtime.

## Consequences

- Users have zero friction: `smart-ralph` behaves like any shell tool, like `ralph` or `gh`.
- Dev-side packaging (`src/smart_ralph/`, `pyproject.toml`) is permitted because users never see it — they invoke the shebang wrapper or the shiv bundle.
- A Python runtime is still required on the user's machine. The shiv bundle handles everything else.
- This rule applies to any future CLI tool we build. Zero-ceremony invocation is the default.
