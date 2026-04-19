from __future__ import annotations

from typing import IO, Any, Iterable

from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel


class Dashboard:
    def __init__(self, stream: IO[str], force_mode: str | None = None) -> None:
        self._stream = stream
        if force_mode in {"attached", "plain"}:
            self.mode = force_mode
        else:
            self.mode = "attached" if _is_tty(stream) else "plain"

    def emit(self, event: dict[str, Any]) -> None:
        if self.mode == "plain":
            self._emit_plain(event)
        else:
            self.render([event])

    def render(self, events: Iterable[dict[str, Any]]) -> None:
        events = list(events)
        if self.mode == "plain":
            for evt in events:
                self._emit_plain(evt)
            return
        self._render_attached(events)

    def _emit_plain(self, event: dict[str, Any]) -> None:
        etype = event.get("type", "?")
        payload = event.get("payload", {})
        detail = payload.get("line") or _compact(payload)
        self._stream.write(f"[{etype}] {detail}\n")
        self._stream.flush()

    def _render_attached(self, events: list[dict[str, Any]]) -> None:
        state = _derive_state(events)
        console = Console(file=self._stream, force_terminal=True, width=80)
        top_body = (
            f"Issue: {state['issue']}\n"
            f"Status: {state['status']}\n"
            f"Ralph PID: {state['pid']}"
        )
        bottom_body = "\n".join(state["stdout_tail"]) or "(no output yet)"
        layout = Layout()
        layout.split_column(
            Layout(Panel(top_body, title="Progress"), size=6),
            Layout(Panel(bottom_body, title="Ralph output")),
        )
        console.print(layout)


def _is_tty(stream: IO[str]) -> bool:
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def _compact(payload: dict[str, Any]) -> str:
    if not payload:
        return ""
    return " ".join(f"{k}={v}" for k, v in payload.items())


def _derive_state(events: list[dict[str, Any]]) -> dict[str, Any]:
    issue: Any = None
    pid: Any = None
    status = "pending"
    stdout_tail: list[str] = []
    for evt in events:
        etype = evt.get("type")
        payload = evt.get("payload", {})
        if evt.get("issue") is not None:
            issue = evt["issue"]
        if etype == "ralph_spawned":
            pid = payload.get("pid")
            status = "running"
        elif etype == "ralph_exited":
            status = f"exited ({payload.get('exit_code')})"
        elif etype == "run_ended":
            if status == "running":
                status = "ended"
        elif etype == "ralph_stdout":
            line = payload.get("line", "")
            stdout_tail.append(line)
    return {
        "issue": issue if issue is not None else "?",
        "pid": pid if pid is not None else "?",
        "status": status,
        "stdout_tail": stdout_tail[-10:],
    }
