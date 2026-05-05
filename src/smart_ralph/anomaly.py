"""Real-time anomaly detection over the supervisor's event stream.

The detector is stateful: it observes every event the supervisor sees,
accumulates short-lived context (e.g., a bounded stdout tail), and runs
registered rules. Rules are callables of (event, context) -> Anomaly | None.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque

Event = dict[str, Any]


@dataclass(frozen=True)
class Anomaly:
    rule: str
    issue: int | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Context:
    """Read-only snapshot of detector state passed to rules."""
    log_tail: list[str]


Rule = Callable[[Event, Context], "Anomaly | None"]


# Exit codes that are normal lifecycle outcomes, not anomalies:
#   0   — success
#   42  — Claude rate limit (handled by a dedicated rule)
#   130 — SIGINT, user-initiated shutdown
_NORMAL_EXIT_CODES = frozenset({0, 42, 130})

LOG_TAIL_MAX_LINES = 100


def _ralph_nonzero_exit(event: Event, context: Context) -> Anomaly | None:
    if event.get("type") != "ralph_exited":
        return None
    exit_code = event.get("payload", {}).get("exit_code")
    if not isinstance(exit_code, int) or exit_code in _NORMAL_EXIT_CODES:
        return None
    return Anomaly(
        rule="ralph_nonzero_exit",
        issue=event.get("issue"),
        evidence={
            "exit_code": exit_code,
            "log_tail": list(context.log_tail),
        },
    )


class AnomalyDetector:
    def __init__(self) -> None:
        self._rules: list[Rule] = [_ralph_nonzero_exit]
        self._log_tail: Deque[str] = deque(maxlen=LOG_TAIL_MAX_LINES)

    def register(self, rule: Rule) -> None:
        self._rules.append(rule)

    def observe(self, event: Event) -> list[Anomaly]:
        if event.get("type") == "ralph_stdout":
            line = event.get("payload", {}).get("line")
            if isinstance(line, str):
                self._log_tail.append(line)

        context = Context(log_tail=list(self._log_tail))
        return [a for rule in self._rules if (a := rule(event, context)) is not None]
