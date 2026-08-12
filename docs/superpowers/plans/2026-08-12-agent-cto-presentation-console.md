# Agent CTO Presentation Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a two-pane Streamlit presentation console for the Agent CTO demo — the CTO's narrated arc on the left, the live tamper-evident ledger on the right — driven live via the existing `run-demo.sh` with a bulletproof recorded fallback.

**Architecture:** Additive only. Two pure helpers in the shared `csuite.trace_view` turn each `/ask` response into a structured beat record; a new `--emit-jsonl` seam in `demos/_driver.py` writes those records (one JSON line per beat) as the demo runs — that JSONL file *is* the recording. A host-run Streamlit app (`demos/08-cto/present/`) shells out to `run-demo.sh --emit-jsonl`, tails the JSONL into left-pane beat cards, and independently polls Postgres (reusing the `inspect-ledger.sh` query) for the right-pane ledger + chain badge. Replay reads a saved recording. Nothing in the CTO agent, the levers, or the ledger schema changes.

**Tech Stack:** Python 3.13, Streamlit (`>=1.38`, already used by `cto/console.py`), httpx, `kubectl exec` → `psql` for ledger reads, pytest.

## Global Constraints

- **Snap env before any kubectl/docker/kind:** `export XDG_RUNTIME_DIR=/run/user/1000 XDG_DATA_HOME=/home/bmartins/.local/share`.
- **Cluster/DB:** context `kind-nano-bank`, namespace `nano-bank`, postgres via `kubectl exec` into the `app=postgres` pod, `psql -U nanobank_user -d nano_bank_db`.
- **The console never mutates the cluster itself.** All staging/mutation stays inside the already-merged `demos/08-cto/run-demo.sh` (host → port-forwarded cluster) and the agent's own levers. The console only *runs* that script, *reads* the ledger, and *renders*.
- **Pure logic is isolated and unit-tested; `st.*` rendering is thin and not unit-tested.**
- **The `--emit-jsonl` seam is additive:** default (unset) leaves the existing terminal demo and its behaviour byte-for-byte unchanged.
- **Deviation from spec (intentional):** the spec's JSONL example included `ledger_seq`; we DROP it so the driver stays DB-free ("a demo — it only asks; it never seeds or mutates"). The ledger pane correlates by polling the DB independently.
- **Lever tool names** (as they appear in the trace `name`): `execute_rollout_restart`, `execute_rollback`.
- **Test runner:** `.venv/bin/python -m pytest ...` from repo root (the repo venv, which has the csuite deps). The `present/` package's own tests run via `.venv/bin/python -m pytest demos/08-cto/present/tests -q`.

---

### Task 1: `beat_outcome` — derive a beat's outcome from the trace

**Files:**
- Modify: `csuite/trace_view.py` (add `beat_outcome` after `extract_highlights`)
- Test: `csuite/tests/test_beat_outcome.py`

**Interfaces:**
- Produces: `beat_outcome(trace: list[dict], outcome_hint: str | None = None) -> dict` returning `{"kind": str, "detail": str}` where `kind ∈ {"executed","refused","deferred","read_only"}`.

**Derivation rules:** scan `trace` for the LAST event with `kind == "tool"` and `name in {"execute_rollback","execute_rollout_restart"}`; read its `output` (a string — see `csuite/trace.py` `on_tool_end`). If found: `"refused"` when the output contains `refused`, else `"executed"`; pull `detail` from `rolled_back_to` / `restarted_at` / `reason` if present. If no lever event: use `outcome_hint` when given (e.g. `"deferred"`), else `"read_only"`.

- [ ] **Step 1: Write the failing test**

