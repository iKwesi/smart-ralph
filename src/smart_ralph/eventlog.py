from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

# POSIX guarantees atomic appends only up to PIPE_BUF (typically 4096).
# Lines that would exceed this window have their payload offloaded to a
# sidecar blob, mirroring lib/ralph-events.sh on the bash side.
_PIPE_BUF_BYTES = 4096


class EventLog:
    def __init__(self, path: Path, run_id: str) -> None:
        self._path = Path(path)
        self._run_id = run_id
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        *,
        event_type: str,
        source: str,
        issue: int | None,
        payload: dict[str, Any],
        sync: bool = False,
    ) -> None:
        envelope = {
            "schema_version": SCHEMA_VERSION,
            "ts": _now_iso_ms(),
            "run_id": self._run_id,
            "type": event_type,
            "source": source,
            "issue": issue,
            "payload": payload,
        }
        line = json.dumps(envelope, separators=(",", ":")) + "\n"
        if len(line.encode("utf-8")) > _PIPE_BUF_BYTES:
            envelope["payload"] = self._offload_payload(payload)
            line = json.dumps(envelope, separators=(",", ":")) + "\n"

        fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
            if sync:
                os.fsync(fd)
        finally:
            os.close(fd)

    def _offload_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Write payload to a blob under blobs/<run_id>/ and return a tiny
        replacement payload that references it via blob_ref."""
        events_root = self._path.parent
        blob_dir = events_root / "blobs" / self._run_id
        blob_dir.mkdir(parents=True, exist_ok=True)
        blob_name = f"{uuid.uuid4().hex}.json"
        blob_path = blob_dir / blob_name
        blob_path.write_text(json.dumps(payload, separators=(",", ":")))
        # Path is relative to events_root so readers can resolve it without
        # needing to know the writer's cwd.
        return {"oversized": True, "blob_ref": f"blobs/{self._run_id}/{blob_name}"}

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
