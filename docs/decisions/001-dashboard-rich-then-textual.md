# ADR 001 — Dashboard: `rich` for v1, `textual` when interactivity is needed

**Status:** Accepted.

## Context

smart-ralph's dashboard is a live split-pane TUI: progress + state on top, Claude's streaming output on the bottom. For v1 it is read-only — no clicks, no inline controls. For v2 we may want real interactivity (click-to-expand issues, pause/resume, inline approval of risky fixes, keyboard shortcuts beyond Ctrl-C).

Two Python TUI libraries are candidates: `rich` and `textual` (same author). `rich` is the simplest path to a read-only live display. `textual` is heavier but gives a real widget model and event loop for interactivity.

## Decision

Build v1 on `rich`. Keep the event-log schema and state-file semantics stable so the view layer can be swapped later without touching the supervisor.

When v2 introduces interactive features (click-to-expand, pause/resume, inline approval, keyboard shortcuts), migrate the dashboard to `textual`. That migration is the trigger — not a calendar date.

## Consequences

- v1 ships faster with less TUI code. The rich split-pane example closely matches what `docs/DESIGN.md` sketched.
- The supervisor does not know which TUI library is in use; it writes events and state. The dashboard is purely a consumer.
- When we port to `textual`, the supervisor stays unchanged. Only `src/smart_ralph/dashboard.py` and its fallbacks move.
- Users see the same split-pane behavior before and after the port; the migration is an implementation detail.
