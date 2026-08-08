# Agent CTO — Phase A: observability seat (design)

**Date:** 2026-08-07
**Status:** design approved, spec under review
**Branch:** a new `agent-cto` branch (kept separate from the large `agent-coo`/PR #55 line; base confirmed at plan time)

## Context

nano-bank runs itself via autonomous **C-suite agents** — a thin LangGraph agent
(kimi-k2.6 via Ollama cloud) whose only domain tools are a domain **MCP** that
reads a data source and computes everything in **pure, unit-tested Python** (the
model never does arithmetic), wrapped in the shared **`csuite`** harness
(planning / todos / subagents / context / Qdrant memory) with a grounding
**verifier**, a `POST /ask` A2A endpoint, a Streamlit console, and a k8s
deployment. The **CFO** (over the `finance/` MCP) and **COO** (over the
`operations/` MCP) are live. The CTO is the next seat; CEO/CXO follow.

The full CTO mandate (agreed) spans three **independent subsystems**, each its
own spec → plan → build:

- **A — Observability** (this spec): the analyst CTO over a hybrid technical
  surface.
- **B — Infra levers** (later): audited k8s operations (rollout restart / scale /
  rollback), same self-verify + tamper-evident-ledger guardrail as the COO.
- **C — Coding agent** (later): a **separate entity the CTO calls** — a
  PR-gated, Claude-Code-style plan→edit→test→commit→PR harness. Not embedded in
  the CTO; its own service, wired in as a tool later.

This document specifies **Phase A only**.

## Goal

A **CTO analyst seat**: an agent that observes the bank's technical platform —
**reliability** (service/pod health, crashloops, restarts) and **delivery**
(rollout status, image/version drift) — across the bank's full kube estate, and
answers grounded questions and makes recommendations. It **takes no action** and
**writes no code** in Phase A.

Non-goals (Phase A): acting on infra (Phase B); writing code (Phase C); the
books (CFO's lane); money-movement operations detail (COO's lane); fraud.

## Design

### Estate

The bank's full kube estate: the **`nano-bank`** kind cluster (bank-api, coo,
cfo, operations-mcp, finance-mcp, postgres, agent-qdrant, …) **and** the
**`modern-core`** kind cluster (the GL core). Contexts: `kind-nano-bank`,
`kind-modern-core`.

### Data source — hybrid

1. **k8s API reads** (both clusters): deployments, pods, replicasets, events.
2. **Service `/health` self-reports** (HTTP): each service's `/health` (bank-api
   `:8081`, coo `:8093`, cfo `:8089`, operations-mcp, finance-mcp) — the services
   already expose dependency-probe health.

### `platform/` MCP (new, mirrors `operations/`)

`nano-platform` FastMCP server on **:8094**. Files:

- **`config.py`** — `Settings.from_env()`: MCP port, the kubeconfig path, the
  list of `(context, cluster_label)` to read, the list of
  `(service_label, health_url)` to probe, timeouts.
- **`k8s_client.py`** — `K8sClient`: read-only reads over the configured
  contexts using the official `kubernetes` Python client. Methods:
  `deployments()`, `pods()`, `replicasets()`, `events()` — each returns plain
  dicts tagged with `cluster` and `namespace`. A `transport`/loader seam lets
  tests inject a fake (no live cluster).
- **`health_client.py`** — `HealthClient.probe()`: `httpx.get` each configured
  `/health`, returns `{service, ok, status, checks, error?}`; never raises (a
  down service is data, not an exception).
- **`metrics.py`** (pure, dict-in/dict-out, unit-tested):
  - `estate_health(deployments)` → per-deployment `{cluster, name, desired,
    ready, available, updated, unavailable}` + a rollup (`total`, `healthy`,
    `degraded` where `ready < desired`).
  - `restarts(pods)` → per-pod restart totals + `crashlooping` list (containers
    in CrashLoopBackOff or with restarts over a threshold) + a total.
  - `rollouts(deployments, replicasets)` → per-deployment rollout state
    (`progressing` / `complete` / `stalled`, updated-vs-desired) — the delivery
    signal.
  - `versions(deployments)` → per-deployment container image tag(s); flags
    `drift` where the same app image tag differs across the estate.
  - `service_health(probes)` → the `/health` rollup: `healthy` / `unhealthy`
    lists + failing dependency probes.
  - `platform_health(...)` → one-shot bundle of the above.
  - `compute(operation, values)` — the shared deterministic-arithmetic tool
    (verbatim from `operations/metrics.py`), so any derived figure stays
    tool-grounded.
