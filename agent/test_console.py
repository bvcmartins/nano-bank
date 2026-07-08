from __future__ import annotations
import os
import httpx
import streamlit as st

from agent.config import Settings

settings = Settings.from_env()
# In-container the api is reachable by service name; on the host it's localhost.
API = os.environ.get("MANAGER_API_URL", f"http://localhost:{settings.branch_port}")
HDR = {"Authorization": f"Bearer {settings.branch_service_token}"}

st.set_page_config(page_title="nano-bank manager — test console", layout="wide")
st.title("nano-bank personal manager — test console")

seed_col, chat_col = st.columns([1, 2])

with seed_col:
    st.subheader("Seed")
    if st.button("Seed demo (2 customers + funded account)"):
        # Seed THROUGH the api so it registers creds for the confirm path.
        r = httpx.post(f"{API}/branch/seed", headers=HDR, timeout=120)
        out = r.json()
        st.session_state["customers"] = out["customers"]
        st.success(f"seeded {len(out['customers'])} customers")
    customers = st.session_state.get("customers", [])
    cid = st.selectbox("client", [c["customer_id"] for c in customers]) if customers else \
        st.text_input("client id")
    if cid and st.button("Load snapshot"):
        r = httpx.get(f"{API}/branch/clients/{cid}/profile", headers=HDR)
        st.json(r.json())

with chat_col:
    st.subheader("Chat")
    msg = st.text_input("Ask or instruct (e.g. 'transfer 50 from <acc> to <acc>')")
    if st.button("Send") and cid and msg:
        r = httpx.post(f"{API}/branch/clients/{cid}/message",
                       json={"message": msg}, headers=HDR, timeout=120)
        data = r.json()
        st.markdown(f"**Manager:** {data.get('answer','')}")
        pa = data.get("pending_action")
        if pa:
            st.warning(f"Proposed: {pa.get('summary', pa)}")
            c1, c2 = st.columns(2)
            if c1.button("Confirm"):
                rr = httpx.post(f"{API}/branch/clients/{cid}/actions/{pa['id']}/confirm",
                                headers=HDR, timeout=120)
                st.success(rr.json())
            if c2.button("Cancel"):
                rr = httpx.post(f"{API}/branch/clients/{cid}/actions/{pa['id']}/cancel",
                                headers=HDR, timeout=120)
                st.info(rr.json())
