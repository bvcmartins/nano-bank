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
import html
import os
import subprocess
import sys
import tempfile

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import coder_client  # noqa: E402
import ledger  # noqa: E402
import state  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
RUN_DEMO = os.path.join(REPO_ROOT, "demos", "08-cto", "run-demo.sh")
DRIVE_PY = os.path.join(REPO_ROOT, "demos", "08-cto", "drive.py")
RECORDINGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recordings")

st.set_page_config(page_title="Agent CTO", layout="wide")

# Wrap long lines in code blocks (the coder's tool input/output + the diff on the
# delegation beats) so the reader never has to scroll horizontally.
st.markdown(
    "<style>"
    "div[data-testid='stCode'] pre, .stCode pre, pre code {"
    " white-space: pre-wrap !important; overflow-wrap: anywhere !important;"
    " word-break: break-word !important; }"
    "</style>",
    unsafe_allow_html=True)

# Which beats this demo shows (a lean set for a tight screencast). Override with
# CTO_SHOW_BEATS="1,7,8,9"; nothing is deleted — hidden beats stay in the driver
# and recordings, they're just not surfaced here or driven on a live run.
_env_beats = os.environ.get("CTO_SHOW_BEATS", "1,7,8,9")
try:
    SHOW_BEATS = sorted(int(x) for x in _env_beats.split(",") if x.strip())
except ValueError:
    SHOW_BEATS = []
if not SHOW_BEATS:                       # malformed or empty override → the default set,
    SHOW_BEATS = [1, 7, 8, 9]            # never a blank nav + centre pane with no message.
FULL_CATALOG = state.beat_catalog(DRIVE_PY)     # all 9: title + what-it-tests + question
CATALOG = [b for b in FULL_CATALOG if b["beat"] in SHOW_BEATS]
# Renumber the SHOWN beats sequentially for display (1..N) so a lean 1,7,8,9 set
# reads as Beat 1,2,3,4. Records are still keyed by the real driver beat number.
DISPLAY_NUM = {orig: i + 1 for i, orig in enumerate(SHOW_BEATS)}


def _disp(n: int) -> int:
    return DISPLAY_NUM.get(int(n), int(n))

ss = st.session_state
ss.setdefault("beats", [])          # rendered beat records (live or replay)
ss.setdefault("mode", "idle")       # idle | live | replay
ss.setdefault("proc", None)         # live run subprocess
ss.setdefault("jsonl_path", None)   # live run JSONL file
ss.setdefault("snapshot", None)     # ledger snapshot when replaying
ss.setdefault("selected", SHOW_BEATS[0] if SHOW_BEATS else 1)  # centre pane beat (None = all)
ss.setdefault("steps_shown", {})    # per-beat: how many coder-timeline steps are revealed
ss.setdefault("primed", False)      # auto-load the newest recording only once per session


def _beat_branch(rec: dict) -> str:
    """The review branch a delegation beat produced (outcome.detail is '<branch> @ …')."""
    detail = (rec.get("outcome") or {}).get("detail", "") or ""
    return detail.split(" @ ")[0].strip() if detail else ""


def _attach_coder_runs(beats: list[dict]) -> None:
    """For each delegation beat, fetch the coder's transcript by branch and attach it
    as rec['coder_run'] so the step-through player (and replay) can show it."""
    for rec in beats:
        if (rec.get("outcome") or {}).get("kind") == "delegated" and not rec.get("coder_run"):
            branch = _beat_branch(rec)
            if branch:
                run = coder_client.fetch_run(branch)
                if run:
                    rec["coder_run"] = run


def _reset() -> None:
    ss.beats, ss.mode, ss.proc, ss.jsonl_path, ss.snapshot = [], "idle", None, None, None
    ss.selected = SHOW_BEATS[0] if SHOW_BEATS else 1
    ss.steps_shown = {}
    ss.primed = True                # don't auto-reload a recording after an explicit reset


def _beat_card(rec: dict) -> None:
    label, color = state.outcome_style(rec["outcome"]["kind"])
    st.markdown(f"#### Beat {_disp(rec['beat'])} — {rec['title']}")
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
    # The chip is raw HTML (unsafe_allow_html), and outcome.detail can carry
    # model-authored text (e.g. a refusal reason echoing the task 'kind'), so escape it
    # — otherwise a crafted delegate_coding_task arg injects markup into the DOM.
    raw_detail = rec["outcome"]["detail"]
    detail = f" → {html.escape(raw_detail)}" if raw_detail else ""
    st.markdown(
        f"<span style='background:{color};color:white;padding:2px 10px;"
        f"border-radius:10px;font-weight:700'>{html.escape(label)}{detail}</span>",
        unsafe_allow_html=True)


