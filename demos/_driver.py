#!/usr/bin/env python3
"""Shared narrated demo driver for the C-suite agents. A demo's drive.py defines
its BEATS and calls `run(BEATS, api_url=…, agent_label=…, run_hint=…)`. It drives
the agent's `/ask` endpoint and pretty-prints, per beat: the question, the
answer, the verification badge, and the harness trace highlights (plan / todos /
subagent / memory). A *demo* — it only asks; it never seeds or mutates."""
from __future__ import annotations
import argparse
import os
import sys
import textwrap

import httpx

# demos/_driver.py -> repo root, so the pure csuite.trace_view helper imports.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from csuite.trace_view import extract_highlights  # noqa: E402

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


def _wrap(s: str, indent: str = "    ") -> str:
    out = []
    for para in (s or "").split("\n"):
        out.append(textwrap.fill(para, width=96, initial_indent=indent,
                                  subsequent_indent=indent) if para.strip() else "")
    return "\n".join(out)


def render_beat(n: int, beat: dict, resp: dict, agent_label: str) -> None:
    print()
    print(bold(cyan(f"━━ Beat {n}: {beat['title']} " + "━" * max(0, 60 - len(beat['title'])))))
    print(dim("   shows: " + beat["shows"]))
    print(bold("\n  Q: ") + beat["message"])
    print(bold(f"\n  {agent_label}:"))
    print(_wrap(resp.get("answer", "(no answer)")))
    print(bold("\n  verify: ") + verification_line(resp.get("verification")))

    h = extract_highlights(resp.get("trace", []))
    line = []
    if h["plan"]:
        line.append(green(f"planned ({len(h['plan'])}×)"))
    if h["todos"]:
        line.append(green(f"todos ({len(h['todos'])}×)"))
    if h["tools"]:
        line.append("tools: " + ", ".join(
            f"{k}×{v}" if v > 1 else k for k, v in h["tools"].items()))
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


def run(beats: list[dict], *, api_url: str, agent_label: str, run_hint: str) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--beats", help="comma-separated 1-based beat numbers (default all)")
    args = ap.parse_args()
    which = (range(1, len(beats) + 1) if not args.beats
             else [int(x) for x in args.beats.split(",")])

    print(bold(f"nano-bank {agent_label} — narrated demo  ({api_url})"))
    try:
        r = httpx.get(f"{api_url}/health", timeout=15)
        ok = r.status_code == 200 and r.json().get("status") == "ok"
    except Exception:  # noqa: BLE001
        ok = False
    if not ok:
        print(red(f"{agent_label} /health not OK at {api_url}. Bring the stack up "
                  f"first ({run_hint}) or set the API url env."))
        return 1

    threads: dict[str, str] = {}
    for n in which:
        beat = beats[n - 1]
        label = beat["thread"]
        tid = None if label == "new" else threads.get(label)
        try:
            r = httpx.post(f"{api_url}/ask",
                           json={"message": beat["message"], "thread_id": tid},
                           timeout=600)
            r.raise_for_status()
            resp = r.json()
        except Exception as e:  # noqa: BLE001
            print(red(f"\nBeat {n} failed: {e}"))
            return 1
        if label != "new":
            threads[label] = resp.get("thread_id")
        render_beat(n, beat, resp, agent_label)

    print(green(bold("\n✓ demo complete\n")))
    return 0