```python
# csuite/tests/test_beat_outcome.py
from csuite.trace_view import beat_outcome


def _tool(name, output):
    return {"kind": "tool", "name": name, "output": output}


def test_executed_rollback_pulls_revision_detail():
    trace = [_tool("platform_health", "ok"),
             _tool("execute_rollback", "{'outcome': 'executed', 'effect': {'rolled_back_to': 28}}")]
    out = beat_outcome(trace)
    assert out["kind"] == "executed"
    assert "28" in out["detail"]


def test_refused_restart_pulls_reason():
    trace = [_tool("execute_rollout_restart",
                   '{"outcome": "refused", "reason": "coo is not crashlooping or unready"}')]
    out = beat_outcome(trace)
    assert out["kind"] == "refused"
    assert "crashlooping" in out["detail"]


def test_no_lever_is_read_only():
    trace = [_tool("estate_health", "..."), _tool("compute", "12.5")]
    assert beat_outcome(trace)["kind"] == "read_only"


def test_hint_used_only_without_a_lever():
    assert beat_outcome([], outcome_hint="deferred")["kind"] == "deferred"
    # a real lever result wins over the hint
    trace = [_tool("execute_rollback", "{'outcome': 'executed'}")]
    assert beat_outcome(trace, outcome_hint="deferred")["kind"] == "executed"


def test_last_lever_event_wins():
    trace = [_tool("execute_rollout_restart", '{"outcome":"refused","reason":"healthy"}'),
             _tool("execute_rollback", '{"outcome":"executed","effect":{"rolled_back_to":16}}')]
    assert beat_outcome(trace)["kind"] == "executed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest csuite/tests/test_beat_outcome.py -q`
Expected: FAIL with `ImportError: cannot import name 'beat_outcome'`.

- [ ] **Step 3: Write minimal implementation**

```python
# csuite/trace_view.py  (append after extract_highlights)
import re

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest csuite/tests/test_beat_outcome.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add csuite/trace_view.py csuite/tests/test_beat_outcome.py
git commit -m "feat(csuite): beat_outcome — derive executed/refused/read-only from a trace"
```

---

### Task 2: `beat_record` — a full structured beat record for the JSONL

**Files:**
- Modify: `csuite/trace_view.py` (add `beat_record` after `beat_outcome`)
- Test: `csuite/tests/test_beat_record.py`

**Interfaces:**
- Consumes: `extract_highlights`, `beat_outcome` (Task 1), both in `csuite.trace_view`.
- Produces: `beat_record(n: int, beat: dict, resp: dict, now: "datetime | None" = None) -> dict` — a JSON-serialisable record: `{beat, title, shows, question, harness{planned,todos,subagents,tools,recalls,records}, answer, outcome{kind,detail}, ts}`. `beat` is a demo beat dict (has `title`, `shows`, `message`, optional `outcome_hint`); `resp` is the `/ask` JSON (`answer`, `trace`).

- [ ] **Step 1: Write the failing test**

```python
# csuite/tests/test_beat_record.py
import json
from datetime import datetime, timezone
from csuite.trace_view import beat_record


def test_record_is_json_serialisable_and_shaped():
    beat = {"title": "Recovery", "shows": "it acts", "message": "fix cfo"}
    resp = {"answer": "I rolled it back.",
            "trace": [{"kind": "tool", "name": "execute_rollback",
                       "output": "{'outcome':'executed','effect':{'rolled_back_to':28}}"}]}
    rec = beat_record(7, beat, resp, now=datetime(2026, 8, 12, tzinfo=timezone.utc))
    json.dumps(rec)  # must not raise
    assert rec["beat"] == 7
    assert rec["title"] == "Recovery"
    assert rec["question"] == "fix cfo"
    assert rec["answer"] == "I rolled it back."
    assert rec["outcome"]["kind"] == "executed"
    assert rec["ts"].startswith("2026-08-12")


def test_harness_counts_are_summarised():
    beat = {"title": "Review", "shows": "grounded", "message": "review"}
    resp = {"answer": "…", "trace": [
        {"kind": "tool", "name": "write_plan", "input": "x"},
        {"kind": "tool", "name": "estate_health", "input": None},
        {"kind": "tool", "name": "estate_health", "input": None},
        {"kind": "subagent", "task": "deep-dive cfo", "tools": ["rollouts"], "depth": 1, "chars": 20},
    ]}
    rec = beat_record(1, beat, resp)
    assert rec["harness"]["planned"] == 1
    assert rec["harness"]["subagents"] == 1
    assert "estate_health" in rec["harness"]["tools"]
    assert rec["outcome"]["kind"] == "read_only"


def test_outcome_hint_flows_through():
    beat = {"title": "Scope", "shows": "defers", "message": "P&L?", "outcome_hint": "deferred"}
    rec = beat_record(5, beat, {"answer": "ask the CFO", "trace": []})
    assert rec["outcome"]["kind"] == "deferred"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest csuite/tests/test_beat_record.py -q`
Expected: FAIL with `ImportError: cannot import name 'beat_record'`.

