from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


class EventLog:
    def __init__(self, path: Path, run_id: str) -> None:
        self._path = Path(path)
        self._run_id = run_id
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        *,
        type: str,
        source: str,
        issue: int | None,
        payload: dict[str, Any],
        sync: bool = False,
    ) -> None:
        envelope = {
            "schema_version": SCHEMA_VERSION,
            "ts": _now_iso_ms(),
            "run_id": self._run_id,
            "type": type,
            "source": source,
            "issue": issue,
            "payload": payload,
        }
        line = json.dumps(envelope, separators=(",", ":")) + "\n"
        fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
            if sync:
                os.fsync(fd)
        finally:
            os.close(fd)

    def prune_runs(self, keep: int) -> None:
        if not self._path.exists():
            return
        lines = self._path.read_text().splitlines()
        # collect run_ids in order of first appearance
        seen: list[str] = []
        parsed: list[tuple[str, str]] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = evt.get("run_id", "")
            if rid not in seen:
                seen.append(rid)
            parsed.append((rid, line))
        if len(seen) <= keep:
            return
        kept = set(seen[-keep:])
        retained = [line for rid, line in parsed if rid in kept]
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text("\n".join(retained) + ("\n" if retained else ""))
        os.replace(tmp, self._path)

    def tail(self, n: int) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        lines = self._path.read_text().splitlines()
        result: list[dict[str, Any]] = []
        for line in lines[-n:]:
            if not line.strip():
                continue
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return result


def _now_iso_ms() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
