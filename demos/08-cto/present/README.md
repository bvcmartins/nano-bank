# Agent CTO — presentation console

A presenter-paced, three-pane console for talks / screencasts:

- **left rail** — a **button per beat**, each captioned with *what that beat tests*
- **centre** — the selected beat's card (question → agent answer → harness → outcome)
- **right** — the live tamper-evident `agent_action_ledger` with a chain badge

Driven live via `run-demo.sh` (`--emit-jsonl`) with a recorded fallback you can
**replay beat-by-beat** — click a beat and it appears instantly (no waiting on the
model), which is the intended screencast flow.

## Run (from the host)

    export XDG_RUNTIME_DIR=/run/user/1000 XDG_DATA_HOME=/home/bmartins/.local/share
    uv pip install --python demos/08-cto/.venv/bin/python -r demos/08-cto/present/requirements.txt
    demos/08-cto/.venv/bin/streamlit run demos/08-cto/present/app.py \
      --server.port 8512 --server.address 0.0.0.0 --server.headless true

Open http://localhost:8512 (or the LAN URL streamlit prints).

## Controls

- **▶ Run live** — stages the incident + drives the shown beats against the deployed
  CTO (needs docker+kind+kubectl+uv and the stack up). Saves each run to `recordings/`.
- **⏮ Replay last good run** — plays the newest recording; network-independent. A
  canonical recording is committed so this works on a fresh checkout.
- **🔒 Tamper demo** — proves the ledger rejects UPDATE/DELETE.
- **▦ All beats** — the classic stacked view of the whole (shown) run.
- **Beat buttons** — click any beat to show just that beat; ✅ marks beats that have
  a result this session.

## Which beats are shown

A lean set is shown by default for a tight demo:

    CTO_SHOW_BEATS="1,7,8,9"    # grounded review → rollback recovery → 2 gated-PR delegations

Override the env var to show more/other beats (e.g. `"1,4,6,7,8,9"`). Nothing is
deleted — hidden beats stay in the driver and in recordings; a live run drives only
the shown beats (via `run-demo.sh --beats`), so trimming also shortens the live run.

The console never mutates the cluster itself — all staging lives in `run-demo.sh`;
the console runs it, reads the ledger, and renders.
