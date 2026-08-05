"""Streamlit chat console for the Agent COO. Streams the COO's run live so the
view fills in as the model reasons and calls tools, instead of blocking on the
whole turn; then shows the answer + a full run-tree inspector."""
from __future__ import annotations
import json
import os
import sys
import httpx
import streamlit as st

# `streamlit run coo/console.py` puts coo/ (the script dir) on sys.path, not the
# repo root, so the `coo` package isn't importable by default. Add the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from coo.verifier import badge  # noqa: E402
from coo.trace_view import extract_highlights, to_steps  # noqa: E402

API = os.environ.get("COO_API_URL", "http://localhost:8093")

st.set_page_config(page_title="nano-bank COO", page_icon="🏭")
st.title("nano-bank — Agent COO")

if "thread_id" not in st.session_state:
    st.session_state.thread_id = None
if "history" not in st.session_state:
    st.session_state.history = []

for role, text in st.session_state.history:
    with st.chat_message(role):
        st.markdown(text)


def _live_line(step: dict) -> str:
    """A compact one-liner for the live ticker as a step streams in."""
    s = f"{step['icon']} **{step['title']}**"
    if step["timing"]:
        s += f"  ·  `{step['timing']}`"
    if step["subtitle"]:
        s += f"  —  {step['subtitle']}"
    return s


def render_run_tree(trace: list[dict], veri: dict | None) -> None:
    """The full, expandable run tree: model reasoning, tool input→output, the
    subagent hand-off, memory, and the verifier's revise divider."""
    with st.expander("🔎 run trace — reasoning & tool calls", expanded=False):
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
            st.markdown(head)
            for label, text in step["body"].items():
                st.caption(label)
                st.code(text, language=None)
        if veri:
            st.markdown(
                f"---\n**verification** — grounded {veri.get('grounded', [])} · "
                f"ungrounded {veri.get('ungrounded', [])} · "
                f"revised {veri.get('revised', False)}")
        with st.expander("raw trace (json)"):
            st.json(trace, expanded=False)


if prompt := st.chat_input("Ask the COO about how the bank is running…"):
    st.session_state.history.append(("user", prompt))
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        answer, veri, trace = "(no answer)", None, []
        try:
            # Stream the run: each step renders into the live status the instant it
            # closes, so the console is never blank while the COO works.
            with st.status("Working…", expanded=True) as live, \
                    httpx.stream("POST", f"{API}/ask/stream",
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
                            # Immediate "currently doing X" feedback, before the
                            # step (and its timing) close — so it's never blank
                            # while a slow model turn generates.
                            live.update(label=("🧠 model reasoning…"
                                               if ev.get("of") == "model"
                                               else f"🔧 {ev.get('name')}…"))
                            continue
                        for step in to_steps([ev]):
                            live.update(label=f"{step['icon']} {step['title']}")
                            live.write(_live_line(step))
                    elif "final" in msg:
                        f = msg["final"]
                        answer = f.get("answer", "(no answer)")
                        veri = f.get("verification")
                        trace = f.get("trace", [])
                        st.session_state.thread_id = f.get("thread_id")
                live.update(label="Done", state="complete", expanded=False)
        except Exception as e:  # noqa: BLE001
            answer = f"⚠️ COO unreachable: {e}"

        st.markdown(answer)
        if veri is not None:
            line = badge(veri)
            (st.warning if (veri.get("ungrounded") or veri.get("unsupported_claims"))
             else st.caption)(line)
        # At-a-glance chips + the full run-tree inspector.
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
