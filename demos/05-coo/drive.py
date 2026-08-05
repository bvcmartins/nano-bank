#!/usr/bin/env python3
"""Narrated COO demo driver.

Drives the COO `/ask` endpoint through a scripted arc and pretty-prints, per
beat: the question, the answer, the verification badge, and the harness trace
highlights (plan / todos / subagent / memory). It is a *demo* — it never seeds
and never mutates the bank; it only asks the read-only COO questions.

    COO_API_URL=http://localhost:8093 python demos/05-coo/drive.py
    python demos/05-coo/drive.py --beats 1,5      # run a subset

The stack (bank + operations MCP + COO, and Qdrant for the memory beat) must
already be up — see demos/05-coo/run-demo.sh, which brings it up and calls this.
"""
from __future__ import annotations
import argparse
import os
import sys
import textwrap

import httpx

# demos/05-coo/drive.py -> repo root, so the pure `coo.trace_view` helper imports
# even when this script is run standalone (it pulls in no heavy deps).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from coo.trace_view import extract_highlights  # noqa: E402

API = os.environ.get("COO_API_URL", "http://localhost:8093")

# --- ANSI (no dependency; degrade to plain if not a tty) -------------------
_TTY = sys.stdout.isatty()


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _TTY else s


def bold(s):   return _c("1", s)
def dim(s):    return _c("2", s)
def cyan(s):   return _c("36", s)
def green(s):  return _c("32", s)
def yellow(s): return _c("33", s)
def red(s):    return _c("31", s)


def verification_line(veri: dict | None) -> str:
    if not veri:
        return dim("(no verification)")
    grounded = veri.get("grounded", [])
    ungrounded = veri.get("ungrounded", [])
    claims = veri.get("unsupported_claims", [])
    revised = veri.get("revised", False)
    if ungrounded or claims:
        bits = []
        if ungrounded:
            bits.append(f"ungrounded: {', '.join(ungrounded)}")
        if claims:
            bits.append(f"unsupported: {', '.join(claims)}")
        tail = " (after one revise pass)" if revised else ""
        return red("⚠ " + "; ".join(bits) + tail)
    tail = green(" ✓ revised once, now clean") if revised else ""
    return green(f"✓ all {len(grounded)} figure(s) tool-grounded") + tail


# --- rendering --------------------------------------------------------------
def _wrap(s: str, indent: str = "    ") -> str:
    out = []
    for para in (s or "").split("\n"):
        out.append(textwrap.fill(para, width=96, initial_indent=indent,
                                  subsequent_indent=indent) if para.strip() else "")
    return "\n".join(out)


def render_beat(n: int, beat: dict, resp: dict) -> None:
    print()
    print(bold(cyan(f"━━ Beat {n}: {beat['title']} " + "━" * max(0, 60 - len(beat['title'])))))
    print(dim("   shows: " + beat["shows"]))
    print(bold("\n  Q: ") + beat["message"])
    answer = resp.get("answer", "(no answer)")
    print(bold("\n  COO:"))
    print(_wrap(answer))
    print(bold("\n  verify: ") + verification_line(resp.get("verification")))

    h = extract_highlights(resp.get("trace", []))
    line = []
    if h["plan"]:
        line.append(green(f"planned ({len(h['plan'])}×)"))
    if h["todos"]:
        line.append(green(f"todos ({len(h['todos'])}×)"))
    if h["tools"]:
        tools = ", ".join(f"{k}×{v}" if v > 1 else k for k, v in h["tools"].items())
        line.append("tools: " + tools)
    if h["subagents"]:
        line.append(yellow(f"subagent×{len(h['subagents'])}"))
    if h["recalls"]:
        line.append(yellow(f"memory recall×{h['recalls']}"))
    if h["records"]:
        line.append(yellow(f"memory record×{h['records']}"))
    if h["compactions"]:
        line.append(dim(f"compaction×{len(h['compactions'])}"))
    if line:
        print(bold("  harness: ") + " · ".join(line))
    for sa in h["subagents"]:
        tools = ", ".join(sa["tools"])
        print(dim(f"    ↳ subagent (depth {sa['depth']}, tools=[{tools}], "
                  f"{sa['chars']} chars): {sa['task'][:80]}"))


# --- the arc ----------------------------------------------------------------
# thread: "new" mints a fresh thread; a label reuses one so a later beat shares
# the earlier beat's thread. The memory beat deliberately RECALLS from a *new*
# thread, so the only way it can know the note is durable Qdrant memory — not
# in-thread checkpoint state.
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
        "title": "Verifier catches an ungrounded figure",
        "shows": "asked for a derived number no tool returns (an average); the "
                 "deterministic verifier flags it and forces one revise pass, or the "
                 "COO declines to invent it",
        "message": "Over the last 30 days, what was the average dollar size of a "
                   "single card purchase? Give me just that one number.",
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
]


def ask(message: str, thread_id: str | None) -> dict:
    r = httpx.post(f"{API}/ask", json={"message": message, "thread_id": thread_id},
                   timeout=600)
    r.raise_for_status()
    return r.json()


def health_ok() -> bool:
    try:
        r = httpx.get(f"{API}/health", timeout=15)
        return r.status_code == 200 and r.json().get("status") == "ok"
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--beats", help="comma-separated 1-based beat numbers (default all)")
    args = ap.parse_args()

    which = (range(1, len(BEATS) + 1) if not args.beats
             else [int(x) for x in args.beats.split(",")])

    print(bold(f"nano-bank Agent COO — narrated demo  ({API})"))
    if not health_ok():
        print(red(f"COO /health not OK at {API}. Bring the stack up first "
                  "(demos/05-coo/run-demo.sh) or set COO_API_URL."))
        return 1

    threads: dict[str, str] = {}   # label -> thread_id
    for n in which:
        beat = BEATS[n - 1]
        label = beat["thread"]
        tid = None if label == "new" else threads.get(label)
        try:
            resp = ask(beat["message"], tid)
        except Exception as e:  # noqa: BLE001
            print(red(f"\nBeat {n} failed: {e}"))
            return 1
        if label != "new":
            threads[label] = resp.get("thread_id")
        render_beat(n, beat, resp)

    print(green(bold("\n✓ demo complete\n")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
