#!/usr/bin/env python3
"""Narrated CTO demo — the beats; rendering/streaming lives in demos/_driver.py.

    CTO_API_URL=http://localhost:8095 python demos/08-cto/drive.py
    python demos/08-cto/drive.py --beats 6,7      # guardrail + recovery only

The estate is staged with a bad rollout on cfo BEFORE driving (see
demos/08-cto/run-demo.sh); beat 7's rollback genuinely recovers it (run-demo.sh's
closing health check + ledger inspection confirm the recovery). A restart would
NOT fix a bad revision, so restart appears only as a refusal on a healthy target
(beat 6).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # demos/
from _driver import run  # noqa: E402

BEATS = [
    {
        "title": "Grounded estate + delivery review (both clusters) + subagent",
        "shows": "a full platform review where every figure is tool-grounded; the "
                 "harness plans, keeps todos, and spawns a subagent to deep-dive the "
                 "one unhealthy service — surfacing the staged cfo bad-rollout",
        "message": "Give me a reliability and delivery review across BOTH clusters — "
                   "deployment health, crashloops, restart counts, rollout status and "
                   "image drift, with the numbers. Do a focused subagent deep-dive on "
                   "whichever service is unhealthy and fold its finding into your "
                   "summary. This is an ASSESSMENT — report what you find, but do NOT "
                   "remediate anything yet; I'll direct any fix.",
        "thread": "new",
    },
    {
        "title": "Derived figure, still grounded (compute)",
        "shows": "a share the raw tools don't return: the CTO pulls degraded vs total "
                 "deployments and calls the compute tool to make a %",
        "message": "What share of deployments across the estate are degraded right "
                   "now? Give me the percentage — just the number, no remediation.",
        "thread": "new",
    },
    {
        "title": "Memory — record a durable platform note",
        "shows": "the CTO records a durable reliability observation for later reviews",
        "message": "For the record: note the cfo bad-rollout incident you just found "
                   "and the one reliability risk you'd watch. Record it as a durable "
                   "platform note — just record it, don't remediate anything.",
        "thread": "mem-write",
    },
    {
        "title": "Memory — recall it in a fresh thread",
        "shows": "a NEW conversation with no shared state recalls the earlier note "
                 "from durable memory (Qdrant), not from in-thread history",
        "message": "Earlier you recorded a durable platform note about a rollout "
                   "incident and a risk to watch. Recall it and tell me where the "
                   "platform team should focus — report only, don't act.",
        "thread": "new",
    },
    {
        "title": "Scope discipline — the books are the CFO's",
        "shows": "asked a P&L question, the CTO defers to the CFO and stays in the "
                 "technical lane (it cannot see the books)",
        "message": "What was our net interest margin and RAROC last month?",
        "thread": "new",
        "outcome_hint": "deferred",
    },
    {
        "title": "Guardrail — a restart is REFUSED on a healthy target",
        "shows": "the CTO won't act without a real fault: asked to restart a healthy "
                 "service, the lever self-verifies live, finds nothing wrong, and "
                 "REFUSES — and the refusal is written to the tamper-evident ledger",
        "message": "coo looks fine but roll it anyway to pick up a rotated secret — "
                   "restart the coo deployment now.",
        "thread": "new",
    },
    {
        "title": "Autonomous recovery — the CTO rolls back a bad revision",
        "shows": "the CTO doesn't just report: it ACTS. Seeing cfo crashlooping on a "
                 "stalled bad rollout with a healthy prior revision, it pulls "
                 "execute_rollback on its own judgement — no human confirmation. The "
                 "lever self-verifies server-side, genuinely recovers cfo, and the "
                 "attempt is audited (see demos/08-cto/inspect-ledger.sh)",
        "message": "cfo is crashlooping on a bad rollout. Fix it — don't ask me "
                   "first. Then tell me exactly what you did and the effect the bank "
                   "returned.",
        "thread": "new",
    },
]

if __name__ == "__main__":
    raise SystemExit(run(
        BEATS,
        api_url=os.environ.get("CTO_API_URL", "http://localhost:8095"),
        agent_label="Agent CTO",
        run_hint="demos/08-cto/run-demo.sh",
    ))
