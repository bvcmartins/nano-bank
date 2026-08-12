"""Pure helpers for reading a merged COO trace (the list returned by
`trace.merge`). No dependencies — safe to import from the Streamlit console and
from the standalone demo driver alike."""
from __future__ import annotations

# Harness/plumbing tools are reported specially; everything else in a turn is a
# domain (operations) tool worth naming.
_HARNESS_TOOLS = {"write_plan", "update_plan", "write_todos",
                  "recall_memory", "record_memory", "spawn_subagent"}


def extract_highlights(trace: list[dict]) -> dict:
    """Distil a merged COO trace into the few things a viewer cares about:
    planning, todos, domain tools (with call counts), subagents, memory ops and
    context compactions."""
    plan, todos, domain_tools = [], [], []
    subagents, compactions = [], []
    recalls = records = 0
    for ev in trace:
        kind, name = ev.get("kind"), ev.get("name")
        if kind == "tool":
            if name == "write_plan":
                plan.append(ev.get("input") or "")
            elif name in ("write_todos", "update_plan"):
                todos.append(ev.get("input") or "")
            elif name == "recall_memory":
                recalls += 1
            elif name == "record_memory":
                records += 1
            elif name not in _HARNESS_TOOLS:
                domain_tools.append(name)
        elif kind == "subagent":
            subagents.append({"task": ev.get("task", ""), "tools": ev.get("tools", []),
                              "depth": ev.get("depth"), "chars": ev.get("chars")})
        elif kind == "memory_write":
            records += 1  # harness-side record events
        elif kind == "compaction":
            compactions.append({"dropped": ev.get("dropped"), "kept": ev.get("kept")})
    counts: dict[str, int] = {}
    for t in domain_tools:
        counts[t] = counts.get(t, 0) + 1
    return {"plan": plan, "todos": todos, "tools": counts,
            "subagents": subagents, "recalls": recalls, "records": records,
            "compactions": compactions}


import re  # noqa: E402

_LEVER_TOOLS = {"execute_rollback", "execute_rollout_restart"}


def beat_outcome(trace: list[dict], outcome_hint: str | None = None) -> dict:
    """Derive a beat's outcome chip from its trace. Lever tools carry the truth
    in their output ({"outcome": "executed"|"refused", ...}); read it from the
    LAST lever call. With no lever, fall back to the beat's declared hint (e.g.
    a scope 'deferred') or 'read_only'. Pure."""
    last = None
    for ev in trace:
        if ev.get("kind") == "tool" and ev.get("name") in _LEVER_TOOLS:
            last = ev
    if last is None:
        return {"kind": outcome_hint or "read_only", "detail": ""}

    text = last.get("output")
    text = text if isinstance(text, str) else str(text)
    kind = "refused" if "refused" in text.lower() else "executed"

    detail = ""
    m = re.search(r"rolled_back_to['\"]?\s*[:=]\s*['\"]?(\d+)", text)
    if m:
        detail = f"rolled back to rev {m.group(1)}"
    else:
        m = re.search(r"restarted_at['\"]?\s*[:=]\s*['\"]?([0-9T:\-.\+Z]+)", text)
        if m:
            detail = f"restarted at {m.group(1)}"
        else:
            m = re.search(r"reason['\"]?\s*[:=]\s*['\"]([^'\"]+)", text)
            if m:
                detail = m.group(1)
    return {"kind": kind, "detail": detail}


from datetime import datetime, timezone  # noqa: E402


def beat_record(n: int, beat: dict, resp: dict, now: datetime | None = None) -> dict:
    """Turn one demo beat + its /ask response into a JSON-serialisable record —
    the unit the presentation console reads (one JSON line per beat). Pure."""
    trace = resp.get("trace", []) or []
    h = extract_highlights(trace)
    ts = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "beat": n,
        "title": beat.get("title", ""),
        "shows": beat.get("shows", ""),
        "question": beat.get("message", ""),
        "harness": {
            "planned": len(h["plan"]),
            "todos": len(h["todos"]),
            "subagents": len(h["subagents"]),
            "tools": list(h["tools"].keys()),
            "recalls": h["recalls"],
            "records": h["records"],
        },
        "answer": resp.get("answer", ""),
        "outcome": beat_outcome(trace, beat.get("outcome_hint")),
        "ts": ts,
    }


