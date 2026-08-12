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
