# Agent CTO — Phase A (observability seat)

The **CTO** is a C-suite agent that observes the bank's **technical platform** and
answers grounded questions about it. Like the COO and CFO, it is a thin
[`csuite`](../csuite) agent: a LangGraph react agent (kimi-k2.6 via Ollama cloud)
whose only domain tools come from a domain MCP that reads a source and computes
everything in pure, unit-tested Python — the model never does arithmetic.

**Phase A is analyst-only: the CTO takes NO action on infrastructure and writes NO
code.** (Infra levers are Phase B; a separate PR-gated coding agent the CTO calls
is Phase C — each its own spec later.)

## What it sees — the platform MCP

The CTO's perception surface is [`platform_mcp/`](../platform_mcp) (`nano-platform`,
`:8094`), a hybrid read over the bank's full kube estate:

1. **k8s API reads** across **both** kind clusters (`kind-nano-bank` +
   `kind-modern-core`): deployments, pods, replicasets, events.
2. **Service `/health` self-reports** (HTTP) for the in-cluster services
   (bank-api, coo, cfo, operations-mcp, finance-mcp).

Its tools roll those up into:

- **reliability** — `estate_health` (desired/ready/degraded), `restarts`
  (crashloops), `service_health` (dependency probes);
- **delivery** — `rollouts` (complete/progressing/stalled), `versions` (image-tag
  drift across the estate);
- `platform_health` (one-shot bundle) and `compute` (deterministic arithmetic so
  any derived figure stays tool-grounded).

## Scope boundary (the CTO's lane)

Technical platform health + delivery **only**. A retargeted [`claims.py`](claims.py)
guard refuses the books (P&L / NIM / RAROC → the CFO's), money-movement operations
detail (float / rail throughput / settlement → the COO's), and fraud/AML — offering
the technical context it can actually see instead.

## Ports

| Component | Port |
|-----------|------|
| platform MCP (`nano-platform`) | 8094 |
| CTO agent A2A API | 8095 |
| CTO Streamlit console | 8509 |

## Running

Offline tests (no cluster, no LLM):

```bash
.venv/bin/python -m pytest platform_mcp cto -q
```

In-cluster (kind `nano-bank`):

```bash
# one-time: mint the cross-cluster read-only kubeconfig Secret (needs BOTH clusters)
./platform_mcp/k8s/make-kubeconfig.sh
# build + load + apply platform-mcp and cto (needs nano-agent-secrets + agent-qdrant)
./cto/k8s/deploy.sh
# live smoke
CTO_API_URL=http://localhost:8095 ./cto/verify-cto.sh
```
