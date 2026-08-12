"""Agent CTO presentation console — one screen, two panes: the CTO's narrated
arc (left) and the live tamper-evident agent_action_ledger (right). Driven live
by run-demo.sh (--emit-jsonl) with a recorded fallback. Run from the HOST:

    export XDG_RUNTIME_DIR=/run/user/1000 XDG_DATA_HOME=/home/bmartins/.local/share
    streamlit run demos/08-cto/present/app.py

Live runs need docker+kind+kubectl+uv and the deployed CTO stack (see
demos/08-cto/run-demo.sh)."""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ledger  # noqa: E402
import state  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
RUN_DEMO = os.path.join(REPO_ROOT, "demos", "08-cto", "run-demo.sh")
RECORDINGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recordings")

st.set_page_config(page_title="Agent CTO", layout="wide")
ss = st.session_state
ss.setdefault("beats", [])          # rendered beat records (live or replay)
ss.setdefault("mode", "idle")       # idle | live | replay
ss.setdefault("proc", None)         # live run subprocess
ss.setdefault("jsonl_path", None)   # live run JSONL file
ss.setdefault("snapshot", None)     # ledger snapshot when replaying


def _beat_card(rec: dict) -> None:
    label, color = state.outcome_style(rec["outcome"]["kind"])
    st.markdown(f"#### Beat {rec['beat']} — {rec['title']}")
    st.caption(rec["shows"])
    st.markdown(f"**Q:** {rec['question']}")
    h = rec["harness"]
    bits = []
    if h["planned"]:
        bits.append(f"planned {h['planned']}")
    if h["todos"]:
        bits.append(f"todos {h['todos']}")
    if h["subagents"]:
        bits.append(f"subagent×{h['subagents']}")
    if h["tools"]:
        bits.append("tools: " + ", ".join(h["tools"]))
    if bits:
        with st.expander("harness · " + " · ".join(bits)):
            st.json(h)
    st.write(rec["answer"])
    detail = f" → {rec['outcome']['detail']}" if rec["outcome"]["detail"] else ""
    st.markdown(
        f"<span style='background:{color};color:white;padding:2px 10px;"
        f"border-radius:10px;font-weight:700'>{label}{detail}</span>",
        unsafe_allow_html=True)
    st.divider()


def _start_live() -> None:
    os.makedirs(RECORDINGS, exist_ok=True)
    fd, path = tempfile.mkstemp(suffix=".jsonl", prefix="cto-run-")
    os.close(fd)
    env = dict(os.environ,
               XDG_RUNTIME_DIR=os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"),
               XDG_DATA_HOME=os.environ.get("XDG_DATA_HOME",
                                            os.path.expanduser("~/.local/share")))
    ss.proc = subprocess.Popen(["bash", RUN_DEMO, "--no-up", "--emit-jsonl", path],
                               cwd=REPO_ROOT, env=env)
    ss.jsonl_path, ss.beats, ss.mode, ss.snapshot = path, [], "live", None


def _start_replay() -> None:
    latest = state.latest_recording(RECORDINGS)
    if not latest:
        st.toast("No recording yet — run live once to capture one.")
        return
    rec = state.load_recording(latest)
    ss.beats, ss.mode, ss.snapshot = rec["beats"], "replay", rec["ledger_snapshot"]


# --- control bar -----------------------------------------------------------
st.title("Agent CTO — analyst + audited self-healing")
c1, c2, c3, _ = st.columns([1, 1, 1, 4])
if c1.button("▶ Run live", type="primary", disabled=ss.mode == "live"):
    _start_live()
if c2.button("⏮ Replay last good run", disabled=ss.mode == "live"):
    _start_replay()
tamper = c3.button("🔒 Tamper demo")

left, right = st.columns([3, 2])

# --- left: agent arc -------------------------------------------------------
with left:
    st.subheader("Agent CTO")
    if ss.mode == "live" and ss.jsonl_path:
        try:
            with open(ss.jsonl_path, encoding="utf-8") as f:
                ss.beats = state.read_jsonl(f.read())
        except FileNotFoundError:
            pass
    for rec in ss.beats:
        _beat_card(rec)
    if ss.mode == "live" and ss.proc and ss.proc.poll() is not None:
        # live run finished: snapshot the ledger + save the recording
        rows = ledger.read_rows()
        state.save_recording(RECORDINGS, ss.beats, rows, ledger.chain_verdict())
        ss.mode, ss.proc = "idle", None
        st.toast("Live run complete — recording saved.")

# --- right: audit ledger ---------------------------------------------------
with right:
    st.subheader("Audit ledger")
    if tamper:
        st.write(ledger.tamper_demo())
    try:
        rows = ss.snapshot if ss.mode == "replay" else ledger.read_rows()
        status, seq = (("INTACT", None) if ss.mode == "replay"
                       else ledger.chain_verdict())
        badge = "🟢 CHAIN INTACT" if status == "INTACT" else f"🔴 TAMPERED (seq {seq})"
        st.markdown(f"### {badge}")
        st.dataframe(rows, use_container_width=True, hide_index=True)
    except Exception as e:  # noqa: BLE001
        st.warning(f"ledger unavailable ({e}). Is the port-forward / cluster up?")

# live view refreshes itself while a run is in flight
if ss.mode == "live":
    import time
    time.sleep(1.5)
    st.rerun()
