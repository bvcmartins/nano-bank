"""nano-bank C-suite operations console — a single pane where you drive an
autonomous agent (COO or CFO) and watch its work AND the tamper-evident audit
trail side by side.

Left: chat — preset demo beats or free text; streams the answer, the grounding
badge, and the harness trace (plan · todos · tools · subagent · memory).
Right: the live agent-action ledger — hash-chained, immutable, spanning every
agent. When the COO pulls a lever, you watch the new row land and the chain stay
INTACT.

    demos/07-suite-console/run.sh        # forwards + seed + launch
    # or, against an already-forwarded stack:
    streamlit run demos/07-suite-console/app.py
"""
from __future__ import annotations
import importlib.util
import json
import os
import subprocess
import sys

import httpx
import streamlit as st

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)               # import csuite.*
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)               # import ledger

from csuite.console_ui import esc, _snip, _live_line, render_run_tree  # noqa: E402
from csuite.trace_view import extract_highlights, to_steps  # noqa: E402
from csuite.verifier import badge  # noqa: E402
import ledger as ledger_reader  # noqa: E402

BANK_URL = os.environ.get("BANK_API_URL", "http://localhost:8081")
AGENTS = {
    "COO": {"icon": "🏭", "url": os.environ.get("COO_API_URL", "http://localhost:8093"),
            "beats": os.path.join(ROOT, "demos/05-coo/drive.py"), "can_seed": True,
            "blurb": "operations — reads the rails, pulls autonomous levers"},
    "CFO": {"icon": "📒", "url": os.environ.get("CFO_API_URL", "http://localhost:8089"),
            "beats": os.path.join(ROOT, "demos/06-cfo/drive.py"), "can_seed": False,
            "blurb": "the books — closes periods, reports NIM / RAROC"},
}


