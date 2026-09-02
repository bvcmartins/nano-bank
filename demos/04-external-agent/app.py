"""External mandated agent console.

An autonomous LLM agent operates a customer's bank ONLY through the agentic
branch's /agent-gateway/*, under a customer-granted mandate (scoped, capped,
revocable). It never sees the bank. Seed a demo mandate, give a high-level
instruction, and watch the agent plan -> act (mandate-gated) -> ask the
manager, rendered live as an animated split-screen cinematic (external
agent vs. personal manager) embedded directly in this page, plus a plain
step-through view. Each run is also saved as a recording under
present/recordings/ so present/gateway.html can replay it standalone too.

Config: DEMO_BRANCH_BASE (default http://localhost:8086) + AGENT_GATEWAY_TOKEN.
The demo builds the planner LLM locally (needs OLLAMA_API_KEY).
"""
from __future__ import annotations
import os
import sys

import requests
import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "present"))
import build_gateway  # noqa: E402
import state  # noqa: E402

from agent.external_agent.agent import ExternalAgent, GatewayHTTP

BASE = os.environ.get("DEMO_BRANCH_BASE", "http://localhost:8086").rstrip("/")
TOKEN = os.environ.get("AGENT_GATEWAY_TOKEN", "")
HDR = {"Authorization": f"Bearer {TOKEN}"}
RECORDINGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "present", "recordings")
DEFAULT_INSTRUCTION = (
    "Pay my $50 Epcor utility bill, then ask my personal manager to confirm the payment "
    "went through and explain what a loan would look like if I want to buy a $28,000 car."
)

st.set_page_config(page_title="nano-bank · external agent", layout="wide")
ss = st.session_state
ss.setdefault("events", [])
ss.setdefault("selected", 0)
ss.setdefault("instr", DEFAULT_INSTRUCTION)
ss.setdefault("primed", False)

if not ss.primed and not ss.events:
    latest = state.latest_recording(RECORDINGS)
    if latest:
        try:
            ss.events = state.load_recording(latest)["events"]
        except (OSError, ValueError, KeyError):
            pass
    ss.primed = True

st.title("🛰️ nano-bank — external mandated agent")
st.caption(f"Gateway: `{BASE}/agent-gateway` · the agent's ONLY door — mandate-gated, capped, revocable")


@st.cache_resource(show_spinner=False)
def _llm():
    from agent import model_factory as mf
    from agent.config import Settings
    s = Settings.from_env()
    mf.init_models(s)
    return mf.llm("fast", temperature=0.0)


def _gw_post(path):
    return requests.post(f"{BASE}{path}", headers=HDR, timeout=180)


# --- mandate panel ----------------------------------------------------------
top = st.columns([3, 1, 1])
with top[0]:
    r = requests.get(f"{BASE}/agent-gateway/mandate", headers=HDR, timeout=30)
    if r.status_code == 200:
        m = r.json()
        st.success(f"**Mandate active** · account `{m.get('account_id','')[:8]}` "
                   f"({m.get('account_type','')}) · scopes: {', '.join(m.get('scopes', []))} "
                   f"· cap/tx: ${m.get('max_per_tx','—')} · expires {m.get('expires_at','')[:19]}")
    else:
        st.warning("No active mandate — click **Seed mandate** to register an agent + grant consent.")
with top[1]:
    if st.button("🌱 Seed mandate"):
        _gw_post("/agent-gateway/demo-seed")
        ss.events, ss.selected = [], 0
        st.rerun()
with top[2]:
    if st.button("⛔ Revoke"):
        _gw_post("/agent-gateway/revoke")
        st.rerun()

st.divider()