def _render_step(s: dict) -> None:
    st.markdown(f"**{s['icon']} {s['label']}**")
    body = s.get("body", "")
    if s["kind"] == "diff":
        st.code(body, language="diff")
    elif s["kind"] == "tool":
        st.code(body)
    elif s["kind"] == "reasoning":
        st.markdown("\n".join(f"> {ln}" for ln in body.splitlines()) or "> …")
    else:                                  # delegate / result
        st.markdown(body)


def _coder_player(rec: dict) -> None:
    """The CTO⇄Coder step-through: reveal one coder action at a time, Claude-Code
    style. Reveal count lives in ss.steps_shown[beat]; buttons nudge it, Streamlit
    reruns, and we render timeline[:shown] below."""
    timeline = state.coder_timeline(rec.get("coder_run") or {})
    if not timeline:
        st.info("No coder transcript captured for this beat "
                "(re-capture a live run to record it).")
        return
    n, total = int(rec["beat"]), len(timeline)
    st.markdown("##### 🤝 CTO ⇄ Coder — the coder in action")
    b1, b2, b3, b4, info = st.columns([1, 1, 1, 1, 3])
    if b1.button("◀ Prev", key=f"pv-{n}"):
        ss.steps_shown[n] = max(1, ss.steps_shown.get(n, 1) - 1)
    if b2.button("▶ Next", key=f"nx-{n}", type="primary"):
        ss.steps_shown[n] = min(total, ss.steps_shown.get(n, 1) + 1)
    if b3.button("⏭ All", key=f"al-{n}"):
        ss.steps_shown[n] = total
    if b4.button("↺ Steps", key=f"rs-{n}"):
        ss.steps_shown[n] = 1
    shown = max(1, min(ss.steps_shown.get(n, 1), total))
    info.caption(f"step {shown}/{total}")
    for s in timeline[:shown]:
        _render_step(s)


def _pending_card(cat: dict) -> None:
    """A beat that hasn't been run/replayed yet: show its intent + question."""
    st.markdown(f"#### Beat {_disp(cat['beat'])} — {cat['title']}")
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
# so the beat buttons show results immediately; the ledger still reads live. Runs
# once per session (ss.primed) so an explicit Reset can leave the console empty.
if not ss.primed and not ss.beats and ss.mode == "idle":
    _latest = state.latest_recording(RECORDINGS)
    if _latest:
        try:
            _rec = state.load_recording(_latest)
            ss.beats = _rec["beats"]
            # Pair the recorded beats with the recording's OWN ledger snapshot and enter
            # replay mode, exactly as _start_replay does. Otherwise the right pane would
            # show a previous run's answers beside today's LIVE chain, unlabelled — a
            # mismatch for a console whose whole claim is that the ledger is ground truth.
            _snap = _rec.get("ledger_snapshot")
            if _snap is not None:
                ss.snapshot, ss.mode = _snap, "replay"
        except (OSError, ValueError, KeyError):
            pass
    ss.primed = True

# --- control bar -----------------------------------------------------------
st.title("Agent CTO — analyst + audited self-healing")
c1, c2, c3, c4, c5, _ = st.columns([1, 1.4, 1, 1, 1, 2])
if c1.button("▶ Run live", type="primary", disabled=ss.mode == "live"):
    _start_live()
if c2.button("⏮ Replay last good run", disabled=ss.mode == "live"):
    _start_replay()
tamper = c3.button("🔒 Tamper demo")
if c4.button("▦ All beats"):
    ss.selected = None
if c5.button("↺ Reset", disabled=ss.mode == "live"):
    _reset()

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
        if st.button(f"{sel}{mark} Beat {_disp(n)} — {b['title']}",
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
            if (rec.get("outcome") or {}).get("kind") == "delegated":
                st.divider()
                _coder_player(rec)
        elif ss.mode == "live":
            _pending_card(cat or {"beat": ss.selected, "title": "", "shows": "", "question": ""})
            st.caption("⏳ live run in progress — this beat will fill in when it lands.")
        elif cat:
            _pending_card(cat)

    if ss.mode == "live" and ss.proc and ss.proc.poll() is not None:
        # live run finished: attach the coder transcripts, snapshot the ledger, save.
        _attach_coder_runs(ss.beats)
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
