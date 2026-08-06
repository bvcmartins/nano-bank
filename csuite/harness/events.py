from __future__ import annotations
import time


class HarnessLog:
    """An ordered, JSON-safe sink for harness events that are NOT tool calls
    (compaction, subagent spawn/return). Tool-shaped harness capabilities
    (plan/todo/memory tools) already surface through the TraceRecorder; this
    captures the rest so the full run stays auditable."""

    def __init__(self):
        self._events: list[dict] = []

    def add(self, kind: str, **fields) -> None:
        self._events.append({"seq": len(self._events), "t": time.time(),
                             "kind": kind, **fields})

    def events(self) -> list[dict]:
        return list(self._events)