- [ ] **Step 3: Write minimal implementation**

```python
# csuite/trace_view.py  (append after beat_outcome)
from datetime import datetime, timezone


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest csuite/tests/test_beat_record.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add csuite/trace_view.py csuite/tests/test_beat_record.py
git commit -m "feat(csuite): beat_record — structured per-beat record for the presentation console"
```

---

### Task 3: `--emit-jsonl` seam in the driver + `run-demo.sh` passthrough + beat-5 hint

**Files:**
- Modify: `demos/_driver.py` (add `_append_jsonl`; wire `--emit-jsonl` into `run`)
- Modify: `demos/08-cto/run-demo.sh` (forward `--emit-jsonl`)
- Modify: `demos/08-cto/drive.py` (add `"outcome_hint": "deferred"` to beat 5)
- Test: `demos/08-cto/present/tests/test_append_jsonl.py`

**Interfaces:**
- Consumes: `beat_record` from `csuite.trace_view` (Task 2).
- Produces: `_append_jsonl(path: str, obj: dict) -> None` in `demos/_driver.py` (append one JSON line + `\n`); `run(...)` gains a `--emit-jsonl PATH` CLI flag that writes one `beat_record` line per beat.

- [ ] **Step 1: Write the failing test**

```python
# demos/08-cto/present/tests/test_append_jsonl.py
import json, os, sys

# demos/_driver.py lives two levels up from present/tests/
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from _driver import _append_jsonl  # noqa: E402


def test_append_writes_one_json_object_per_line(tmp_path):
    p = tmp_path / "run.jsonl"
    _append_jsonl(str(p), {"beat": 1, "title": "a"})
    _append_jsonl(str(p), {"beat": 2, "title": "b"})
    lines = p.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["beat"] == 1
    assert json.loads(lines[1])["title"] == "b"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest demos/08-cto/present/tests/test_append_jsonl.py -q`
Expected: FAIL with `ImportError: cannot import name '_append_jsonl'`.

- [ ] **Step 3: Write minimal implementation**

In `demos/_driver.py`, add the import near the top (beside the existing `from csuite.trace_view import extract_highlights`):

```python
from csuite.trace_view import extract_highlights, beat_record  # noqa: E402
import json
```

Add the helper (module level):

```python
def _append_jsonl(path: str, obj: dict) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj) + "\n")
```

In `run(...)`, register the flag and emit per beat. Change the argparse block:

```python
    ap = argparse.ArgumentParser()
    ap.add_argument("--beats", help="comma-separated 1-based beat numbers (default all)")
    ap.add_argument("--emit-jsonl", dest="emit_jsonl", default=None,
                    help="also append one structured JSON record per beat to this path")
    args = ap.parse_args()
```

Then, in the per-beat loop, right after `render_beat(n, beat, resp, agent_label)`:

```python
        render_beat(n, beat, resp, agent_label)
        if args.emit_jsonl:
            _append_jsonl(args.emit_jsonl, beat_record(n, beat, resp))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest demos/08-cto/present/tests/test_append_jsonl.py -q`
Expected: PASS.

- [ ] **Step 5: Add the beat-5 hint and the `run-demo.sh` passthrough**

In `demos/08-cto/drive.py`, add to the beat 5 dict (`"title": "Scope discipline — the books are the CFO's"`):

```python
        "thread": "new",
        "outcome_hint": "deferred",
```

In `demos/08-cto/run-demo.sh`, add an `--emit-jsonl` flag that forwards to `drive.py`. In the arg-parse `while` loop add a case:

```bash
    --emit-jsonl) EMIT_ARG="--emit-jsonl $2"; shift ;;
```

Initialise `EMIT_ARG=""` next to `BEATS_ARG=""` (near the top), and change the drive invocation (currently the last two lines) to include it:

```bash
CTO_API_URL=http://localhost:8095 PYTHONPATH="$PWD" \
  "$VENV/bin/python" demos/08-cto/drive.py $BEATS_ARG $EMIT_ARG
```

- [ ] **Step 6: Verify the terminal demo is unchanged and the flag parses**

Run: `.venv/bin/python -m pytest cto csuite demos/08-cto/present/tests -q`
Expected: PASS (existing 58 + the new tests).
Run: `bash -n demos/08-cto/run-demo.sh` (syntax check) — expected: no output, exit 0.

- [ ] **Step 7: Commit**

