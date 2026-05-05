from __future__ import annotations

import os
import shutil
import traceback
import uuid
from collections.abc import Callable
from pathlib import Path

from smart_ralph.anomaly import Anomaly, AnomalyDetector
from smart_ralph.eventlog import EventLog
from smart_ralph.ralph_client import RalphClient
from smart_ralph.router import DiagnosticRouter


class ConcurrentRunError(RuntimeError):
    pass


class HealthCheckError(RuntimeError):
    pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _default_detector_factory() -> AnomalyDetector:
    """Default factory used by Supervisor when no detector_factory is
    injected. A named function (rather than the class itself) keeps the
    Callable[[], AnomalyDetector] type literal under strict type checkers
    that distinguish type[AnomalyDetector] from a no-arg callable."""
    return AnomalyDetector()


class Supervisor:
    def __init__(
        self,
        ralph_path: Path,
        cwd: Path,
        required_tools: list[str],
        retention_runs: int = 50,
        *,
        detector_factory: Callable[[], AnomalyDetector] = _default_detector_factory,
        router_factory: Callable[[EventLog], DiagnosticRouter] | None = None,
    ) -> None:
        self._ralph_path = Path(ralph_path)
        self._cwd = Path(cwd)
        self._required_tools = required_tools
        self._retention_runs = retention_runs
        # A factory (not an instance) is injected so each run() gets a
        # fresh detector. This avoids state leaking — the bounded log_tail
        # or any per-rule counters — across repeated runs on the same
        # Supervisor instance. Callers that want shared state across runs
        # can pass a factory that returns the same instance, but they opt
        # in explicitly.
        self._detector_factory = detector_factory
        # Optional: when wired, every Anomaly the detector returns is
        # routed through DiagnosticRouter. Default None → graceful
        # degradation; the supervisor still records anomaly_detected
        # events but skips routing entirely.
        self._router_factory = router_factory

    def run(self, issue: int) -> tuple[int, list[dict]]:
        if not isinstance(issue, int) or issue <= 0:
            raise ValueError(
                f"issue must be a positive integer; got {issue!r}"
            )
        missing = [t for t in self._required_tools if shutil.which(t) is None]
        if missing:
            raise HealthCheckError(
                f"required tools not found on PATH: {', '.join(missing)}"
            )

        meta_dir = self._cwd / ".smart-ralph"
        meta_dir.mkdir(parents=True, exist_ok=True)
        lock_path = meta_dir / "run.lock"

        if lock_path.exists():
            try:
                existing_pid = int(lock_path.read_text().strip())
            except ValueError:
                existing_pid = -1
            if existing_pid > 0 and _pid_alive(existing_pid):
                raise ConcurrentRunError(
                    f"smart-ralph is already running (pid {existing_pid}); "
                    f"remove {lock_path} if stale"
                )

        lock_path.write_text(str(os.getpid()))

        run_id = uuid.uuid4().hex[:16]
        log = EventLog(meta_dir / "events.jsonl", run_id=run_id)
        log.prune_runs(keep=self._retention_runs)
        detector = self._detector_factory()
        router = self._router_factory(log) if self._router_factory is not None else None
        process = None
        exit_code = 1
        events: list[dict] = []

        def _record_anomalies(anomalies: list[Anomaly]) -> None:
            for a in anomalies:
                log.append(
                    event_type="anomaly_detected", source="supervisor",
                    issue=a.issue,
                    payload={"rule": a.rule, "evidence": a.evidence},
                    sync=True,
                )
                if router is not None:
                    # Context is intentionally minimal in this slice — the
                    # router builds its prompt from anomaly + context. Richer
                    # state-snapshot context lands when state.json reading
                    # is wired (issue #9 territory).
                    #
                    # A routing failure (provider timeout, network, parser
                    # crash, etc.) must never take the whole supervised run
                    # down. We log it as diagnosis_failed and continue.
                    try:
                        router.route(a, context={"issue": a.issue})
                    except Exception as e:
                        # Include the full traceback so operators can
                        # locate the failure without re-running with a
                        # debugger. EventLog auto-offloads >4KB to a
                        # blob, so even a long traceback is safe.
                        log.append(
                            event_type="diagnosis_failed", source="supervisor",
                            issue=a.issue,
                            payload={
                                "reason": "router_exception",
                                "error": f"{type(e).__name__}: {e}",
                                "traceback": traceback.format_exc(),
                            },
                            sync=True,
                        )

        try:
            log.append(
                event_type="run_started", source="supervisor",
                issue=issue, payload={}, sync=True,
            )
            client = RalphClient(
                ralph_path=self._ralph_path,
                cwd=self._cwd,
                run_id=run_id,
                events_path=meta_dir / "events.jsonl",
            )
            process = client.spawn(issue=issue)
            log.append(
                event_type="ralph_spawned", source="supervisor",
                issue=issue, payload={"pid": process.pid}, sync=True,
            )
            events = []
            for evt in process.events():
                events.append(evt)
                _record_anomalies(detector.observe(evt))
            exit_code = process.wait()
            ralph_exited_event = {
                "type": "ralph_exited",
                "issue": issue,
                "payload": {"exit_code": exit_code},
            }
            log.append(
                event_type="ralph_exited", source="supervisor",
                issue=issue, payload={"exit_code": exit_code}, sync=True,
            )
            _record_anomalies(detector.observe(ralph_exited_event))
            return exit_code, events
        except KeyboardInterrupt:
            if process is not None:
                try:
                    process.kill(issue=issue)
                except Exception as e:
                    log.append(
                        event_type="repair_failed", source="supervisor",
                        issue=issue,
                        payload={"op": "kill_and_stash", "error": str(e)},
                        sync=True,
                    )
                log.append(
                    event_type="ralph_exited", source="supervisor",
                    issue=issue,
                    payload={"exit_code": -2, "reason": "sigint"},
                    sync=True,
                )
            exit_code = 130
            return exit_code, events
        finally:
            log.append(
                event_type="run_ended", source="supervisor",
                issue=issue, payload={"exit_code": exit_code}, sync=True,
            )
            if lock_path.exists():
                lock_path.unlink()
