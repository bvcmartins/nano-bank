"""Pure state helpers for the presentation console: parse the JSONL beat stream,
save/load recordings, map an outcome kind to a chip style, and read the static
beat catalog (title + what-it-tests + question) from the driver. No Streamlit here
so it stays unit-testable."""
from __future__ import annotations
import ast
import glob
import json
import os
from datetime import datetime, timezone


def beat_catalog(drive_path: str) -> list[dict]:
    """The demo's beats as a static catalog — {beat, title, shows, question} per
    entry — parsed straight from drive.py's BEATS list via ast (no import, no
    network), so the per-beat buttons + their 'what is being tested' captions
    render before any run and stay in sync with the driver. Empty on any problem."""
    try:
        with open(drive_path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError):
        return []
    node = next((n.value for n in tree.body if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "BEATS" for t in n.targets)),
                None)
    if node is None:
        return []
    try:
        raw = ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return []
    return [{"beat": i, "title": b.get("title", ""), "shows": b.get("shows", ""),
             "question": b.get("message", "")} for i, b in enumerate(raw, 1)]


def read_jsonl(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # partial trailing line mid-write
    return out


def save_recording(dir_: str, beats: list[dict], ledger_rows: list[dict],
                   chain: tuple[str, int | None]) -> str:
    os.makedirs(dir_, exist_ok=True)
    # microseconds keep filenames unique even for back-to-back saves.
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    path = os.path.join(dir_, f"{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"beats": beats, "ledger_snapshot": ledger_rows,
                   "chain": list(chain), "captured_at": ts}, f, indent=2)
    return path


def load_recording(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def latest_recording(dir_: str) -> str | None:
    files = sorted(glob.glob(os.path.join(dir_, "*.json")), key=os.path.getmtime)
    return files[-1] if files else None


_STYLES = {
    "executed":  ("EXECUTED", "#1a7f37"),
    "refused":   ("REFUSED", "#b35900"),
    "deferred":  ("DEFERRED", "#6639ba"),
    "delegated": ("DELEGATED", "#0969da"),
    "failed":    ("FAILED", "#cf222e"),
    "read_only": ("READ-ONLY", "#57606a"),
}


def outcome_style(kind: str) -> tuple[str, str]:
    return _STYLES.get(kind, (kind.upper(), "#57606a"))


_TOOL_ICON = {"read_file": "📖", "write_file": "📝", "write_code": "📝",
              "bash": "🖥️", "run_python": "🖥️", "run_tests": "🧪"}


def _tool_label(name: str, inp: str) -> str:
    """A compact 'write_file(rounding.py)'-style label from a tool call + its input."""
    arg = (inp or "").strip().splitlines()[0] if (inp or "").strip() else ""
    arg = arg[:60]
    return f"{name}({arg})" if arg else name


def coder_timeline(coder_run: dict) -> list[dict]:
    """Normalise a stored coder run into an ordered list of display steps, framed as
    the CTO⇄Coder exchange: the delegation, the coder's reasoning/tool steps (the
    'coder in action'), then the diff and the gated-PR result. Each step is
    {icon, label, body, kind}. Pure — the app just renders it, one at a time."""
    if not coder_run:
        return []
    steps: list[dict] = []
    kind = coder_run.get("kind", "")
    task = coder_run.get("task", "")
    steps.append({"icon": "📋", "kind": "delegate",
                  "label": f"CTO → Coder  (delegate · {kind})",
                  "body": task})
    for s in coder_run.get("steps", []):
        if s.get("type") == "reasoning":
            steps.append({"icon": "🧠", "kind": "reasoning",
                          "label": "coder reasoning", "body": s.get("text", "")})
        elif s.get("type") == "tool":
            name = s.get("name", "tool")
            body = (s.get("input", "") or "")
            out = (s.get("output", "") or "")
            if out:
                body = f"{body}\n\n→ {out}" if body else f"→ {out}"
            steps.append({"icon": _TOOL_ICON.get(name, "🔧"), "kind": "tool",
                          "label": _tool_label(name, s.get("input", "")), "body": body})
    diff = coder_run.get("diff", "")
    if diff.strip():
        steps.append({"icon": "📄", "kind": "diff",
                      "label": "diff on the review branch", "body": diff})
    tests = coder_run.get("tests", "")
    branch = coder_run.get("branch", "")
    # Reflect the ACTUAL outcome, not a hardcoded success line — a stored failed run
    # (red suite or no change) must not claim a gated PR was opened.
    outcome = coder_run.get("outcome", "executed")
    if outcome == "executed":
        icon = "✅"
        tail = (f"branch: {branch}\n"
                "gated PR — a human reviews and merges (never auto-merged).")
    else:
        icon = "❌"
        reason = coder_run.get("reason", "")
        tail = f"{outcome}{(' — ' + reason) if reason else ''}\nno PR opened."
    steps.append({"icon": icon, "kind": "result",
                  "label": "Coder → CTO  (result)",
                  "body": f"tests: {tests}   ·   {tail}"})
    return steps