```bash
git add demos/_driver.py demos/08-cto/drive.py demos/08-cto/run-demo.sh \
        demos/08-cto/present/tests/test_append_jsonl.py
git commit -m "feat(demo): --emit-jsonl beat-stream seam in the shared driver + run-demo passthrough"
```

---

### Task 4: `ledger.py` — read the tamper-evident ledger from the cluster

**Files:**
- Create: `demos/08-cto/present/ledger.py`
- Test: `demos/08-cto/present/tests/test_ledger.py`

**Interfaces:**
- Produces:
  - `parse_rows(psql_text: str) -> list[dict]` (pure) — parses `-At -F'|'` output into row dicts with keys `seq, ts, actor, action, outcome, deployment, detail, prev, entry`.
  - `parse_verdict(psql_text: str) -> tuple[str, int | None]` (pure) — `("INTACT", None)` for empty output, else `("TAMPERED", seq)`.
  - `read_rows() -> list[dict]`, `chain_verdict() -> tuple[str, int | None]`, `tamper_demo() -> dict` — subprocess wrappers over `kubectl exec … psql`.

- [ ] **Step 1: Write the failing test**

```python
# demos/08-cto/present/tests/test_ledger.py
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from ledger import parse_rows, parse_verdict  # noqa: E402

SAMPLE = (
    "14|2026-08-11 17:17:17|cto|rollout_restart|executed|cfo|2026-08-11T17:17:17Z|d1038bc793|c0f50ccd6d\n"
    "15|2026-08-11 21:25:31|cto|rollout_restart|refused|coo|coo is not crashlooping|c0f50ccd6d|e7488dad44\n"
)


def test_parse_rows_splits_fields():
    rows = parse_rows(SAMPLE)
    assert len(rows) == 2
    assert rows[0]["seq"] == "14"
    assert rows[0]["actor"] == "cto"
    assert rows[0]["outcome"] == "executed"
    assert rows[1]["deployment"] == "coo"
    assert rows[1]["detail"].startswith("coo is not crashlooping")


def test_parse_rows_ignores_blank_lines():
    assert parse_rows("\n\n") == []


def test_verdict_empty_is_intact():
    assert parse_verdict("\n") == ("INTACT", None)


def test_verdict_seq_is_tampered():
    assert parse_verdict("7\n") == ("TAMPERED", 7)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest demos/08-cto/present/tests/test_ledger.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'ledger'`.

- [ ] **Step 3: Write minimal implementation**

```python
# demos/08-cto/present/ledger.py
"""Read the tamper-evident agent_action_ledger for the presentation console.
Reads straight from Postgres in the kind cluster via `kubectl exec` (no host DB
driver), mirroring demos/08-cto/inspect-ledger.sh. The pure parsers are unit
tested; the subprocess wrappers are exercised by the live smoke."""
from __future__ import annotations
import subprocess

CTX = "kind-nano-bank"
NS = "nano-bank"
_FIELDS = ["seq", "ts", "actor", "action", "outcome", "deployment", "detail", "prev", "entry"]

_ROWS_SQL = """
SELECT seq, to_char(ts,'YYYY-MM-DD HH24:MI:SS'), actor, action,
       COALESCE(effect->>'outcome','—'),
       COALESCE(params->>'deployment',''),
       COALESCE(effect->'effect'->>'rolled_back_to',
                effect->'effect'->>'restarted_at',
                effect->>'reason',''),
       left(prev_hash,10), left(entry_hash,10)
FROM agent_action_ledger ORDER BY seq;
"""


def parse_rows(psql_text: str) -> list[dict]:
    rows = []
    for line in psql_text.splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        parts += [""] * (len(_FIELDS) - len(parts))
        rows.append(dict(zip(_FIELDS, parts)))
    return rows


def parse_verdict(psql_text: str) -> tuple[str, int | None]:
    s = psql_text.strip()
    return ("INTACT", None) if s == "" else ("TAMPERED", int(s))


def _pg_pod() -> str:
    return subprocess.run(
        ["kubectl", "--context", CTX, "-n", NS, "get", "pod", "-l", "app=postgres",
         "-o", "jsonpath={.items[0].metadata.name}"],
        capture_output=True, text=True, check=True).stdout.strip()


def _psql(sql: str, pod: str | None = None) -> str:
    pod = pod or _pg_pod()
    return subprocess.run(
        ["kubectl", "--context", CTX, "-n", NS, "exec", "-i", pod, "--",
         "psql", "-U", "nanobank_user", "-d", "nano_bank_db", "-At", "-F", "|", "-c", sql],
        capture_output=True, text=True).stdout


def read_rows() -> list[dict]:
    return parse_rows(_psql(_ROWS_SQL))


def chain_verdict() -> tuple[str, int | None]:
    return parse_verdict(_psql("SELECT verify_agent_ledger();"))


def tamper_demo() -> dict:
    """Prove immutability: attempt an UPDATE and a DELETE; both must be rejected
    by the append-only trigger. Returns {update: bool, delete: bool} rejected."""
    pod = _pg_pod()
    upd = _run_expect_reject(
        "UPDATE agent_action_ledger SET effect='{\"outcome\":\"tampered\"}' WHERE seq=1;", pod)
    dele = _run_expect_reject("DELETE FROM agent_action_ledger WHERE seq=1;", pod)
    return {"update_rejected": upd, "delete_rejected": dele}


def _run_expect_reject(sql: str, pod: str) -> bool:
    out = subprocess.run(
        ["kubectl", "--context", CTX, "-n", NS, "exec", "-i", pod, "--",
         "psql", "-U", "nanobank_user", "-d", "nano_bank_db", "-c", sql],
        capture_output=True, text=True)
    blob = (out.stdout + out.stderr).lower()
    return "append-only" in blob or "error" in blob
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest demos/08-cto/present/tests/test_ledger.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add demos/08-cto/present/ledger.py demos/08-cto/present/tests/test_ledger.py
git commit -m "feat(present): ledger read + chain verdict + tamper-demo (pure parsers tested)"
```

