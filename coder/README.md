# coder — the CTO's engineering seat (Phase C)

The **coder** is the in-cluster service the Agent CTO delegates a scoped coding
task to. It authors the change in a dedicated **sandbox repo**
(`bvcmartins/cto-sandbox`), verifies it against that repo's own pytest suite, and
opens a **PR-gated pull request** — a human reviews and merges. The CTO/coder
**never merge**.

Design spec: `docs/superpowers/specs/2026-08-12-agent-cto-phase-c-coding-agent-design.md`.
Plan: `docs/superpowers/plans/2026-08-13-agent-cto-phase-c-coding-agent.md`.

## What it is

- `coding_agent.py` — a **port** of `agentic_patterns/.../coding_agent_gemini.py`,
  structure-for-structure. **Only** the model factory is swapped Gemini → kimi/ollama
  (`model_factory.py`, reusing the CTO's ollama credentials — no new API keys).
- `service.py` — the repo-level orchestration: clone the sandbox → drive the coder
  in **agentic** mode against the repo's tests → self-verify gate → branch/commit/
  push/`gh pr create`.
- `api.py` / `api_main.py` — FastAPI on **`:8096`**: `POST /code-task {kind, task}`,
  plus `/livez` and `/health`.

## The contract

`POST /code-task {"kind": "remediation"|"delivery", "task": "..."}` →
`{"outcome": "executed"|"failed", "pr_url": ..., "branch": ..., "tests": ..., "summary": ...}`.
`executed` means **a PR was opened** (not merged). Repo tests red after the coder's
rounds → `failed`, no PR.

## One-time provisioning

1. **Create the sandbox repo** from the seed (needs `gh` with `repo` scope):
   ```bash
   SANDBOX_REPO=bvcmartins/cto-sandbox coder/sandbox-seed/provision-sandbox.sh
   ```
   This creates the repo, commits the baseline (a helper service with two
   intentional, fixable gaps), and pushes a `baseline` tag.

2. **Mint a repo-scoped gh token** and store it as a k8s secret:
   ```bash
   kubectl --context kind-nano-bank -n nano-bank \
     create secret generic coder-gh-token --from-literal=token=<GH_PAT>
   ```

3. **Confirm cluster egress** to `github.com` and `ollama.com` from the
   `nano-bank` namespace.

## Deploy

```bash
export XDG_RUNTIME_DIR=/run/user/1000 XDG_DATA_HOME=/home/bmartins/.local/share
./coder/k8s/deploy.sh
# then redeploy platform-mcp so it picks up the delegate_coding_task tool:
kubectl --context kind-nano-bank -n nano-bank rollout restart deploy/platform-mcp
```

## Guardrails

- **Sandbox only.** The service is pinned to one repo; the lever has no repo
  argument, so there is no other target to choose.
- **Self-verify.** No red PRs — the repo's own pytest must be green.
- **Human-merge gate.** The coder opens the PR; a person merges it.
- **Audited.** Every delegation is one row in the tamper-evident
  `agent_action_ledger` (written by the CTO's `delegate_coding_task` lever).

## Tests

```bash
python -m pytest coder/tests/ -q          # offline: selftest, fake model + temp repo, TestClient
python -m coder.coding_agent --selftest   # the ported offline self-test (no backend)
```
