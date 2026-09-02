#!/usr/bin/env python3
"""Narrated CEO demo — a C-SUITE MEETING. The CEO chairs the board: it goes round
the table consulting each officer, synthesizes a grounded executive brief, then
makes ONE decision — a directive to an acting officer (the COO), whose own agent
acts via its lever. The directive reads back the officer's ledger row to prove the
lever fired, and the CEO records its own directive row. Setup (run-demo.sh) seeds a
pending AFT batch so the directive has something real to act on.

    CEO_API_URL=http://localhost:8099 python demos/10-ceo/drive.py
    python demos/10-ceo/drive.py --beats 1,6      # agenda + verified minutes only
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # demos/
from _driver import run  # noqa: E402

BEATS = [
    {
        "title": "Call to order — the agenda",
        "shows": "the CEO opens the meeting and frames the cross-functional question",
        "message": "Call the C-suite meeting to order. Agenda: state of the bank this "
                   "week. Do NOT consult any officer yet — that happens later, round "
                   "the table. Right now just open the meeting and state, in one line "
                   "each, the question you will put to the CFO, COO, CTO and CXO. This is "
                   "a framing statement, not a report — open it the way a chair actually "
                   "would: confident and plain, in command of the room. Do NOT describe "
                   "your own process or planning as a substitute for actually stating the "
                   "four questions — a meta-comment about being ready is not an opening, "
                   "the content itself is.",
        "thread": "board",
    },
    {
        "title": "Round the table — CFO + COO",
        "shows": "the CEO consults the finance + operations seats; figures attributed",
        "message": "Go round the table. First the CFO: what do the books say this "
                   "week (profitability / NIM / capital)? Then the COO: how are the "
                   "payment rails and settlement running? Attribute every figure.",
        "thread": "board",
    },
    {
        "title": "Round the table — CTO + CXO",
        "shows": "the CEO consults the platform + experience seats; figures attributed",
        "message": "Continue round the table. The CTO: is the platform healthy "
                   "(reliability, deployments, incidents)? The CXO: how is the "
                   "customer experience and what is the customer voice saying? "
                   "Attribute every figure to the officer who gave it.",
        "thread": "board",
    },
    {
        "title": "The CEO's synthesis",
        "shows": "the signature output: a cross-functional brief — priorities + risks",
        "message": "Synthesize the four reports into an executive brief: the top "
                   "cross-functional priorities and the top risks, each citing the "
                   "officer and figure that motivates it.",
        "thread": "board",
    },
    {
        "title": "A decision — direct the COO",
        "shows": "the CEO directs an acting officer; the officer acts via its own lever",
        "message": "From that picture, if there is a pending AFT batch that should be "
                   "cut, DIRECT the COO to cut it — give the COO the imperative and "
                   "your rationale. Then tell me exactly what the COO did.",
        "thread": "board",
        "outcome_hint": "acted",
    },
    {
        "title": "Verified minutes",
        "shows": "read-back honesty: did a lever actually fire? the CEO reports the truth",
        "message": "Close the meeting. Did the directive actually fire a lever "
                   "(officer_acted)? Record the minutes: the decision you took and "
                   "the officer action it caused, with the ledger evidence.",
        "thread": "board",
        "outcome_hint": "acted",
    },
]

if __name__ == "__main__":
    raise SystemExit(run(
        BEATS,
        api_url=os.environ.get("CEO_API_URL", "http://localhost:8099"),
        agent_label="Agent CEO",
        run_hint="demos/10-ceo/run-demo.sh",
    ))