---

### Task 5: `state.py` — recordings + beat view-models

**Files:**
- Create: `demos/08-cto/present/state.py`
- Test: `demos/08-cto/present/tests/test_state.py`

**Interfaces:**
- Produces:
  - `read_jsonl(text: str) -> list[dict]` (pure) — parse a JSONL beat stream, skipping blank/partial trailing lines.
  - `save_recording(dir_: str, beats: list[dict], ledger_rows: list[dict], chain: tuple[str, int | None]) -> str` — write `<dir>/<ts>.json`, return the path.
  - `load_recording(path: str) -> dict` — `{beats, ledger_snapshot, chain, captured_at}`.
  - `latest_recording(dir_: str) -> str | None` — newest `*.json` path, or None.
  - `outcome_style(kind: str) -> tuple[str, str]` (pure) — `(label, color)` for the chip, e.g. `("EXECUTED", "#1a7f37")`.

- [ ] **Step 1: Write the failing test**

```python
# demos/08-cto/present/tests/test_state.py
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from state import (read_jsonl, save_recording, load_recording,  # noqa: E402
                   latest_recording, outcome_style)


def test_read_jsonl_skips_partial_trailing_line():
    text = '{"beat":1}\n{"beat":2}\n{"beat":3'  # last line half-written
    beats = read_jsonl(text)
    assert [b["beat"] for b in beats] == [1, 2]


def test_recording_round_trip(tmp_path):
    p = save_recording(str(tmp_path), beats=[{"beat": 1}],
                       ledger_rows=[{"seq": "1"}], chain=("INTACT", None))
    rec = load_recording(p)
    assert rec["beats"] == [{"beat": 1}]
    assert rec["ledger_snapshot"] == [{"seq": "1"}]
    assert rec["chain"] == ["INTACT", None]  # json turns the tuple into a list


def test_latest_recording_picks_newest(tmp_path):
    a = save_recording(str(tmp_path), [{"beat": 1}], [], ("INTACT", None))
    import time; time.sleep(0.01)
    b = save_recording(str(tmp_path), [{"beat": 2}], [], ("INTACT", None))
    assert latest_recording(str(tmp_path)) == b
    assert a != b


def test_latest_recording_none_when_empty(tmp_path):
    assert latest_recording(str(tmp_path)) is None


def test_outcome_style_labels():
    assert outcome_style("executed")[0] == "EXECUTED"
    assert outcome_style("refused")[0] == "REFUSED"
    assert outcome_style("read_only")[0] == "READ-ONLY"
    assert outcome_style("deferred")[0] == "DEFERRED"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest demos/08-cto/present/tests/test_state.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'state'`.

