# Agent CFO

The bank's autonomous Chief Financial Officer. **Phase 1 is an analyst**: it
reads nano-bank's financial reports through the finance MCP service, computes
CFO metrics (RAROC, ROA/ROE, efficiency ratio, LDR, leverage, cost of funds,
yield on earning assets) and answers questions about the bank's financial
health. It is **read-only over the bank** — no money movement, no postings, no
DB access; the finance service stays the single source of financial truth.

Design: `docs/superpowers/specs/2026-07-20-agent-cfo-phase1-design.md`.

## How it works

```
CFO console :8506 ──HTTP──► CFO API :8089 ──MCP──► finance :8088 ──► bank :8081
                              (GLM-5.2 via Ollama, LangGraph react agent)
```

All arithmetic happens in `finance/metrics.py` (pure, unit-tested) and reaches
the agent as MCP tools — the model never computes a figure itself.

## Run locally

The finance MCP must be up on `:8088` (`python -m finance.mcp_server`).

```bash
OLLAMA_API_KEY=… python -m cfo.api_main               # A2A API on :8089
streamlit run cfo/console.py --server.port 8506      # chat console
```

Endpoints: `POST /ask` `{message, thread_id?}` → `{answer, thread_id, trace}`;
`GET /health`.

## Env

| var | default | meaning |
|---|---|---|
| `OLLAMA_API_KEY` | – | Ollama cloud key |
| `OLLAMA_BASE_URL` | `https://ollama.com/v1` | OpenAI-compat endpoint |
| `CFO_MODEL` | `glm-5.2` | model id |
| `FINANCE_MCP_URL` | `http://localhost:8088/mcp` | finance MCP |
| `API_PORT` / `CONSOLE_PORT` | `8089` / `8506` | ports |

The risk model behind RAROC is tuned on the **finance** side via `RISK_WEIGHT_*`,
`RISK_LOSS_*` and `RISK_TARGET_RATIO` (see `finance/config.py`).

## Deploy

`kubectl apply -f cfo/k8s/cfo.yaml` — reuses the existing `nano-agent-secrets`
secret for `OLLAMA_API_KEY` (created by `agent/k8s/deploy.sh`).

## Demo

`cfo/demo/` brings the whole stack up and seeds a bank full of events to talk
about — see `cfo/demo/README.md`:

```bash
bash cfo/demo/run-cfo-stack.sh && bash cfo/demo/seed-demo-bank.sh
```

## Smoke

`bash cfo/verify-cfo.sh` with the stack up (once per `CORE_BACKEND`).

## Roadmap

- **Phase 2** — budget planning and capital allocation (write tools, proposals).
- **Phase 3** — C-suite meetings with the CEO/COO/CTO agents over `/ask`.
- Spec #5 replaces the Basel-lite capital proxy with real economic capital,
  behind the same `raroc()` signature.
