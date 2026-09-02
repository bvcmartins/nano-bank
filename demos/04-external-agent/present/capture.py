#!/usr/bin/env python3
"""Capture one live external-agent run as a JSONL recording, for
gateway_server.py's headless "Capture live" button and build_gateway.py.

Demo 4 has no run-demo.sh/drive.py like the officer demos (05-10) -- app.py
runs the agent in-process directly, which works fine interactively but not
from a headless server with no Streamlit session. This script is that
driver, adapted to ExternalAgent's own shape (plan -> act* -> message* ->
result), not a multi-beat board consult.

One run here is a handful of HTTP calls and returns in well under a second
-- unlike the officer demos' multi-minute debates, events land in the JSONL
file in one batch at the end, not progressively.

    python demos/04-external-agent/present/capture.py --emit-jsonl /tmp/x.jsonl
    python demos/04-external-agent/present/capture.py --emit-jsonl /tmp/x.jsonl \
        --no-seed --instruction "Pay my $50 Epcor utility bill..."
"""
from __future__ import annotations
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

import requests
from agent.external_agent.agent import ExternalAgent, GatewayHTTP

DEFAULT_INSTRUCTION = (
    "Pay my $50 Epcor utility bill, then ask my personal manager to confirm the payment "
    "went through and explain what a loan would look like if I want to buy a $28,000 car."
)


def _llm():
    from agent import model_factory as mf
    from agent.config import Settings
    s = Settings.from_env()
    mf.init_models(s)
    return mf.llm("fast", temperature=0.0)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--emit-jsonl", required=True)
    p.add_argument("--instruction", default=DEFAULT_INSTRUCTION)
    p.add_argument("--no-seed", action="store_true",
                   help="reuse an already-seeded mandate instead of re-seeding")
    args = p.parse_args()

    base = os.environ.get("DEMO_BRANCH_BASE", "http://localhost:8086").rstrip("/")
    token = os.environ.get("AGENT_GATEWAY_TOKEN", "")
    hdr = {"Authorization": f"Bearer {token}"}

    if not args.no_seed:
        requests.post(f"{base}/agent-gateway/demo-seed", headers=hdr, timeout=30)

    agent = ExternalAgent(gateway=GatewayHTTP(base, token), llm=_llm())
    events = agent.run(args.instruction)

    with open(args.emit_jsonl, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
            f.flush()

    print(f"captured {len(events)} events -> {args.emit_jsonl}")


if __name__ == "__main__":
    main()
