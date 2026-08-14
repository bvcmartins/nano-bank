# coder — the CTO's engineering seat (Phase C)

The **coder** is the in-cluster service the Agent CTO delegates a scoped coding
task to. It authors the change in a dedicated **sandbox repo**, verifies it
against that repo's own pytest suite, and publishes a **review branch a human
merges** — the CTO/coder **never merge**.

## Sandbox modes

- **`local` (default, no GitHub).** The sandbox is a **bare git repo on a PVC**
  mounted at `/sandbox`, seeded once by the manifest's initContainer from
  `coder/sandbox-seed/`. On green tests the coder pushes a branch `cto/<slug>-<ts>`
  back to it; the "PR" is that local branch. No `gh`, no token, no GitHub egress.
- **`github` (opt-in, `SANDBOX_MODE=github`).** Clones `bvcmartins/cto-sandbox`
  and opens a real PR with `gh pr create` (needs the `coder-gh-token` secret +
  egress to github.com). Same self-verify gate and human-merge rule.

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

## Deploy (local mode — the default)

No GitHub, no token. The initContainer seeds the PVC-backed bare repo on first
start.

```bash
export XDG_RUNTIME_DIR=/run/user/1000 XDG_DATA_HOME=/home/bmartins/.local/share
./coder/k8s/deploy.sh
# then redeploy platform-mcp so it picks up the delegate_coding_task tool:
kubectl --context kind-nano-bank -n nano-bank rollout restart deploy/platform-mcp
```

### Reviewing / merging a delegated change (local mode)

```bash
kubectl -n nano-bank exec deploy/coder -- git -C /sandbox log --oneline --all
kubectl -n nano-bank exec deploy/coder -- git -C /sandbox diff main..<branch>
kubectl -n nano-bank exec deploy/coder -- git -C /sandbox merge --ff-only <branch>
```

`demos/08-cto/reseed-sandbox.sh` (called by `run-demo.sh`) drops stale `cto/*`
branches before a run.

### A literal host repo (optional)

To back `/sandbox` with a bare repo on your disk instead of a PVC, recreate the
kind cluster with an `extraMounts` entry mapping e.g. `~/dev/cto-sandbox.git` to a
node path, provision it with `coder/sandbox-seed/provision-local-sandbox.sh`, and
swap the manifest's `sandbox` volume for a `hostPath`.

## github mode (opt-in)

Set `SANDBOX_MODE=github` and, as one-time provisioning:

1. **Create the repo** from the seed (needs `gh` with `repo` scope):
   `SANDBOX_REPO=bvcmartins/cto-sandbox coder/sandbox-seed/provision-sandbox.sh`
2. **Mint a repo-scoped gh token** as a secret:
   `kubectl -n nano-bank create secret generic coder-gh-token --from-literal=token=<GH_PAT>`
   and mount it (`GH_TOKEN_PATH=/etc/coder/gh-token`).
3. **Confirm egress** to github.com + ollama.com.

## Guardrails

- **Sandbox only.** The service is pinned to one repo; the lever has no repo
  argument, so there is no other target to choose.
- **Self-verify.** No red review branches — the repo's own pytest must be green.
- **Human-merge gate.** The coder publishes a review branch; a person merges it.
- **Audited.** Every delegation is one row in the tamper-evident
  `agent_action_ledger` (written by the CTO's `delegate_coding_task` lever).

## Tests

```bash
python -m pytest coder/tests/ -q          # offline: selftest, fake model + temp repo, TestClient
python -m coder.coding_agent --selftest   # the ported offline self-test (no backend)
```
