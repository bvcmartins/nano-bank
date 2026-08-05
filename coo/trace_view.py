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
