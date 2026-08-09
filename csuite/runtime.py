"""The shared C-suite agent runtime: the ask / ask_stream flow every harnessed
agent (COO, CFO, …) runs. Agent-agnostic — the caller supplies the prompt, the
model, the (already-fetched) domain tools, and a memory; everything else (the
harness, grounding verifier, revise pass, live streaming) lives here.

    from csuite import runtime
    out = await runtime.ask(settings=s, message=m, prompt=PROMPT, model=llm,
                            tools=tools, agent="cfo")
"""
from __future__ import annotations
import asyncio
import uuid
from typing import AsyncIterator, Optional

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from .trace import TraceRecorder, merge
from . import verifier, claims
from .harness import assemble
from .harness.memory import HarnessMemory, SafeMemory

# One process-lived checkpointer so a thread_id restores its plan/todos/summary
# across requests. Each agent runs in its own process, so a module global here is
# naturally per-agent — no cross-agent state sharing.
_CHECKPOINTER = InMemorySaver()


_NO_ANSWER = "(no answer)"

_SYNTH_NUDGE = (
    "Give your final written answer now: summarize the figures you gathered from "
    "the tools this turn — grounded, with the period/window named. Do not call any "
    "more tools; just write the answer.")


def _last_ai_text(state) -> str:
    for m in reversed(state["messages"]):
        if isinstance(m, AIMessage) and (m.content or "").strip():
            return m.content
    return _NO_ANSWER


def _empty(answer: str) -> bool:
    return not answer or answer.strip() in ("", _NO_ANSWER)


def default_memory(settings):
    """Best-effort durable memory from settings; degrades to a no-op if Qdrant is
    unreachable so the agent still answers from live tools."""
    try:
        return SafeMemory(HarnessMemory.from_settings(settings))
    except Exception:  # noqa: BLE001
        return SafeMemory(None)


def _build(*, settings, prompt, model, tools, memory, agent, thread_id):
    thread_id = thread_id or f"{agent}-{uuid.uuid4().hex[:6]}"
    memory = memory if memory is not None else default_memory(settings)
    react, log = assemble(
        model, tools, prompt, memory, thread_id=thread_id, checkpointer=_CHECKPOINTER,
        context_token_threshold=settings.context_token_threshold,
        subagent_max_depth=settings.subagent_max_depth)
    return react, log, memory, thread_id


async def ask(*, settings, message: str, prompt: str, model, tools, agent: str,
              thread_id: Optional[str] = None, memory=None, claims_fn=None) -> dict:
    _claims_fn = claims_fn or claims.unsupported_claims
    react, log, _, thread_id = _build(settings=settings, prompt=prompt, model=model,
                                      tools=tools, memory=memory, agent=agent,
                                      thread_id=thread_id)
    rec = TraceRecorder()
    cfg = {"configurable": {"thread_id": thread_id}, "recursion_limit": 60,
           "callbacks": [rec]}
    init = {"messages": [HumanMessage(message)], "plan": [], "todos": [],
            "running_summary": "", "depth": 0}
    out = await react.ainvoke(init, config=cfg)
    answer = _last_ai_text(out)
    if _empty(answer):   # model ended on an empty turn after tool work — synthesise
        out = await react.ainvoke({"messages": [HumanMessage(_SYNTH_NUDGE)]}, config=cfg)
        answer = _last_ai_text(out)

    revised = False
    figs = verifier.ungrounded(answer, rec.events())
    clms = _claims_fn(answer, rec.events())
    if figs or clms:
        revised = True
        rec.mark("revision", figures=figs, claims=clms)
        nudge = verifier.revise_prompt(figs, clms)
        out = await react.ainvoke({"messages": [HumanMessage(nudge)]}, config=cfg)
        answer = _last_ai_text(out)

    trace = merge(rec.events(), log.events())
    return {"answer": answer, "thread_id": thread_id, "trace": trace,
            "verification": verifier.report(answer, rec.events(), revised=revised,
                                            claims_fn=_claims_fn)}


_SENTINEL = object()


async def ask_stream(*, settings, message: str, prompt: str, model, tools, agent: str,
                     thread_id: Optional[str] = None, memory=None, claims_fn=None
                     ) -> AsyncIterator[dict]:
    """Yield the run as it happens: `{"event": <trace event>}` per start/step/phase
    the instant it fires, then exactly one `{"final": {...}}`. Same verify-then-
    revise flow as `ask`."""
    _claims_fn = claims_fn or claims.unsupported_claims
    react, log, _, thread_id = _build(settings=settings, prompt=prompt, model=model,
                                      tools=tools, memory=memory, agent=agent,
                                      thread_id=thread_id)
    q: asyncio.Queue = asyncio.Queue()
    rec = TraceRecorder(on_event=q.put_nowait)   # callbacks fire on this loop
    cfg = {"configurable": {"thread_id": thread_id}, "recursion_limit": 60,
           "callbacks": [rec]}

    def _spawn(payload) -> "asyncio.Task":
        t = asyncio.create_task(react.ainvoke(payload, config=cfg))
        t.add_done_callback(lambda _t: q.put_nowait(_SENTINEL))
        return t

    async def _pump(task):
        while True:
            item = await q.get()
            if item is _SENTINEL:
                return
            yield {"event": item}

    try:
        t1 = _spawn({"messages": [HumanMessage(message)], "plan": [], "todos": [],
                     "running_summary": "", "depth": 0})
        async for chunk in _pump(t1):
            yield chunk
        answer = _last_ai_text(t1.result())
        if _empty(answer):   # empty final turn — nudge once to synthesise
            ts = _spawn({"messages": [HumanMessage(_SYNTH_NUDGE)]})
            async for chunk in _pump(ts):
                yield chunk
            answer = _last_ai_text(ts.result())

        revised = False
        figs = verifier.ungrounded(answer, rec.events())
        clms = _claims_fn(answer, rec.events())
        if figs or clms:
            revised = True
            rec.mark("revision", figures=figs, claims=clms)
            nudge = verifier.revise_prompt(figs, clms)
            t2 = _spawn({"messages": [HumanMessage(nudge)]})
            async for chunk in _pump(t2):
                yield chunk
            answer = _last_ai_text(t2.result())

        trace = merge(rec.events(), log.events())
        yield {"final": {"answer": answer, "thread_id": thread_id, "trace": trace,
                         "verification": verifier.report(answer, rec.events(),
                                                          revised=revised,
                                                          claims_fn=_claims_fn)}}
    except Exception as e:  # noqa: BLE001
        yield {"final": {"answer": f"⚠️ the {agent} run failed: {type(e).__name__}: {e}",
                         "thread_id": thread_id,
                         "trace": merge(rec.events(), log.events()),
                         "verification": {"grounded": [], "ungrounded": [],
                                          "unsupported_claims": [], "revised": False}}}
