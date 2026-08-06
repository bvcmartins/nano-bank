"""Context control: when the message history grows past a token threshold, older
messages are summarized into a rolling summary and dropped; the plan, todos, and
summary always survive (they live in HarnessState, not the message list), and the
dropped detail is written to memory so it stays recoverable. Agent-agnostic.

`compact` is pure (summarize_fn injected) so the policy is unit-tested without an
LLM; `make_context_hook` wires it as a create_react_agent pre_model_hook."""
from __future__ import annotations
from dataclasses import dataclass, field

from langchain_core.messages import SystemMessage, RemoveMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES


def estimate_tokens(messages) -> int:
    return sum(len(str(getattr(m, "content", m))) for m in messages) // 4


@dataclass
class CompactResult:
    compacted: bool
    kept: list = field(default_factory=list)
    dropped: list = field(default_factory=list)
    summary: str = ""


def compact(messages, *, threshold: int, summarize_fn, keep_last: int) -> CompactResult:
    if estimate_tokens(messages) <= threshold or len(messages) <= keep_last:
        return CompactResult(compacted=False, kept=list(messages))
    split = len(messages) - keep_last
    # The kept tail must not begin with a ToolMessage whose parent
    # AIMessage(tool_calls) got summarized into the head — that orphan is an
    # invalid message sequence and the provider rejects the next model call.
    # Advance the split past any leading ToolMessages so the tail starts on a
    # self-contained boundary (they fold into the summarized head instead).
    while split < len(messages) and isinstance(messages[split], ToolMessage):
        split += 1
    head, tail = messages[:split], messages[split:]
    return CompactResult(compacted=True, kept=list(tail), dropped=list(head),
                         summary=summarize_fn(head))


_SUMMARY_INSTRUCTION = (
    "Summarize the operational review so far in <=8 terse bullet points: the "
    "question, figures already obtained (with their window), and open threads. "
    "Facts only.")


def make_context_hook(*, threshold: int, summarizer, memory, log, keep_last: int = 6,
                       thread_id=None):
    def _summarize(head) -> str:
        try:
            resp = summarizer.invoke([SystemMessage(_SUMMARY_INSTRUCTION), *head])
            return resp.content if hasattr(resp, "content") else str(resp)
        except Exception:  # noqa: BLE001
            return "(summary unavailable)"

    def pre_model_hook(state) -> dict:
        msgs = state["messages"]
        res = compact(msgs, threshold=threshold, summarize_fn=_summarize,
                      keep_last=keep_last)
        if not res.compacted:
            return {}
        prior = state.get("running_summary") or ""
        rolling = (prior + "\n" + res.summary).strip()
        # best-effort: park the dropped detail in durable memory. thread_id lives
        # in config.configurable, not HarnessState, so state.get("thread_id") is
        # always None — take it from the assemble-time closure instead.
        memory.record("compacted context: " + res.summary, kind="context",
                      thread_id=thread_id)
        log.add("compaction", dropped=len(res.dropped),
                kept=len(res.kept), summary_chars=len(res.summary))
        # Persisted mutation: drop everything, re-seed with a summary system
        # message + the recent tail. plan/todos/running_summary are separate
        # state keys and are untouched.
        new_messages = [RemoveMessage(id=REMOVE_ALL_MESSAGES),
                        SystemMessage("Rolling summary of earlier context:\n" + rolling),
                        *res.kept]
        return {"messages": new_messages, "running_summary": rolling}

    return pre_model_hook
