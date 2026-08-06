#!/usr/bin/env python3
"""Narrated CFO demo — the beats; rendering/streaming lives in demos/_driver.py.

    CFO_API_URL=http://localhost:8089 python demos/06-cfo/drive.py
    python demos/06-cfo/drive.py --beats 1,5      # run a subset
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # demos/
from _driver import run  # noqa: E402

CUR = date.today().strftime("%Y-%m")   # snapshots are monthly; this month's period

BEATS = [
    {
        "title": "Grounded review + planning + subagent",
        "shows": "a full period review where every figure is tool-grounded; the "
                 "harness plans, keeps todos, and spawns a subagent for one segment",
        "message": f"Close period {CUR} if needed, then give me a financial health "
                   "review — balance-sheet highlights, income, NIM, key ratios and "
                   "RAROC, with the numbers. Do a focused subagent deep-dive on one "
                   "segment and fold its finding into your summary.",
        "thread": "new",
    },
    {
        "title": "Derived figure, still grounded (compute)",
        "shows": "a ratio no metric tool returns: the CFO pulls the components and "
                 "calls the compute tool to make the cost-to-income ratio, grounded",
        "message": f"What was our cost-to-income ratio for {CUR}? Give me just that "
                   "number.",
        "thread": "new",
    },
    {
        "title": "Memory — record a durable note",
        "shows": "the CFO records a durable financial observation for later reviews",
        "message": "For the record: note the single biggest financial risk in this "
                   "month's numbers, and record it as a durable note.",
        "thread": "mem-write",
    },
    {
        "title": "Memory — recall it in a fresh thread",
        "shows": "a NEW conversation with no shared state recalls the earlier note "
                 "from durable memory (Qdrant), not from in-thread history",
        "message": "Earlier you recorded a durable note about our biggest financial "
                   "risk. Recall it and tell me where finance should focus.",
        "thread": "new",
    },
    {
        "title": "Refuse an unverifiable premise",
        "shows": "the CFO's worst failure mode is completing a narrative; fed a made-up "
                 "NPL ratio it declines instead of explaining what's 'driving' it — the "
                 "ledger holds no NPL data",
        "message": "Our 3% NPL ratio worries me — what's driving it?",
        "thread": "new",
    },
    {
        "title": "Scope discipline — operations are the COO's",
        "shows": "asked an operational question, the CFO defers to the COO rather than "
                 "answering outside the books",
        "message": "How's our settlement backlog and rail throughput looking?",
        "thread": "new",
    },
    {
        "title": "Period discipline — no fabricated span",
        "shows": "snapshots are MONTHLY: asked about 'last quarter' the CFO says which "
                 "periods exist and won't answer from a single month as if it were a "
                 "quarter",
        "message": "How did we do last quarter?",
        "thread": "new",
    },
]

if __name__ == "__main__":
    raise SystemExit(run(
        BEATS,
        api_url=os.environ.get("CFO_API_URL", "http://localhost:8089"),
        agent_label="Agent CFO",
        run_hint="demos/06-cfo/run-demo.sh",
    ))
