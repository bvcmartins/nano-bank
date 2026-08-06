# csuite: shared agent harness + CFO parity

**Status:** approved (design)
**Date:** 2026-08-06

## Problem

The COO (`coo/`) runs on a hand-rolled agentic harness (planning, todos,
subagents, context-compaction, durable memory) behind one `assemble()` seam,
plus a grounding verifier, a live run-tree streaming console, and a `compute`
tool. The CFO (`cfo/`) predates all of this: it is a plain
`create_react_agent` + a **near-verbatim copy** of the verifier/claims/trace.
The two verifiers will drift (a review finding). The harness was deliberately
built agent-agnostic so it could be lifted into a shared package and both agents
could share one engine.

## Goal

Extract the agent-agnostic engine into a shared `csuite/` package and rebuild the
CFO on it at **full parity** with the COO — killing the duplication.

## Design

### Shared package `csuite/` (new, top-level)

Agent-agnostic pieces, imported by both agents:

```
csuite/
  harness/        # moved from coo/harness: assemble, planning, todos, subagents,
                  #   context, memory, state, events
  trace.py        # TraceRecorder + merge
  trace_view.py   # to_steps, extract_highlights
  verifier.py     # the COO's improved grounding verifier (single copy)
  claims.py       # unsupported-claims checker
  runtime.py      # ask()/ask_stream(): the verify -> revise -> stream flow,
                  #   parameterized by (prompt, tools, memory, model, namespace)
  console_ui.py   # shared Streamlit render (esc, live st.status, run-tree) so
                  #   both consoles are thin
  tests/          # harness/verifier/trace tests (moved from coo/tests)
```

Each agent keeps only its **identity**: `agent.py` (its PROMPT + a thin call into
`csuite.runtime`), `config.py`, `model_factory.py`, `tools.py`,
`api.py`/`api_main.py`, a thin `console.py`, `k8s/`, `demo/`, agent-specific tests.

### What the CFO gains

Planning, todos, subagents, context-compaction, durable memory (namespace
`cfo`), the shared verifier + grounding, the **live run-tree streaming console**,
and a **`compute` tool added to the finance MCP** so derived figures (RAROC/NIM
ratios) stay tool-grounded. Behaviourally identical to the COO, over `finance/`.

### Duplication removed

Delete `cfo/{verifier,claims,trace}.py` and the copied ask/verify/revise flow;
both agents use `csuite`. Resolves the "two verifiers drift" finding permanently.

### Build

Both images build from the repo root (`docker build -f coo/Dockerfile
-t nano-coo:dev .`) copying `csuite/` + the agent dir. `deploy.sh` build commands
updated. `nano-cfo` built the same way.

## Phases (one PR, staged commits)

1. **Extract + repoint COO.** Create `csuite/`, move the pieces, repoint COO
   imports, extract the run-loop into `csuite.runtime` and console render into
   `csuite.console_ui` (COO console becomes thin). **COO behaviour unchanged.**
   `csuite/tests` + `coo/tests` + `operations/tests` green; COO rebuilds/redeploys.
2. **Back-port CFO.** `cfo/agent.py` -> `csuite.runtime` with `CFO_PROMPT` +
   finance tools; `cfo/api.py` gains `/ask/stream`; `cfo/console.py` uses
   `csuite.console_ui`; add `compute` to the finance MCP (+ `finance.metrics`);
   delete the CFO's duplicated modules; `cfo/tests` + `finance/tests` green;
   deploy the CFO stack (finance MCP + cfo) in-cluster and verify a grounded,
   streamed, planned CFO review.

## Testing & verification

- Unit: `csuite/tests`, `coo/tests`, `cfo/tests`, `operations/tests`,
  `finance/tests` all green.
- End-to-end (in-cluster): a CFO review that plans, calls finance tools +
  `compute`, streams its run tree, and grounds every figure; the COO unchanged.

## Non-goals

- No new CFO domain metrics.
- No COO behaviour change in phase 1.
- CEO/CTO agents (they inherit `csuite` for free later).