- [ ] **Step 3: Write minimal implementation**

```python
# demos/08-cto/present/state.py
"""Pure state helpers for the presentation console: parse the JSONL beat stream,
save/load recordings, and map an outcome kind to a chip style. No Streamlit here
so it stays unit-testable."""
from __future__ import annotations
import glob
import json
import os
from datetime import datetime, timezone


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
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
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
    "read_only": ("READ-ONLY", "#57606a"),
}


def outcome_style(kind: str) -> tuple[str, str]:
    return _STYLES.get(kind, (kind.upper(), "#57606a"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest demos/08-cto/present/tests/test_state.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add demos/08-cto/present/state.py demos/08-cto/present/tests/test_state.py
git commit -m "feat(present): recordings + JSONL parse + outcome chip styles (pure, tested)"
```

---

### Task 6: `app.py` — the two-pane Streamlit console

**Files:**
- Create: `demos/08-cto/present/app.py`
- Create: `demos/08-cto/present/requirements.txt`
- Create: `demos/08-cto/present/README.md`

**Interfaces:**
- Consumes: `ledger.read_rows/chain_verdict/tamper_demo` (Task 4), `state.read_jsonl/save_recording/load_recording/latest_recording/outcome_style` (Task 5), and `run-demo.sh --emit-jsonl` (Task 3).

This task is a Streamlit app wiring already-tested pure modules; there is no unit test for `st.*` rendering — the live smoke (Task 7) is its test.

- [ ] **Step 1: Write `requirements.txt`**

```text
streamlit>=1.38
```

(httpx/kubectl are already available in the run environment; the ledger reads shell out to `kubectl`.)

- [ ] **Step 2: Write the app**