- **`mcp_server.py`** — tools: `estate_health`, `restarts`, `rollouts`,
  `versions`, `service_health`, `platform_health`, `compute`. Decimals/objects
  stringified for transport (reuse `operations/`'s `_stringify`).
- **`Dockerfile`**, **`.dockerignore`**, **`requirements.txt`**
  (`kubernetes`, `httpx`, `mcp>=1.2,<2`, `mcp` FastMCP), **`k8s/`** manifest,
  **`verify-platform.sh`**, **`tests/`**.

### Cross-cluster access

The MCP runs in the `nano-bank` cluster but must read both clusters:

- A read-only **ServiceAccount** in each cluster with a `ClusterRole`
  (`get`/`list`/`watch` on `deployments`, `replicasets` (apps), `pods`,
  `events` (core)) bound via `ClusterRoleBinding`.
- Each SA's token + the cluster CA + API server URL are assembled into **one
  kubeconfig** with two contexts (`kind-nano-bank`, `kind-modern-core`), stored
  as a **Secret** (`nano-platform-kubeconfig`) mounted at a fixed path; the MCP
  reads it via `config.py`. (The modern-core API server must be reachable from
  the nano-bank cluster — kind clusters share the host docker network; the
  kubeconfig uses the reachable endpoint, not `127.0.0.1`.)
- A `platform/k8s/make-kubeconfig.sh` script mints the SAs, extracts tokens/CAs,
  and writes the Secret. Documented as a one-time setup step (the operator runs
  it; it needs both clusters).

### `cto/` agent (new, mirrors `coo/`)

- **`config.py`** — ports (agent **:8095**, console **:8509**),
  `platform_mcp_url`, qdrant/memory, harness knobs.
- **`model_factory.py`** — kimi-k2.6 via `ChatOpenAI` @ `ollama.com/v1` (copy of
  `coo/model_factory.py`; the C-suite's current main model).
- **`trace.py`**, **`verifier.py`** — reused from `csuite` (numeric grounding is
  domain-agnostic; it already carries the round-threshold-exemption fix).
- **`claims.py`** — retargeted for the CTO: **phantom concepts** are the books
  (P&L / NIM / RAROC → "that's the CFO's") and money-movement operations detail
  (float / rail throughput / settlement → "that's the COO's") and fraud/AML;
  disclaimer-aware, same structure as the COO's `claims.py`. The COO's *window*
  grounding (24h/7d/30d) does **not** apply — the platform reads are
  point-in-time snapshots — so the CTO's `claims.py` drops window grounding and
  keeps only phantom-concept guarding.
- **`tools.py`** — a `MultiServerMCPClient` over the platform MCP.
- **`agent.py`** — `CTO_PROMPT` + harnessed `ask()` / `ask_stream()` returning
  `{answer, thread_id, trace, verification}`. Prompt: Phase-A **analyst** —
  observe reliability + delivery, quote every figure exactly as a tool returned
  it, use `compute` for any derived figure, stay in the platform lane (defer the
  books to the CFO and money-ops to the COO), and **take no action / write no
  code** (Phase B and the coding entity come later); use the harness
  (plan/todos/recall/record/subagent).
- **`api.py`** / **`api_main.py`** — `/ask`, `/ask/stream`, `/livez`, and a
  3-probe `/health` (ollama / platform-mcp / qdrant), degrade-not-500.
- **`console.py`** — the shared `csuite.console_ui.run_console` one-liner (a
  richer single-pane "chat + live estate" console is a later demo, not Phase A).
- **`Dockerfile`** (built from repo root to bundle `csuite`), **`k8s/cto.yaml`**
  + **`k8s/deploy.sh`** (mirrors `coo/k8s/deploy.sh`; reuses `nano-agent-secrets`
  + `agent-qdrant`), **`README.md`**, **`verify-cto.sh`**.

### Scope boundary (the CTO's lane)

Technical platform health + delivery **only**. The `claims.py` phantom guard
refuses the books, money-movement operations detail, and fraud, offering the
technical context it can actually see instead — the same discipline the COO/CFO
already demonstrate in their own lanes.

## Testing

- **`platform/tests/`**: pure `metrics` unit tests (fake deployments/pods/
  replicasets/probe dicts → asserted rollups: a degraded deployment, a
  crashlooping pod, a stalled rollout, a version drift, an unhealthy service);
  `k8s_client` / `health_client` via injected fakes (no live cluster).
- **`cto/tests/`**: offline agent tests (scriptable fake LLM + fake MCP +
  in-memory Qdrant), mirroring `coo/tests` — a grounded estate review, a
  scope-refusal (a books question deferred to the CFO), `compute` used for a
  derived figure.
- **Live smoke** (`verify-cto.sh` / `platform/verify-platform.sh`): the platform
  MCP reads both real clusters and returns a real `platform_health`; the CTO
  answers "give me an estate health review" grounded, and defers a NIM question
  to the CFO.

## Rollout

- Build + load `nano-platform-mcp:dev` and `nano-cto:dev` (CTO from repo root to
  bundle `csuite`); `kind load` into `nano-bank`.
- One-time `platform/k8s/make-kubeconfig.sh` to mint the cross-cluster
  read-only kubeconfig Secret.
- `kubectl apply` the platform-mcp + cto manifests; rollout; `/health` green on
  all three probes.
- Ports added: platform MCP **8094**, CTO agent **8095**, CTO console **8509**.

## Isolation / boundaries

- **`platform/` MCP** is one bounded unit: reads (k8s + health) in, pure metric
  rollups out; the only new capability (cluster reads) is isolated behind
  `k8s_client` with an injectable seam.
- **`cto/` agent** is thin over `csuite`; the only CTO-specific code is the
  prompt, the retargeted `claims.py`, and config — everything else is reused.
- **Cross-cluster credentials** live in one mounted, read-only kubeconfig Secret;
  the agent stack never holds cluster-admin.
- Phases B and C plug into this seat later without reshaping it (levers add an
  acting surface; the coding agent is an external tool the CTO calls).
