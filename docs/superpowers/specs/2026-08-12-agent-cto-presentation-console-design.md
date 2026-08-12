# Design: Agent CTO presentation console

Date: 2026-08-12
Status: Approved (design), pending implementation plan

## Problem

The Agent CTO demo (`demos/08-cto/`) is complete and honest, but it only exists
as a **terminal** experience: `drive.py` narrates the 7-beat arc to stdout and
`inspect-ledger.sh` dumps the `agent_action_ledger` as a raw `psql` table. That
is fine for a developer, but it is not something to stand in front of an audience
with — the story (the agent *acting*, and the tamper-evident audit trail
*proving* it) is buried in scrollback, and the last line a viewer often sees is a
cosmetic `exceeded its progress deadline`.

We want a **presentation console**: one screen that shows the CTO reasoning
through the arc on the left and the audit ledger filling up on the right, driven
live during the talk with a bulletproof recorded fallback.

## Decisions (locked with the user)

1. **Centerpiece = split two-pane.** Left = the CTO's narrated arc; right = the
   live tamper-evident ledger with an INTACT/TAMPERED chain badge. The audience
   sees the agent act *and* the audit trail prove it, side by side.
2. **Drive mode = live, with a recorded fallback.** `Run live` runs the real
   7-beat arc against the deployed CTO agent and streams each beat as it lands;
   every run is captured to a transcript, and `Replay last good run` plays the
   last capture instantly, network-independent. If the room's network or
   ollama.com hiccups, present the recording — same view either way.
3. **Agent pane depth = rich (collapsible).** Each beat shows the question, a
   collapsible harness trace (planned N steps · todos · subagent spawns · tool
   calls), the agent's answer, and a large outcome chip
   (READ-ONLY / DEFERRED / REFUSED / EXECUTED). Sells that this is a grounded,
   planning, tool-using operator — not a chatbot.
4. **Tech = Streamlit, run from the host.** Matches the house console pattern
   (`cto/console.py` is already Streamlit). A live run must stage the incident
   and port-forward into the cluster exactly the way `run-demo.sh` already does,
   so the console runs on the host, not in-cluster.
5. **The UI does not re-implement staging.** `Run live` shells out to the
   existing, PR'd `demos/08-cto/run-demo.sh --no-up`. The only new seam is a
   `--emit-jsonl <path>` option threaded down into the shared `demos/_driver.py`
   so each beat is also written as one structured JSON line. The UI tails that
   JSONL — clean structured data, not stdout scraping. **That JSONL file is the
   recording.**
6. **Model-agnostic.** The console drives whatever the deployed CTO agent serves
   (kimi-k3 when credit is enabled, else the kimi-k2.6 fallback). No model logic
   in the console.

## Non-goals (YAGNI)

- A general agent chat surface. `cto/console.py` already covers free-form chat;
  this is a *demo presenter*, not a replacement for it.
- Generalising to a COO/CFO presenter now. Build CTO-only, but put the reusable
  bits (the `--emit-jsonl` emitter in the shared `_driver.py`, a ledger-read
  helper) where a future presenter can reuse them without a rewrite.
- In-cluster deployment, auth, multi-user. Host-local, projector-facing, one
  presenter. Same posture as `run-demo.sh`.
- Re-implementing incident staging, cluster mutation, or the beat arc. All of
  that stays in `run-demo.sh` / `drive.py`, which are already tested and merged.
- Animations beyond a simple card reveal and the ledger auto-refresh.

## Architecture

Additions only. Two small seams plus one new Streamlit app. Nothing in the CTO
agent, the levers, or the ledger schema changes.

### A. Structured beat stream — `demos/_driver.py` (shared)

`_driver.run(BEATS, ...)` already drives the beats and collects harness
introspection for its pretty stdout. Add an **optional** `emit_jsonl: str | None`
parameter (surfaced as a `--emit-jsonl <path>` flag on `drive.py` and passed
through by `run-demo.sh`). When set, after each beat the driver appends one JSON
object (one line) to the file:

```json
{
  "beat": 7,
  "title": "Autonomous recovery — the CTO rolls back a bad revision",
  "shows": "the CTO doesn't just report: it ACTS…",
  "question": "cfo is crashlooping on a bad rollout. Fix it…",
  "harness": {"planned": 0, "todos": 0, "subagents": 0,
              "tools": ["platform_health", "execute_rollback"]},
  "answer": "I executed a rollback on the cfo deployment…",
  "outcome": {"kind": "executed", "detail": "rolled_back_to rev 28"},
  "ledger_seq": 32,
  "ts": "2026-08-12T20:37:28Z"
}
```

`outcome.kind` ∈ `read_only | deferred | refused | executed`, derived from the
same signals `drive.py` already prints. Default (`emit_jsonl=None`) is unchanged
behaviour — the JSONL is purely additive, so the existing terminal demo and its
tests are untouched.

### B. Ledger read helper — `demos/08-cto/present/ledger.py` (pure)

A thin, pure module wrapping the exact query `inspect-ledger.sh` already runs
(via `kubectl exec` into the postgres pod). Two functions:

- `read_rows() -> list[LedgerRow]` — seq, ts, actor, action, outcome,
  deployment, detail, truncated `prev_hash`/`entry_hash`.
- `chain_verdict() -> ("INTACT" | "TAMPERED", first_bad_seq | None)` — runs
  `verify_agent_ledger()`.

