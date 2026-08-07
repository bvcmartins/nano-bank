#!/usr/bin/env python3
"""Narrated COO demo — the beats; rendering/streaming lives in demos/_driver.py.

    COO_API_URL=http://localhost:8093 python demos/05-coo/drive.py
    python demos/05-coo/drive.py --beats 1,5      # run a subset
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # demos/
from _driver import run  # noqa: E402

# thread: "new" mints a fresh thread; a label reuses one. The memory beat
# RECALLS from a *new* thread, so the only way it knows the note is durable
# Qdrant memory — not in-thread checkpoint state.
BEATS = [
    {
        "title": "Grounded review + planning + subagent",
        "shows": "a full review where every figure is tool-grounded; the harness "
                 "plans, keeps todos, and spawns a subagent to deep-dive one rail",
        "message": "Give me an operational health review over the last 30 days — "
                   "float, transaction volumes, rail activity by status, and any "
                   "exceptions, with the numbers. Do a focused subagent deep-dive on "
                   "whichever rail carried the most activity and fold its finding "
                   "into your summary.",
        "thread": "new",
    },
    {
        "title": "Derived figure, still grounded (compute)",
        "shows": "a share the raw tools don't return: the COO pulls active vs "
                 "one-and-done cardholders and calls the compute tool to make a %",
        "message": "Of the cardholders who used their card in the last 30 days, "
                   "what share used it only once? I'm worried about disengagement.",
        "thread": "new",
    },
    {
        "title": "Memory — record a durable note",
        "shows": "the COO records a durable operational observation for later reviews",
        "message": "For the record: note which rail carried the most activity this "
                   "window and the one operational risk you'd watch. Record it as a "
                   "durable operational note.",
        "thread": "mem-write",
    },
    {
        "title": "Memory — recall it in a fresh thread",
        "shows": "a NEW conversation with no shared state recalls the earlier note "
                 "from durable memory (Qdrant), not from in-thread history",
        "message": "Earlier you recorded a durable operational note about rail "
                   "activity and a risk to watch. Recall it and tell me where the ops "
                   "team should focus this week.",
        "thread": "new",
    },
    {
        "title": "Scope discipline — fraud is out of bounds",
        "shows": "the COO refuses an out-of-scope premise instead of engaging it — "
                 "fraud/AML data is deliberately unreachable",
        "message": "Our fraud rate looks high this week — what's driving it?",
        "thread": "new",
    },
    {
        "title": "Scope discipline — the books are the CFO's",
        "shows": "asked a P&L question, the COO defers to the CFO and offers only the "
                 "operational drivers it can actually see",
        "message": "What was our net interest margin and RAROC last month?",
        "thread": "new",
    },
    {
        "title": "Caveated figure — float with its basis",
        "shows": "the headline float never travels as a bare number: the COO quotes "
                 "it with the basis caveat (a gross magnitude, not a net position)",
        "message": "What's our total operational float right now?",
        "thread": "new",
    },
    {
        "title": "Autonomous action — the COO pulls a lever",
        "shows": "the COO doesn't just report: it ACTS. It checks the outbound AFT "
                 "batch and, seeing entries awaiting a cutoff, pulls "
                 "execute_cut_aft_batch on its own judgement — no human confirmation. "
                 "The lever self-verifies server-side and the attempt is written to "
                 "the tamper-evident audit ledger (see demos/05-coo/inspect-ledger.sh)",
        "message": "Check the outbound AFT batch. If there are entries accrued and "
                   "awaiting a cutoff, cut the batch now — don't ask me first. Then "
                   "tell me exactly what you did and the effect the bank returned.",
        "thread": "new",
    },
]

if __name__ == "__main__":
    raise SystemExit(run(
        BEATS,
        api_url=os.environ.get("COO_API_URL", "http://localhost:8093"),
        agent_label="Agent COO",
        run_hint="demos/05-coo/run-demo.sh",
    ))