# --- instruction + run -------------------------------------------------------
ss.instr = st.text_area("High-level instruction to the autonomous agent", ss.instr, height=70)
c1, c2 = st.columns([1, 1])
if c1.button("▶ Run agent", type="primary"):
    try:
        agent = ExternalAgent(gateway=GatewayHTTP(BASE, TOKEN), llm=_llm())
        with st.spinner("agent planning + acting through the gateway…"):
            ss.events = agent.run(ss.instr)
        ss.selected = 0
        state.save_recording(RECORDINGS, ss.events)
    except Exception as e:  # noqa: BLE001
        st.error(f"agent run failed: {e}")
    st.rerun()
if c2.button("⏮ Replay last recording"):
    latest = state.latest_recording(RECORDINGS)
    if latest:
        ss.events = state.load_recording(latest)["events"]
        ss.selected = 0
    else:
        st.toast("No recording yet — run the agent once.")

_ICON = {"plan": "🧠", "act": "🤖", "message": "💬", "result": "✅"}


def _label(i: int, e: dict) -> str:
    kind = e["kind"]
    if kind == "act":
        return f"{_ICON[kind]} {i}. act · {e['operation']}"
    if kind == "message":
        return f"{_ICON[kind]} {i}. ask the manager"
    if kind == "plan":
        return f"{_ICON[kind]} {i}. plan"
    return f"{_ICON[kind]} {i}. done"


def _event_card(e: dict) -> None:
    kind = e["kind"]
    if kind == "plan":
        st.markdown("#### 🧠 Agent plan")
        st.caption(e["instruction"])
        return
    if kind == "act":
        left, right = st.columns(2)
        with left, st.container(border=True):
            st.markdown("🛰️ **External agent → act**")
            st.markdown(f"`{e['operation']}` {e.get('params', {})}")
        res = e.get("result", {})
        dec = res.get("decision", "?")
        label, color = state.decision_style(dec)
        with right, st.container(border=True):
            st.markdown("🏦 **Gateway → mandate check**")
            st.markdown(
                f"<span style='background:{color};color:white;padding:2px 10px;"
                f"border-radius:10px;font-weight:700'>{label}</span>", unsafe_allow_html=True)
            if dec == "pending_approval":
                st.info(f"⏸ over the daily cap — parked for the customer to approve "
                        f"(approval `{str(res.get('approval_id'))[:8]}`). Not paid yet.")
            else:
                st.write(res.get("reason") or (res.get("result") if dec == "allow" else res))
        return
    if kind == "message":
        left, right = st.columns(2)
        with left, st.container(border=True):
            st.markdown("🛰️ **External agent → asks the manager**")
            st.write(e.get("text", ""))
        with right, st.container(border=True):
            st.markdown("🏦 **Personal manager**")
            st.write(e.get("answer", ""))
            trace = e.get("trace")
            if trace:
                st.caption("trace: " + "  ·  ".join(
                    f"{'🔧' if t['kind'] == 'tool' else '🧠'}{'✅' if t.get('ok') else '❌'} "
                    f"{t['name']} {t['elapsed_ms']}ms" for t in trace))
        return
    st.success(f"✅ done — {e['steps']} step(s) completed successfully. "
               "Want to see a denial instead? Click **Revoke**, then **Run agent** again — "
               "the next act will be denied at the gateway (no active mandate).")


# --- cinematic (default) + step-through -------------------------------------
st.divider()
tab_cinematic, tab_steps = st.tabs(["🎬 Cinematic", "📋 Step-through"])
with tab_cinematic:
    if ss.events:
        st.iframe(build_gateway.render(ss.events), height=760)
    else:
        st.info("No run yet. Click ▶ Run agent.")
with tab_steps:
    nav, centre = st.columns([1.6, 5])
    with nav:
        st.subheader("Run")
        st.caption("Click a step to show it.")
        for i, e in enumerate(ss.events):
            sel = "▶ " if ss.selected == i else ""
            if st.button(f"{sel}{_label(i, e)}", key=f"ev-{i}", use_container_width=True):
                ss.selected = i
        if not ss.events:
            st.info("No run yet. Click ▶ Run agent.")
    with centre:
        if ss.events:
            idx = min(ss.selected, len(ss.events) - 1)
            _event_card(ss.events[idx])
