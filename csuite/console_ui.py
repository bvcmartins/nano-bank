"""Shared Streamlit console for any C-suite agent: a chat that streams the run
live (start markers + each step as it closes) then shows the answer, the
grounding badge, and a full run-tree inspector. Each agent's console.py is a
one-liner calling `run_console(...)` with its title + /ask endpoint."""
from __future__ import annotations
import json

import httpx
import streamlit as st

from .verifier import badge
from .trace_view import extract_highlights, to_steps


def esc(text: str) -> str:
    """Escape '$' so Streamlit doesn't render '$…$' spans as LaTeX math — which
    mangles dollar amounts (spaces stripped, '**'→'∗∗', '-'→'−')."""
    return (text or "").replace("$", "\\$")


def _snip(text: str, n: int) -> str:
    text = text or ""
    return text if len(text) <= n else text[:n].rstrip() + " …"


def _live_line(step: dict) -> str:
    s = f"{step['icon']} **{step['title']}**"
    if step["timing"]:
        s += f"  ·  `{step['timing']}`"
    if step["subtitle"]:
        s += f"  —  {step['subtitle']}"
    return esc(s)


def render_run_tree(trace: list[dict], veri: dict | None) -> None:
    """The full, expandable run tree: model reasoning, tool input→output, the
    subagent hand-off, memory, and the verifier's revise divider."""
    with st.expander("🔎 run trace — reasoning & tool calls", expanded=True):
        for step in to_steps(trace):
            if step["kind"] == "phase":
                st.markdown(f"---\n#### {step['icon']} {step['title']}")
                if step["subtitle"]:
                    st.caption(step["subtitle"])
                continue
            head = f"{step['icon']} **{step['title']}**"
            if step["timing"]:
                head += f"  ·  `{step['timing']}`"
            if step["subtitle"]:
                head += f"  ·  {step['subtitle']}"
            st.markdown(esc(head))
            for label, text in step["body"].items():
                st.caption(label)
                if label in ("says", "thinking"):
                    # the model's own reasoning — render it readably, capped.
                    st.markdown("> " + esc(_snip(text, 1200)).replace("\n", "\n> "))
                else:
                    st.code(_snip(text, 1200), language=None)  # literal — no $ escape
        if veri:
            st.markdown(esc(
                f"---\n**verification** — grounded {veri.get('grounded', [])} · "
                f"ungrounded {veri.get('ungrounded', [])} · "
                f"revised {veri.get('revised', False)}"))
        st.caption("raw trace (json)")
        st.json(trace, expanded=False)


def run_console(*, title: str, page_icon: str, api_url: str,
                placeholder: str = "Ask about how the bank is running…") -> None:
    st.set_page_config(page_title=title, page_icon=page_icon)
    st.title(title)

    if "thread_id" not in st.session_state:
        st.session_state.thread_id = None
    if "history" not in st.session_state:
        st.session_state.history = []

    for role, text in st.session_state.history:
        with st.chat_message(role):
            st.markdown(esc(text))

    if prompt := st.chat_input(placeholder):
        st.session_state.history.append(("user", prompt))
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            answer, veri, trace = "(no answer)", None, []
            try:
                with st.status("Working…", expanded=True) as live, \
                        httpx.stream("POST", f"{api_url}/ask/stream",
                                     json={"message": prompt,
                                           "thread_id": st.session_state.thread_id},
                                     timeout=600) as r:
                    r.raise_for_status()
                    for raw in r.iter_lines():
                        if not raw:
                            continue
                        msg = json.loads(raw)
                        if "event" in msg:
                            ev = msg["event"]
                            if ev.get("kind") == "start":
                                live.update(label=("🧠 model reasoning…"
                                                   if ev.get("of") == "model"
                                                   else f"🔧 {ev.get('name')}…"))
                                continue
                            for step in to_steps([ev]):
                                live.update(label=f"{step['icon']} {step['title']}")
                                live.write(_live_line(step))
                                # show a sample of the model's actual reasoning
                                # as it lands, not just the step title.
                                says = step["body"].get("says") or step["body"].get("thinking")
                                if step["kind"] == "model" and says:
                                    live.markdown("> " + esc(_snip(says, 300)))
                        elif "final" in msg:
                            f = msg["final"]
                            answer = f.get("answer", "(no answer)")
                            veri = f.get("verification")
                            trace = f.get("trace", [])
                            st.session_state.thread_id = f.get("thread_id")
                    live.update(label="Done", state="complete", expanded=False)
            except Exception as e:  # noqa: BLE001
                answer = f"⚠️ agent unreachable: {e}"

            # The answer in its own clearly-separated box, above the run-trace,
            # so the streamed reasoning never crowds it out.
            with st.container(border=True):
                st.markdown("#### 💬 Answer")
                st.markdown(esc(answer))
                if veri is not None:
                    line = badge(veri)
                    (st.warning if (veri.get("ungrounded") or veri.get("unsupported_claims"))
                     else st.caption)(line)
            if trace:
                h = extract_highlights(trace)
                chips = []
                if h["plan"]:
                    chips.append(f"🗺️ planned ×{len(h['plan'])}")
                if h["todos"]:
                    chips.append(f"✅ todos ×{len(h['todos'])}")
                if h["tools"]:
                    chips.append("🔧 " + ", ".join(
                        f"{k}×{v}" if v > 1 else k for k, v in h["tools"].items()))
                if h["subagents"]:
                    chips.append(f"🧵 subagent ×{len(h['subagents'])}")
                if h["recalls"]:
                    chips.append(f"🧠 recall ×{h['recalls']}")
                if h["records"]:
                    chips.append(f"💾 record ×{h['records']}")
                if h["compactions"]:
                    chips.append(f"📦 compaction ×{len(h['compactions'])}")
                if chips:
                    st.caption(" · ".join(chips))
                render_run_tree(trace, veri)
            st.session_state.history.append(("assistant", answer))