@st.cache_data(show_spinner=False)
def load_beats(path: str) -> list[dict]:
    """Import a demo drive.py and lift its BEATS (skips the __main__ block)."""
    spec = importlib.util.spec_from_file_location(f"beats_{os.path.basename(os.path.dirname(path))}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return list(getattr(mod, "BEATS", []))


def health(url: str) -> bool:
    try:
        r = httpx.get(f"{url}/health", timeout=4)
        if r.status_code != 200:
            return False
        s = r.json().get("status")
        return s in ("ok", "healthy")
    except Exception:  # noqa: BLE001
        return False


def stream_ask(url: str, message: str, thread_id):
    """Stream one /ask/stream turn into a live status box; return the final
    (answer, verification, trace, thread_id)."""
    answer, veri, trace, new_thread = "(no answer)", None, [], thread_id
    try:
        with st.status("Working…", expanded=True) as live, \
                httpx.stream("POST", f"{url}/ask/stream",
                             json={"message": message, "thread_id": thread_id},
                             timeout=600) as r:
            r.raise_for_status()
            for raw in r.iter_lines():
                if not raw:
                    continue
                msg = json.loads(raw)
                if "event" in msg:
                    ev = msg["event"]
                    if ev.get("kind") == "start":
                        live.update(label=("🧠 model reasoning…" if ev.get("of") == "model"
                                           else f"🔧 {ev.get('name')}…"))
                        continue
                    for step in to_steps([ev]):
                        live.update(label=f"{step['icon']} {step['title']}")
                        live.write(_live_line(step))
                        says = step["body"].get("says") or step["body"].get("thinking")
                        if step["kind"] == "model" and says:
                            live.markdown("> " + esc(_snip(says, 300)))
                elif "final" in msg:
                    f = msg["final"]
                    answer = f.get("answer", "(no answer)")
                    veri = f.get("verification")
                    trace = f.get("trace", [])
                    new_thread = f.get("thread_id")
            live.update(label="Done", state="complete", expanded=False)
    except Exception as e:  # noqa: BLE001
        answer = f"⚠️ agent unreachable: {e}"
    return answer, veri, trace, new_thread


def render_answer(answer: str, veri, trace) -> None:
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
            chips.append("🔧 " + ", ".join(f"{k}×{v}" if v > 1 else k
                                           for k, v in h["tools"].items()))
        if h["subagents"]:
            chips.append(f"🧵 subagent ×{len(h['subagents'])}")
        if h["recalls"]:
            chips.append(f"🧠 recall ×{h['recalls']}")
        if h["records"]:
            chips.append(f"💾 record ×{h['records']}")
        if chips:
            st.caption(" · ".join(chips))
        render_run_tree(trace, veri)


_OUTCOME_EMOJI = {"executed": "🟢 executed", "refused": "⛔ refused", "—": "·"}


def render_ledger() -> None:
    st.subheader("🔗 Agent-action ledger")
    st.caption("hash-chained · append-only · immutable · out of bounds for the agents")
    if st.button("🔄 Refresh ledger", use_container_width=True):
        pass  # a click just triggers a rerun, which re-fetches below
    data = ledger_reader.fetch()
    if not data["ok"]:
        st.error(f"cannot read ledger: {data['error']}")
        return
    if data["intact"] is True:
        st.success(f"✅ chain INTACT — {len(data['rows'])} entries, every link verified")
    elif data["intact"] is False:
        st.error(f"❌ chain BROKEN at seq {data['broken_seq']}")

    # newest first; show the linked hashes so the chain is legible.
    rows = list(reversed(data["rows"]))
    head = "| seq | time | actor | action | outcome | detail | prev→hash |\n" \
           "|--:|:--|:--|:--|:--|:--|:--|\n"
    body = ""
    for r in rows:
        outcome = _OUTCOME_EMOJI.get(r["outcome"], esc(r["outcome"]))
        detail = esc(_snip(r["detail"], 26))
        actor = f"**{r['actor'].upper()}**"
        body += (f"| {r['seq']} | {r['ts']} | {actor} | `{r['action']}` | {outcome} "
                 f"| {detail} | `{r['prev']}`→`{r['hash']}` |\n")
    st.markdown(head + body)


def main() -> None:
    st.set_page_config(page_title="nano-bank C-suite console", page_icon="🏦",
                       layout="wide")

    ss = st.session_state
    ss.setdefault("threads", {})       # agent -> current thread_id
    ss.setdefault("hist", {})          # agent -> [(role, text)]
    ss.setdefault("last", {})          # agent -> (veri, trace)

    pending = None  # (message, reset_thread)
    with st.sidebar:
        st.title("🏦 C-suite console")
        agent = st.radio("Agent", list(AGENTS), format_func=lambda a: f"{AGENTS[a]['icon']} {a}",
                         horizontal=True)
        cfg = AGENTS[agent]
        st.caption(cfg["blurb"])

        bank_ok = health(BANK_URL)
        agent_ok = health(cfg["url"])
        st.markdown(f"bank-api {'🟢' if bank_ok else '🔴'} · "
                    f"{agent} {'🟢' if agent_ok else '🔴'}")

        if st.button("🧵 New conversation", use_container_width=True,
                     help="Clear this agent's chat and forget the thread, so "
                          "memory-recall must come from durable storage, not this chat "
                          "(the ledger is untouched)"):
            ss.threads[agent] = None
            ss.hist[agent] = []          # wipe the on-screen transcript
            ss.last.pop(agent, None)     # and the last turn's badge / run-tree

        if cfg["can_seed"]:
            if st.button("🌱 Seed open AFT batch", use_container_width=True,
                         help="Leave one open outbound AFT batch so the COO's "
                              "cut-batch lever has a real action to take"):
                out = subprocess.run([sys.executable,
                                      os.path.join(ROOT, "demos/05-coo/seed_open_aft.py")],
                                     capture_output=True, text=True,
                                     env={**os.environ, "API_URL": BANK_URL}, timeout=40)
                (st.success if out.returncode == 0 else st.error)(
                    (out.stdout or out.stderr).strip() or "seeded")

        st.divider()
        st.caption("preset beats — click to run")
        beats = load_beats(cfg["beats"])
        for i, b in enumerate(beats):
            if st.button(f"{i+1}. {b['title']}", key=f"beat_{agent}_{i}",
                         use_container_width=True, help=b.get("shows", "")):
                pending = (b["message"], b.get("thread", "new") == "new")

    typed = st.chat_input(f"Ask the {agent} anything…")
    if pending:
        message, reset = pending
    elif typed:
        message, reset = typed, False
    else:
        message, reset = None, False

    chat_col, ledger_col = st.columns([3, 2], gap="large")

    with chat_col:
        st.subheader(f"{cfg['icon']} Agent {agent}")
        for role, text in ss.hist.get(agent, []):
            with st.chat_message(role):
                st.markdown(esc(text))
        # re-show the most recent turn's badge + run tree (survives reruns)
        if not message and agent in ss.last:
            veri, trace = ss.last[agent]
            render_answer_tail(veri, trace)

        if message:
            ss.hist.setdefault(agent, []).append(("user", message))
            with st.chat_message("user"):
                st.markdown(esc(message))
            thread = None if reset else ss.threads.get(agent)
            with st.chat_message("assistant"):
                answer, veri, trace, new_thread = stream_ask(cfg["url"], message, thread)
                render_answer(answer, veri, trace)
            ss.hist[agent].append(("assistant", answer))
            ss.threads[agent] = new_thread
            ss.last[agent] = (veri, trace)

    with ledger_col:
        render_ledger()


def render_answer_tail(veri, trace) -> None:
    """Badge + run tree for the last turn, without repeating the answer text
    (the answer is already in the chat history above)."""
    if veri is not None:
        line = badge(veri)
        (st.warning if (veri.get("ungrounded") or veri.get("unsupported_claims"))
         else st.caption)(line)
    if trace:
        render_run_tree(trace, veri)


if __name__ == "__main__":
    main()