# --- run-tree view: normalize a merged trace into renderable steps ----------

_TOOL_META = {
    "write_plan":   ("🗺️", "write_plan"),
    "write_todos":  ("✅", "write_todos"),
    "update_plan":  ("✅", "update_plan"),
    "recall_memory": ("🧠", "recall_memory"),
    "record_memory": ("💾", "record_memory"),
}


def _ms(ev: dict) -> str:
    v = ev.get("elapsed_ms")
    return f"{v / 1000:.1f}s" if isinstance(v, (int, float)) else ""


def _first_line(s, n: int = 90) -> str:
    for line in (s or "").splitlines():
        line = line.strip()
        if line:
            return line if len(line) <= n else line[:n] + "…"
    return ""


def _fmt_args(args) -> str:
    if not isinstance(args, dict) or not args:
        return ""
    return ", ".join(f"{k}={v}" for k, v in args.items())


def to_steps(trace: list[dict]) -> list[dict]:
    """Normalize a merged trace into an ordered list of renderable steps. Each
    step is {kind, icon, title, subtitle, timing, body} where `body` is a dict of
    {section-label: text} for an expander. Pure — the console just draws it.

    A spawned subagent is rendered from its `spawn_subagent` tool event (which
    carries both the delegated task and the returned summary); the paired
    harness `subagent` log event is dropped to avoid a duplicate node."""
    steps: list[dict] = []
    for ev in trace:
        kind, name = ev.get("kind"), ev.get("name")

        if kind == "phase":
            det = ev.get("output") or {}
            figs, claims = det.get("figures") or [], det.get("claims") or []
            bits = []
            if figs:
                bits.append("figures: " + ", ".join(figs))
            if claims:
                bits.append("claims: " + ", ".join(claims))
            steps.append({"kind": "phase", "icon": "🔁", "title": "Revision pass",
                          "subtitle": "; ".join(bits) or "verifier flagged an issue",
                          "timing": "", "body": {}})

        elif kind == "model":
            out = ev.get("output") or {}
            calls = out.get("tool_calls") or []
            body: dict[str, str] = {}
            if out.get("reasoning"):
                body["thinking"] = out["reasoning"]
            if out.get("content"):
                body["says"] = out["content"]
            if calls:
                body["decides to call"] = "\n".join(
                    f"{c.get('name')}({_fmt_args(c.get('args'))})" for c in calls)
            sub = _first_line(out.get("content"))
            if not sub and calls:
                sub = "→ " + ", ".join(str(c.get("name")) for c in calls)
            steps.append({"kind": "model", "icon": "🧠", "title": "model reasons",
                          "subtitle": sub, "timing": _ms(ev), "body": body})

        elif kind == "tool" and name == "spawn_subagent":
            body = {}
            if ev.get("input"):
                body["delegated"] = ev["input"]
            if ev.get("output") is not None:
                body["returned summary"] = ev["output"] if isinstance(
                    ev["output"], str) else str(ev["output"])
            steps.append({"kind": "subagent", "icon": "🧵", "title": "subagent",
                          "subtitle": _first_line(ev.get("input")),
                          "timing": _ms(ev), "body": body})

        elif kind == "tool":
            icon, title = _TOOL_META.get(name, ("🔧", name or "tool"))
            body = {}
            if ev.get("input"):
                body["input"] = ev["input"]
            if ev.get("output") is not None:
                body["output"] = ev["output"] if isinstance(
                    ev["output"], str) else str(ev["output"])
            if ev.get("error"):
                body["error"] = str(ev["error"])
            steps.append({"kind": "tool", "icon": icon, "title": title,
                          "subtitle": "", "timing": _ms(ev), "body": body})

        elif kind == "subagent":
            continue  # rendered from the spawn_subagent tool event above

        elif kind == "memory_write":
            steps.append({"kind": "memory", "icon": "💾", "title": "memory record",
                          "subtitle": "", "timing": "", "body": {}})

        elif kind == "compaction":
            steps.append({"kind": "compaction", "icon": "📦",
                          "title": "context compaction",
                          "subtitle": f"dropped {ev.get('dropped')}, "
                                      f"kept {ev.get('kept')}",
                          "timing": "", "body": {}})

    return steps