```python
# demos/08-cto/present/app.py
"""Agent CTO presentation console — one screen, two panes: the CTO's narrated
arc (left) and the live tamper-evident agent_action_ledger (right). Driven live
by run-demo.sh (--emit-jsonl) with a recorded fallback. Run from the HOST:

    export XDG_RUNTIME_DIR=/run/user/1000 XDG_DATA_HOME=/home/bmartins/.local/share
    streamlit run demos/08-cto/present/app.py

Live runs need docker+kind+kubectl+uv and the deployed CTO stack (see
demos/08-cto/run-demo.sh)."""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ledger  # noqa: E402
import state  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
RUN_DEMO = os.path.join(REPO_ROOT, "demos", "08-cto", "run-demo.sh")
RECORDINGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recordings")

st.set_page_config(page_title="Agent CTO", layout="wide")
ss = st.session_state
ss.setdefault("beats", [])          # rendered beat records (live or replay)
ss.setdefault("mode", "idle")       # idle | live | replay
ss.setdefault("proc", None)         # live run subprocess
ss.setdefault("jsonl_path", None)   # live run JSONL file
ss.setdefault("snapshot", None)     # ledger snapshot when replaying


def _beat_card(rec: dict) -> None:
    label, color = state.outcome_style(rec["outcome"]["kind"])
    st.markdown(f"#### Beat {rec['beat']} — {rec['title']}")
    st.caption(rec["shows"])
    st.markdown(f"**Q:** {rec['question']}")
    h = rec["harness"]
    bits = []
    if h["planned"]:
        bits.append(f"planned {h['planned']}")
    if h["todos"]:
        bits.append(f"todos {h['todos']}")
    if h["subagents"]:
        bits.append(f"subagent×{h['subagents']}")
    if h["tools"]:
        bits.append("tools: " + ", ".join(h["tools"]))
    if bits:
        with st.expander("harness · " + " · ".join(bits)):
            st.json(h)
    st.write(rec["answer"])
    detail = f" → {rec['outcome']['detail']}" if rec["outcome"]["detail"] else ""
    st.markdown(
        f"<span style='background:{color};color:white;padding:2px 10px;"
        f"border-radius:10px;font-weight:700'>{label}{detail}</span>",
        unsafe_allow_html=True)
    st.divider()


def _start_live() -> None:
    os.makedirs(RECORDINGS, exist_ok=True)
    fd, path = tempfile.mkstemp(suffix=".jsonl", prefix="cto-run-")
    os.close(fd)
    env = dict(os.environ,
               XDG_RUNTIME_DIR=os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"),
               XDG_DATA_HOME=os.environ.get("XDG_DATA_HOME",
                                            os.path.expanduser("~/.local/share")))
    ss.proc = subprocess.Popen(["bash", RUN_DEMO, "--no-up", "--emit-jsonl", path],
                               cwd=REPO_ROOT, env=env)
    ss.jsonl_path, ss.beats, ss.mode, ss.snapshot = path, [], "live", None


def _start_replay() -> None:
    latest = state.latest_recording(RECORDINGS)
    if not latest:
        st.toast("No recording yet — run live once to capture one.")
        return
    rec = state.load_recording(latest)
    ss.beats, ss.mode, ss.snapshot = rec["beats"], "replay", rec["ledger_snapshot"]


# --- control bar -----------------------------------------------------------
st.title("Agent CTO — analyst + audited self-healing")
c1, c2, c3, _ = st.columns([1, 1, 1, 4])
if c1.button("▶ Run live", type="primary", disabled=ss.mode == "live"):
    _start_live()
if c2.button("⏮ Replay last good run", disabled=ss.mode == "live"):
    _start_replay()
tamper = c3.button("🔒 Tamper demo")

left, right = st.columns([3, 2])

# --- left: agent arc -------------------------------------------------------
with left:
    st.subheader("Agent CTO")
    if ss.mode == "live" and ss.jsonl_path:
        try:
            with open(ss.jsonl_path, encoding="utf-8") as f:
                ss.beats = state.read_jsonl(f.read())
        except FileNotFoundError:
            pass
    for rec in ss.beats:
        _beat_card(rec)
    if ss.mode == "live" and ss.proc and ss.proc.poll() is not None:
        # live run finished: snapshot the ledger + save the recording
        rows = ledger.read_rows()
        state.save_recording(RECORDINGS, ss.beats, rows, ledger.chain_verdict())
        ss.mode, ss.proc = "idle", None
        st.toast("Live run complete — recording saved.")

# --- right: audit ledger ---------------------------------------------------
with right:
    st.subheader("Audit ledger")
    if tamper:
        st.write(ledger.tamper_demo())
    try:
        rows = ss.snapshot if ss.mode == "replay" else ledger.read_rows()
        status, seq = (("INTACT", None) if ss.mode == "replay"
                       else ledger.chain_verdict())
        badge = "🟢 CHAIN INTACT" if status == "INTACT" else f"🔴 TAMPERED (seq {seq})"
        st.markdown(f"### {badge}")
        st.dataframe(rows, use_container_width=True, hide_index=True)
    except Exception as e:  # noqa: BLE001
        st.warning(f"ledger unavailable ({e}). Is the port-forward / cluster up?")

# live view refreshes itself while a run is in flight
if ss.mode == "live":
    import time
    time.sleep(1.5)
    st.rerun()
```

- [ ] **Step 3: Write the README**

```markdown
# Agent CTO — presentation console

Two-pane console for talks: the CTO's narrated arc (left) and the live
tamper-evident `agent_action_ledger` (right). Driven live via `run-demo.sh`
with a recorded fallback.

## Run (from the host)

    export XDG_RUNTIME_DIR=/run/user/1000 XDG_DATA_HOME=/home/bmartins/.local/share
    uv pip install --python demos/08-cto/.venv/bin/python -r demos/08-cto/present/requirements.txt
    demos/08-cto/.venv/bin/streamlit run demos/08-cto/present/app.py

- **▶ Run live** — stages the incident + drives the 7 beats against the deployed
  CTO agent (needs docker+kind+kubectl+uv and the CTO stack up). Each run is
  saved to `recordings/`.
- **⏮ Replay last good run** — plays the newest recording; network-independent.
  A canonical recording is committed so this works on a fresh checkout.
- **🔒 Tamper demo** — proves the ledger rejects UPDATE/DELETE.

The console never mutates the cluster itself — all staging lives in
`run-demo.sh`; the console runs it, reads the ledger, and renders.
```

- [ ] **Step 4: Syntax-check the app and confirm pure-module tests still pass**

Run: `.venv/bin/python -c "import ast; ast.parse(open('demos/08-cto/present/app.py').read())"`
Expected: no output, exit 0.
Run: `.venv/bin/python -m pytest demos/08-cto/present/tests -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add demos/08-cto/present/app.py demos/08-cto/present/requirements.txt \
        demos/08-cto/present/README.md
git commit -m "feat(present): two-pane Streamlit CTO presentation console"
```

