"""Streamlit chat console for the Agent COO. Talks to the COO /ask endpoint."""
from __future__ import annotations
import os
import sys
import httpx
import streamlit as st

# `streamlit run coo/console.py` puts coo/ (the script dir) on sys.path, not the
# repo root, so the `coo` package isn't importable by default. Add the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from coo.verifier import badge  # noqa: E402
from coo.demo.drive import extract_highlights  # noqa: E402

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

if prompt := st.chat_input("Ask the COO about how the bank is running…"):
    st.session_state.history.append(("user", prompt))
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        veri = None
        try:
            r = httpx.post(f"{API}/ask",
                           json={"message": prompt,
                                 "thread_id": st.session_state.thread_id},
                           timeout=600)
            r.raise_for_status()
            data = r.json()
            st.session_state.thread_id = data.get("thread_id")
            answer = data.get("answer", "(no answer)")
            veri = data.get("verification")
            trace = data.get("trace", [])
        except Exception as e:  # noqa: BLE001
            answer = f"⚠️ COO unreachable: {e}"
            trace = []
        st.markdown(answer)
        if veri is not None:
            line = badge(veri)
            if veri.get("ungrounded") or veri.get("unsupported_claims"):
                st.warning(line)
            else:
                st.caption(line)
        # What the harness actually did this turn — plan/todos/subagent/memory,
        # so a viewer can see the machinery behind the answer, not just the text.
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
            with st.expander("harness trace"):
                for sa in h["subagents"]:
                    st.markdown(
                        f"**subagent** (depth {sa['depth']}, "
                        f"tools=`{', '.join(sa['tools'])}`, {sa['chars']} chars): "
                        f"{sa['task'][:160]}")
                if veri:
                    st.markdown(
                        f"**grounded** {veri.get('grounded', [])}  \n"
                        f"**ungrounded** {veri.get('ungrounded', [])}  \n"
                        f"**revised** {veri.get('revised', False)}")
                st.json(trace, expanded=False)
        st.session_state.history.append(("assistant", answer))
