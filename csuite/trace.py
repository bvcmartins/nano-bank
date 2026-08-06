from __future__ import annotations
import time
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler


def _short(x: Any, n: int = 2000) -> str:
    s = x if isinstance(x, str) else str(x)
    return s if len(s) <= n else s[:n] + "…"


def _model_output(response) -> dict:
    """Pull the interesting parts of a chat-model turn out of the LLMResult:
    what the model *said* (content), the hidden reasoning if the backend emits
    it, and which tools it decided to call. Best-effort — never raises."""
    try:
        gen = response.generations[0][0]
        msg = getattr(gen, "message", None)
        if msg is None:
            return {"content": _short(getattr(gen, "text", "") or "")}
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        calls = [{"name": tc.get("name"), "args": tc.get("args", {})}
                 for tc in (getattr(msg, "tool_calls", None) or [])]
        ak = getattr(msg, "additional_kwargs", None) or {}
        reasoning = ak.get("reasoning_content") or ak.get("reasoning")
        out: dict = {"content": _short(content), "tool_calls": calls}
        if reasoning:
            out["reasoning"] = _short(reasoning if isinstance(reasoning, str)
                                      else str(reasoning))
        return out
    except Exception:  # noqa: BLE001
        return {}


class TraceRecorder(BaseCallbackHandler):
    """Records tool/model steps of a LangGraph run as ordered, JSON-safe events.
    Each closed event carries a wall-clock `t` so it can be merged with the
    harness event log (compaction / subagent) into one ordered trace."""

    def __init__(self, on_event=None):
        self._open: dict = {}      # run_id -> {kind, name, t0, input}
        self._events: list[dict] = []
        # Optional live hook: called with each event the instant it closes, so a
        # streaming endpoint can push steps to the UI as they happen. Called on
        # the event loop (our tools are async), so it must not block.
        self.on_event = on_event

    def _emit(self, event: dict) -> None:
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception:  # noqa: BLE001 — a broken sink must not kill the run
                pass

    # --- tools ---
    def on_tool_start(self, serialized, input_str, **kwargs):
        rid = kwargs.get("run_id")
        name = (serialized or {}).get("name", "tool")
        self._open[rid] = {"kind": "tool", "name": name,
                           "t0": time.perf_counter(), "input": _short(input_str)}
        self._emit({"kind": "start", "of": "tool", "name": name})

    def on_tool_end(self, output, **kwargs):
        # Full output, not _short: the verifier parses these numbers to build
        # its grounded set, and a truncated bundle would drop figures and
        # produce false "ungrounded" flags. Tool outputs are bounded (a few KB).
        text = output if isinstance(output, str) else str(output)
        self._close(kwargs.get("run_id"), ok=True, output=text)

    def on_tool_error(self, error, **kwargs):
        self._close(kwargs.get("run_id"), ok=False, error=_short(error))

    # --- model ---
    def on_chat_model_start(self, serialized, messages, **kwargs):
        rid = kwargs.get("run_id")
        name = (serialized or {}).get("name", "model")
        self._open[rid] = {"kind": "model", "name": name,
                           "t0": time.perf_counter(), "input": None}
        self._emit({"kind": "start", "of": "model", "name": name})

    def on_llm_end(self, response, **kwargs):
        rid = kwargs.get("run_id")
        if rid in self._open:
            self._close(rid, ok=True, output=_model_output(response))

    def mark(self, label: str, **detail) -> None:
        """Insert a phase divider (e.g. the verifier's revise round-trip) so the
        merged trace shows where one stage ends and the next begins."""
        self._events.append({
            "seq": len(self._events), "t": time.time(),
            "kind": "phase", "name": label, "ok": True, "elapsed_ms": None,
            "input": None, "output": (detail or None), "error": None,
        })
        self._emit(self._events[-1])

    def _close(self, rid, *, ok, output=None, error=None):
        info = self._open.pop(rid, None)
        if info is None:
            return
        self._events.append({
            "seq": len(self._events), "t": time.time(),
            "kind": info["kind"], "name": info["name"],
            "ok": ok, "elapsed_ms": int((time.perf_counter() - info["t0"]) * 1000),
            "input": info.get("input"), "output": output, "error": error,
        })
        self._emit(self._events[-1])

    def events(self) -> list[dict]:
        return list(self._events)


def merge(tool_events: list[dict], harness_events: list[dict]) -> list[dict]:
    """One ordered, auditable trace covering tool/model steps AND the non-tool
    harness events (compaction, subagent spawn/return, memory writes). Both carry
    a wall-clock `t`; sort by it, falling back to arrival order so events without
    a timestamp keep their relative position."""
    tagged = ([{**e, "source": "tool"} for e in tool_events]
              + [{**e, "source": "harness"} for e in harness_events])
    return sorted(tagged, key=lambda e: (e.get("t", 0.0), e.get("seq", 0)))