---

### Task 7: Canonical recording + gitignore + live smoke

**Files:**
- Create: `demos/08-cto/present/.gitignore`
- Create: `demos/08-cto/present/recordings/<ts>.json` (one committed canonical recording)
- Modify: `demos/README.md` (note the console under row 8)

**Interfaces:** none produced; this task captures a real recording and verifies the whole thing live.

- [ ] **Step 1: Ignore ad-hoc recordings but keep the canonical one**

```bash
cat > demos/08-cto/present/.gitignore <<'EOF'
# Ad-hoc live-run recordings are local; the one canonical recording is force-added.
recordings/*.json
!recordings/canonical.json
EOF
```

- [ ] **Step 2: Install streamlit into the demo venv**

Run:
```bash
export XDG_RUNTIME_DIR=/run/user/1000 XDG_DATA_HOME=/home/bmartins/.local/share
uv pip install --python demos/08-cto/.venv/bin/python -r demos/08-cto/present/requirements.txt
```
Expected: streamlit installed.

- [ ] **Step 3: Live smoke — run the console and drive one live run**

Confirm the CTO stack is up (`kubectl --context kind-nano-bank -n nano-bank get deploy cto` → READY 1/1). Launch:
```bash
demos/08-cto/.venv/bin/streamlit run demos/08-cto/present/app.py --server.headless true
```
In the browser: click **▶ Run live**. Verify the left pane fills beat-by-beat, the right pane shows rows with **🟢 CHAIN INTACT**, beat 6 ends **REFUSED**, beat 7 ends **EXECUTED → rev N**, and a recording is written to `recordings/`. Click **🔒 Tamper demo** → `{"update_rejected": true, "delete_rejected": true}`.

- [ ] **Step 4: Promote the captured run to the canonical recording**

```bash
cp "$(ls -t demos/08-cto/present/recordings/*.json | head -1)" \
   demos/08-cto/present/recordings/canonical.json
```
Reload the app, click **⏮ Replay last good run**, and confirm all 7 cards render from the recording with the ledger snapshot and INTACT badge — with no cluster needed.

- [ ] **Step 5: Note the console in the demo index**

In `demos/README.md`, under the Agent CTO row (row 8), add a line: `A presentation console (agent arc + live ledger, live/replay) lives in demos/08-cto/present/ — see its README.`

- [ ] **Step 6: Commit**

```bash
git add -f demos/08-cto/present/recordings/canonical.json
git add demos/08-cto/present/.gitignore demos/README.md
git commit -m "feat(present): canonical recording + gitignore + demo-index note (live-verified)"
```

---

## Self-Review

**Spec coverage:**
- Split two-pane centerpiece → Task 6 (`left`/`right` columns). ✓
- Live-with-recorded-fallback drive mode → Task 3 (emit) + Task 6 (`_start_live`/`_start_replay`) + Task 7 (canonical). ✓
- Rich collapsible harness cards → Task 6 `_beat_card` (expander + JSON). ✓
- Streamlit, host-run → Task 6. ✓
- §A `--emit-jsonl` additive seam in shared `_driver.py` → Task 3 (+ Task 1/2 pure helpers). ✓
- §B pure ledger read helper → Task 4. ✓
- §C two-pane app → Task 6. ✓
- §D recording format + committed canonical → Task 5 + Task 7. ✓
- Error handling (ledger unavailable, no recording, failing live run) → Task 6 (`try/except`, replay toast) + Task 5 (`latest_recording` None). ✓
- Testing (driver emitter, ledger helper, app pure logic, live smoke) → Tasks 1–5 unit tests + Task 7 smoke. ✓
- YAGNI: reusable bits in shared `csuite`/`_driver` (Tasks 1–3); CTO-only app (Task 6). ✓

**Placeholder scan:** no TBD/TODO; every code step has concrete code. ✓

**Type consistency:** `beat_record`/`beat_outcome` signatures match between Tasks 1–3; `outcome.kind` values (`executed/refused/deferred/read_only`) are identical across Tasks 1, 2, 5; ledger row keys (`seq…entry`) match between Task 4 producer and Task 6 dataframe consumer; `chain` tuple↔list round-trip noted in Task 5 test. ✓

**Intentional deviation:** `ledger_seq` dropped from the JSONL (recorded in Global Constraints) to keep the driver DB-free. ✓
