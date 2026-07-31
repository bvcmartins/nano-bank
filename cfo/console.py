"""Streamlit chat console for the Agent CFO. Talks to the CFO /ask endpoint."""
from __future__ import annotations
import os
import sys
import httpx
import streamlit as st

# `streamlit run cfo/console.py` puts cfo/ (the script dir) on sys.path, not the
# repo root, so the `cfo` package isn't importable by default. Add the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cfo.verifier import badge  # noqa: E402

API = os.environ.get("CFO_API_URL", "http://localhost:8089")

st.set_page_config(page_title="nano-bank CFO", page_icon="📊")
st.title("nano-bank — Agent CFO")

if "thread_id" not in st.session_state:
    st.session_state.thread_id = None
if "history" not in st.session_state:
    st.session_state.history = []

for role, text in st.session_state.history:
    with st.chat_message(role):
        st.markdown(text)

if prompt := st.chat_input("Ask the CFO about the bank's finances…"):
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
        except Exception as e:  # noqa: BLE001
            answer = f"⚠️ CFO unreachable: {e}"
        st.markdown(answer)
        if veri is not None:
            line = badge(veri)
            # badge() counts ungrounded figures AND unsupported claims, so escalate
            # on either — an answer flagged only for a phantom-metric claim was
            # rendering as a quiet caption while the badge said "⚠".
            if veri.get("ungrounded") or veri.get("unsupported_claims"):
                st.warning(line)
            else:
                st.caption(line)
        st.session_state.history.append(("assistant", answer))