Kept pure (subprocess in, parsed rows out) so it is unit-testable against a
recorded psql payload and reusable by a future COO/CFO presenter. The `st.*`
rendering lives in the app, not here.

### C. The console — `demos/08-cto/present/app.py` (Streamlit)

`st.columns([3, 2])`: left **Agent CTO**, right **Audit ledger**, with a top
control bar.

**Left pane — beat cards.** One card per beat read from the JSONL: title, the
question, a collapsible harness trace (`planned N · todos N · subagent → … ·
tools: …`), the answer (trimmed, expander for full), and a big outcome chip.

**Right pane — live ledger.** `ledger.read_rows()` on an auto-refresh (~2 s),
newest `cto` rows highlighted, and a large chain badge from
`ledger.chain_verdict()`: **● INTACT** / **✖ TAMPERED (seq N)**. A `Tamper demo`
button attempts an `UPDATE`/`DELETE` (reusing `inspect-ledger.sh --tamper-demo`)
and shows the DB reject it.

**Control bar.**
- `▶ Run live` — spawns `run-demo.sh --no-up --emit-jsonl <tmp>` as a subprocess,
  tails `<tmp>` to fill the left pane card-by-card while the right pane polls the
  ledger. On completion, `<tmp>` + a ledger snapshot + chain verdict are saved to
  `present/recordings/<ts>.json`.
- `⏮ Replay last good run` — loads the newest recording and plays its cards on a
  timer; the ledger pane shows the recording's **snapshot** (so beats and ledger
  stay consistent when nothing real is happening).
- `Tamper demo` — as above.

### D. Recording format — `demos/08-cto/present/recordings/`

A recording is a single JSON file: `{beats: [...jsonl...], ledger_snapshot:
[...rows...], chain: "INTACT", captured_at}`. Live runs write here; `Replay`
reads the newest. **One canonical recording is committed** so `Replay` works on a
fresh checkout before any live run (guaranteed fallback for the talk); other
recordings are gitignored.

## Data flow

```
▶ Run live
  app ── spawn ──► run-demo.sh --no-up --emit-jsonl /tmp/run.jsonl
                     ├─ stage cfo bad rollout (host → port-forwarded cluster)
                     └─ drive.py runs 7 beats → append 1 JSON line/beat
  app ── tail /tmp/run.jsonl ─────────────► left pane fills, card by card
  app ── ledger.read_rows()/verdict (2s) ─► right pane fills + chain badge
  run ends ─► save {beats + ledger snapshot + verdict} → recordings/<ts>.json

⏮ Replay
  app ── read newest recording ──► cards on a timer; ledger pane = snapshot
```

Live → ledger pane shows the real DB. Replay → ledger pane shows the snapshot.

## Error handling

- **Live run errors** (network / K3 billing / a stalled beat): the failing beat
  card shows the error, both panes stay, and `Replay last good run` is one click
  away — this is exactly why the fallback exists.
- **Ledger query fails** (port-forward down): right pane shows "ledger
  unavailable", no crash; a hint says to check the port-forward.
- **No recording yet**: `Replay` is disabled with a hint ("run live once to
  capture, or ship the committed canonical recording").
- **`run-demo.sh`'s own restore** still runs on exit (its EXIT trap), so an
  aborted live run leaves `cfo` recovered regardless of the console.

## Testing (stub-first, matching the suite)

- **Driver JSONL emitter (unit):** a scripted-model run (reuse the existing
  `csuite` fakes) with `emit_jsonl` set writes one well-formed JSON object per
  beat with the right `outcome.kind`; default (unset) writes nothing and leaves
  existing behaviour identical.
- **Ledger helper (unit):** parse a recorded psql payload into `LedgerRow`s;
  map an intact verifier result to `INTACT`, a tampered one to `TAMPERED` + seq.
- **App logic (unit):** the pure parts — JSONL → beat view-models, recording
  round-trip (save → load), "no recording" / "ledger unavailable" states. The
  `st.*` rendering stays thin and is not unit-tested.
- **Manual live smoke (once):** one real `Run live` end-to-end against the
  deployed agent (k2.6 today, k3 once credit is on); confirm the left pane fills,
  the ledger badge shows INTACT, the tamper button is rejected, and a recording
  is written and replays.

## Build order

1. Seam A (`--emit-jsonl` in `_driver.py` + `drive.py` + `run-demo.sh`
   passthrough) — smallest, unblocks everything, independently testable.
2. Seam B (`ledger.py` pure helper).
3. The Streamlit app (C) wiring A + B into the two-pane view, then recording +
   replay (D). Capture and commit one canonical recording last.

## Files

- `demos/_driver.py` — add `emit_jsonl` param + per-beat JSON emission (shared).
- `demos/08-cto/drive.py` — `--emit-jsonl` flag passthrough.
- `demos/08-cto/run-demo.sh` — forward `--emit-jsonl` to `drive.py`.
- `demos/08-cto/present/app.py` — the Streamlit console.
- `demos/08-cto/present/ledger.py` — pure ledger read + chain verdict.
- `demos/08-cto/present/recordings/<ts>.json` — one committed canonical
  recording; the rest gitignored.
- `demos/08-cto/present/README.md` — how to run for a talk.
- `demos/08-cto/present/tests/` — unit tests for A, B, and the app's pure logic.
