"""Agent CTO presentation console — a presenter-paced, three-pane screen:

  · left rail  — a BUTTON PER BEAT, each captioned with what that beat tests
  · centre     — the selected beat's card (question → agent answer → outcome)
  · right      — the live tamper-evident agent_action_ledger with a chain badge

Driven live by run-demo.sh (--emit-jsonl) with a recorded fallback you can replay
beat-by-beat. Run from the HOST:

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
DRIVE_PY = os.path.join(REPO_ROOT, "demos", "08-cto", "drive.py")
RECORDINGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recordings")

st.set_page_config(page_title="Agent CTO", layout="wide")

# Which beats this demo shows (a lean set for a tight screencast). Override with
# CTO_SHOW_BEATS="1,7,8,9"; nothing is deleted — hidden beats stay in the driver
# and recordings, they're just not surfaced here or driven on a live run.
_env_beats = os.environ.get("CTO_SHOW_BEATS", "1,7,8,9")
SHOW_BEATS = sorted(int(x) for x in _env_beats.split(",") if x.strip())
FULL_CATALOG = state.beat_catalog(DRIVE_PY)     # all 9: title + what-it-tests + question
CATALOG = [b for b in FULL_CATALOG if b["beat"] in SHOW_BEATS]

ss = st.session_state
ss.setdefault("beats", [])          # rendered beat records (live or replay)
ss.setdefault("mode", "idle")       # idle | live | replay
ss.setdefault("proc", None)         # live run subprocess
ss.setdefault("jsonl_path", None)   # live run JSONL file
ss.setdefault("snapshot", None)     # ledger snapshot when replaying
ss.setdefault("selected", SHOW_BEATS[0] if SHOW_BEATS else 1)  # centre pane beat (None = all)


def _beat_card(rec: dict) -> None:
    label, color = state.outcome_style(rec["outcome"]["kind"])
    st.markdown(f"#### Beat {rec['beat']} — {rec['title']}")
    st.caption(rec["shows"])
    st.markdown(f"**Q:** {rec['question']}")
    h = rec["harness"]
    bits = []
    if h.get("planned"):
        bits.append(f"planned {h['planned']}")
    if h.get("todos"):
        bits.append(f"todos {h['todos']}")
    if h.get("subagents"):
        bits.append(f"subagent×{h['subagents']}")
    if h.get("tools"):
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


def _pending_card(cat: dict) -> None:
    """A beat that hasn't been run/replayed yet: show its intent + question."""
    st.markdown(f"#### Beat {cat['beat']} — {cat['title']}")
    st.caption(cat["shows"])
    st.markdown(f"**Q:** {cat['question']}")
    st.info("Not shown yet — click **▶ Run live** or **⏮ Replay last good run**, "
            "then pick this beat.")


def _start_live() -> None:
    os.makedirs(RECORDINGS, exist_ok=True)
    fd, path = tempfile.mkstemp(suffix=".jsonl", prefix="cto-run-")
    os.close(fd)
    env = dict(os.environ,
               XDG_RUNTIME_DIR=os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"),
               XDG_DATA_HOME=os.environ.get("XDG_DATA_HOME",
                                            os.path.expanduser("~/.local/share")))
    cmd = ["bash", RUN_DEMO, "--no-up", "--emit-jsonl", path]
    if SHOW_BEATS:                       # drive only the shown beats → a shorter live run
        cmd += ["--beats", ",".join(str(n) for n in SHOW_BEATS)]
    ss.proc = subprocess.Popen(cmd, cwd=REPO_ROOT, env=env)
    ss.jsonl_path, ss.beats, ss.mode, ss.snapshot = path, [], "live", None
    ss.selected = SHOW_BEATS[0] if SHOW_BEATS else 1


def _start_replay() -> None:
    latest = state.latest_recording(RECORDINGS)
    if not latest:
        st.toast("No recording yet — run live once to capture one.")
        return
    rec = state.load_recording(latest)
    ss.beats, ss.mode, ss.snapshot = rec["beats"], "replay", rec["ledger_snapshot"]
    ss.selected = SHOW_BEATS[0] if SHOW_BEATS else 1


# On first load (nothing driven yet) prime the stepper from the newest recording
# so the beat buttons show results immediately; the ledger still reads live.
if not ss.beats and ss.mode == "idle":
    _latest = state.latest_recording(RECORDINGS)
    if _latest:
        try:
            ss.beats = state.load_recording(_latest)["beats"]
        except (OSError, ValueError):
            pass

# --- control bar -----------------------------------------------------------
st.title("Agent CTO — analyst + audited self-healing")
c1, c2, c3, c4, _ = st.columns([1, 1.4, 1, 1, 3])
if c1.button("▶ Run live", type="primary", disabled=ss.mode == "live"):
    _start_live()
if c2.button("⏮ Replay last good run", disabled=ss.mode == "live"):
    _start_replay()
tamper = c3.button("🔒 Tamper demo")
if c4.button("▦ All beats"):
    ss.selected = None

by_num = {int(r["beat"]): r for r in ss.beats}

nav, centre, right = st.columns([2.2, 4, 3])

# --- left rail: a button + "what it tests" per beat ------------------------
with nav:
    st.subheader("Beats")
    st.caption("Click a beat to show it. ✅ = has a result this session.")
    for b in CATALOG:
        n = b["beat"]
        mark = "✅" if n in by_num else "⚪"
        sel = "▶ " if ss.selected == n else ""
        if st.button(f"{sel}{mark} Beat {n} — {b['title']}",
                     key=f"beat-btn-{n}", use_container_width=True):
            ss.selected = n
        st.caption(b["shows"])

# --- centre: the selected beat (or the whole run) --------------------------
with centre:
    # live runs stream new beats into the JSONL; refresh ss.beats each rerun
    if ss.mode == "live" and ss.jsonl_path:
        try:
            with open(ss.jsonl_path, encoding="utf-8") as f:
                ss.beats = state.read_jsonl(f.read())
            by_num = {int(r["beat"]): r for r in ss.beats}
        except FileNotFoundError:
            pass

    if ss.selected is None:                       # "All beats" — the classic stacked view
        st.subheader("Full run")
        shown = [r for r in ss.beats if int(r["beat"]) in SHOW_BEATS]
        if not shown:
            st.info("No run loaded yet. Click ▶ Run live or ⏮ Replay last good run.")
        for rec in shown:
            _beat_card(rec)
            st.divider()
    else:
        rec = by_num.get(ss.selected)
        cat = next((b for b in CATALOG if b["beat"] == ss.selected), None)
        if rec:
            _beat_card(rec)
        elif ss.mode == "live":
            _pending_card(cat or {"beat": ss.selected, "title": "", "shows": "", "question": ""})
            st.caption("⏳ live run in progress — this beat will fill in when it lands.")
        elif cat:
            _pending_card(cat)

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
        st.dataframe(rows, use_container_width=True, hide_index=True, height=560)
    except Exception as e:  # noqa: BLE001
        st.warning(f"ledger unavailable ({e}). Is the cluster up?")

# live view refreshes itself while a run is in flight
if ss.mode == "live":
    import time
    time.sleep(1.5)
    st.rerun()
