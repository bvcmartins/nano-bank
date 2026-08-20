# Agent CTO — Phase C: the PR-gated coding agent

**Status:** design approved 2026-08-12. Successor to Phase A (analyst) and Phase B
(infra levers: `execute_rollout_restart` / `execute_rollback`).

**Update 2026-08-13 (local sandbox mode + containment):** the artifact is
configurable via `SANDBOX_MODE`; **default `local`** — the sandbox is an
**in-cluster bare git repo on a PVC** (seeded by an initContainer). A delegated
change is published as a **review branch a human merges** (host-initiated via
`kubectl exec`), no GitHub / `gh` / token. `github` mode (real `gh pr create`) is
opt-in. **Containment (the coder runs model-authored code, treated as hostile):**
(1) no host mount — other repos/personal files aren't in the pod; (2) only
`OLLAMA_API_KEY` injected, scrubbed from every subprocess that runs model code
(`sandbox_env()`); (3) pod non-root, read-only rootfs, all caps dropped, no k8s API
token; (4) network — the coder needs no host access, and `coder/k8s/egress-firewall.sh`
(host iptables in DOCKER-USER; kindnet doesn't enforce NetworkPolicy) denies every
kind pod from reaching the host + LAN while keeping pod-to-pod + internet. "Open a
PR" reads as "publish a gated review branch" in local mode.

## Goal

Give the CTO a way to **delegate a coding task to a coding agent** and have that
agent open a **real, PR-gated pull request**. One capability, two narratives:

1. **Incident remediation** — after the CTO rolls back a stalled bad rollout
   (Phase B), it delegates the *durable root-cause code fix* as a gated PR.
   Rollback stops the bleeding; the code change is the real remedy.
2. **Delivery** — the CTO is handed a backlog task and delegates its
   implementation as a gated PR.

Both are the same lever invoked twice with a different `kind` + `task`.

The README's forward reference — *"a separate PR-gated coding agent the CTO calls
is Phase C"* — is what this builds.

## Non-goals

- The CTO/coder never **merge**. A human reviews and merges the PR — that is the
  gate.
- The coder never touches the nano-bank repo or anything in prod. It edits **only
  the dedicated sandbox repo**, and this is **structural, not validated**: the lever
  takes no repo argument, so there is no other target to choose. The coder service is
  pinned to the sandbox by its own `SANDBOX_CLONE_URL`.
- No autonomous multi-repo or free-form code changes. One sandbox, scoped tasks.

## Decisions (locked in brainstorming)

| Question | Decision |
|---|---|
| Narrative | Both incident-remediation **and** delivery — one capability, two beats. |
| Coder engine | A **port of `coding_agent_gemini.py`** with only the model-factory seam swapped Gemini → kimi/ollama (same stack as the CTO; no new API keys). |
| Artifact | A **real GitHub PR**. |
| Target repo | A **dedicated sandbox repo**, reseeded each run. |
| Coder home | An **in-cluster service** (the "engineering seat"), called by the CTO's lever over HTTP. |
| Lever home | Registered in `platform_mcp` (reuses the CTO's already-wired audit/writer path). |
| Sandbox language | **Python / pytest** (slim coder image, fast test loop). |

## Architecture

```
CTO agent (kimi)                      platform_mcp (:8094)                coder service (:8096)         cto-sandbox (GitHub)
  /ask ─► delegate_coding_task ─────►  @tool delegate_coding_task ──HTTP─► POST /code-task ─┐
                                        (structural sandbox pin +            clone sandbox   │
                                         precondition; writes ledger)        run coder loop  │
                                                                              run tests ◄─────┘
                                                                              tests green?
                                                                                ├─ yes ─► branch+commit+push+gh pr create ─► PR url
                                                                                └─ no  ─► failed (no PR)
  ◄──────────────────── {outcome, pr_url, tests, summary} ◄──────────── {pr_url|failed} ◄──┘
        │
        └─► agent_action_ledger row (action=delegate_coding_task,
             outcome=executed|refused|failed, effect={pr_url,branch,tests,summary})
```

### 1. The sandbox repo — `cto-sandbox`

A tiny real Python project (pytest), framed narratively as "a nano-bank helper
service." `main` is a stable **baseline** carrying two independent, real, fixable
things:

- **A defect** — e.g. a rounding / off-by-one bug a test catches. Target of the
  *remediation* task.
- **A stub + a missing (or xfail) test** — target of the *delivery* task.

**Reseed each run** (`reseed-sandbox.sh`, run from the demo driver, host-side):
- close any open `cto/*` demo PRs,
- delete stale `cto/*` branches (local + remote),
- confirm `main` is at the `baseline` tag (no force-push in the happy path, since
  the arc does not merge; a post-run reset restores baseline if a presenter merged).

The repo is created once (`bvcmartins/cto-sandbox`); provisioning is a documented
one-time step, not part of every run.

### 2. The coder — port of `coding_agent_gemini.py`, kimi/ollama backend

**The coding agent follows the exact pattern of
`~/dev/agentic_patterns/src/code_assistant/coding_agent_gemini.py`.** That module
is deliberately built so *only the model factory is backend-specific; everything
above it is backend-agnostic LangChain/LangGraph.* We port it into
`coder/coding_agent.py` and swap that one seam Gemini → kimi/ollama.

**Ported verbatim in structure (backend-agnostic — kept as-is):**
- `CodingAgent` class: `generate(task, test_code|criteria)`, `fix(task, code,
  feedback)`, and the `_solve_loop` that feeds the **verbatim test failure** back
  into the next attempt until green or `max_rounds`.
- The **two modes**: `direct` (architect→editor→verify — deliberate then
  transcribe) and `agentic` (a ReAct tool loop over the workspace).
- The **quality gates + tools**: `lint_python` (compile + bare-except),
  `_run_tests` (pytest), `write_code_to_disk` (lint-gated persistence), the
  `@tool` surface (`read_file`, `write_file`, `bash`, `write_code`, `run_python`,
  `run_tests`), and the `_safe_path` WORKSPACE sandbox.
- The **spec layer** (`compile_test_suite` / `spec_verify`), `CodeResult`, the
  **self-improvement seam** (`add_instruction` / `save_policy` / `load_policy`),
  and the offline `_selftest()` (no backend) + the `RichTracer` observability.

**Swapped (the one backend seam):** `_build_model` / `llm` / `backend_healthcheck`
are re-pointed at kimi via ollama, reusing the CTO's `model_factory` conventions
(`ChatOpenAI` at `https://ollama.com/v1`, the `reasoning`/`fast` role split, and
the primary→fallback `_candidates` resolution). No Gemini, no new API keys.

**Repo-level adaptation.** `coding_agent_gemini` solves single-file tasks into
`agent_code/solution.py`. For a real repo the service points `WORKSPACE` at the
**cloned sandbox checkout** and drives the coder in **`agentic` mode**, using the
**sandbox's own pytest suite** as the contract (the `_run_tests` gate runs the
repo's tests). So the coder reads the failing test, edits the repo file, and
re-verifies against the real suite — the same generate→verify epistemics, applied
to an existing codebase.

### 2b. The coder service — `coder/api.py`

A thin FastAPI wrapper around `CodingAgent` that adds the git/gh plumbing and the
network surface (the same shape as the other C-suite seats):

- `POST /code-task {kind, task}`:
  1. clone the sandbox into a temp `WORKSPACE`,
  2. run `CodingAgent(mode="agentic").generate/fix` against the repo's tests,
  3. **self-verify gate** (`_run_tests` green?): red → `{"outcome":"failed", ...}`,
     no PR; green → branch `cto/<slug>-<ts>` → commit → push → `gh pr create` →
     `{"outcome":"executed","pr_url":...,"branch":...,"tests":...,"summary":...}`.
- `/livez` + `/health` like the other seats.
- k8s manifests (`coder/k8s/`). The image bundles `git`, `gh`, python + pytest. A
  **gh token** is a k8s secret; kimi creds come from the same ollama secret the
  CTO uses. Egress to github.com + ollama.com required.

Pure, unit-testable helpers stay separate from IO: task→branch-slug, the
`gh pr create` argument builder, and the result-shaping (`code_task_result`) —
alongside the ones the port already unit-tests offline (`lint_python`,
`compile_test_suite`, `write_code_to_disk`, the self-improvement seam).

### 3. The CTO's delegation lever — in `platform_mcp/mcp_server.py`

A new **acting tool**, registered only when the write path is wired (exactly like
the Phase B levers):

```python
@mcp.tool()
def delegate_coding_task(kind: str, task: str) -> dict:
    """Delegate a scoped coding task to the engineering coder, which opens a
    PR-gated pull request against the sandbox repo. The sandbox is the ONLY target
    (structural — the lever takes no repo argument). For kind='remediation', REFUSED
    unless an actual failing/degraded platform signal is observed. Autonomous +
    audited; a human merges the PR. Report the PR link verbatim."""
```

- **Structural sandbox pin:** the sandbox is the only repo the coder can touch —
  the lever takes no repo argument, so there is nothing else to target. (Containment
  by construction, not a validated allow-list. Note: `settings.allow_list` *is*
  passed in the delegation path, but only as the set of **k8s deployments** to
  health-check for remediation — it is not a repo guard. The repo guard the
  restart/rollback levers use, `levers.is_allowed`, does not apply here because there
  is no repo parameter to guard.)
- **Precondition self-verify:** `kind="remediation"` requires an **observed
  platform failing signal** — the same k8s reads Phase B self-verifies against
  (a degraded/recently-stalled deployment). This ties remediation to the real
  incident; the fix is then *authored in the sandbox*, which stands in for that
  service's source (the platform reads do not — and need not — inspect the
  sandbox's own tests; the coder does that). `kind="delivery"` needs only a
  non-empty task.
- **Audited:** one `agent_action_ledger` row, `action="delegate_coding_task"`,
  `outcome ∈ {executed, refused, failed}`, `effect={pr_url,branch,tests,summary}`.
- The lever calls the coder over HTTP; a coder/network failure is `failed`, not a
  crash. The CTO reports the outcome verbatim and never merges.

### 4. "PR-gated" semantics

The coder opens the PR; a human reviews and merges. `executed` means *work
delegated + PR opened*, not merged. Tests-red → no PR, ledger `failed`, the CTO
reports it could not produce a safe change. This keeps the human firmly in the
loop for any code that could land.

### 5. Demo integration

Two beats appended to the CTO arc in `demos/08-cto` (`drive.py` BEATS +
`questions.md`):

- **Beat 8 — remediation:** after the rollback beat, the CTO delegates the durable
  root-cause fix → a real PR, audited. Honest framing: rollback recovered the
  running service; the code fix is the durable remedy.
- **Beat 9 — delivery:** the CTO is handed a backlog task and delegates it → a real
  PR, audited.

Both render in the presentation console for free (they are `beat_record`s). Add a
new **outcome kind `delegated`** → chip `DELEGATED` with the PR link as the detail
(extends `csuite.trace_view.beat_outcome` + `state.outcome_style`). The two new
`delegate_coding_task` rows appear in the live ledger pane like any other action.

`run-demo.sh` gains a reseed step (calls `reseed-sandbox.sh`) before driving the
new beats, and the EXIT trap restores the sandbox baseline alongside the cfo
restore it already does.

### 6. Guardrails & testing

Guardrails: **structural sandbox pin** (no repo argument to the lever) · **self-verify**
(no red PRs) · **human-merge gate** · every delegation **audited** in the tamper-evident ledger.

Testing:
- The port carries its own **offline `_selftest()`** (lint gate, fence/think
  stripping, write-code gate, `spec_verify` green/red, the self-improvement seam
  round-trip, graph compilation) — kept and run in CI with no backend.
- New pure helpers (branch-slug, pr-arg builder, `code_task_result`,
  `beat_outcome` `delegated` case, `outcome_style`) — unit tests.
- Coder service — a **fake model + a temp git repo** (no network): verifies the
  agentic loop edits repo files, the test-gate opens a PR only on green, and
  refuses-on-red with no PR. `gh` and `push` are seams stubbed in the test.
- Lever — a **fake coder client**: refusal on a bad `kind`/empty task and on a missing
  remediation signal, and the exact ledger record on each of executed/refused/failed.
  (No allow-list refusal test — there is no repo argument to reject; containment is
  structural.)
- One **live smoke**: a real run producing a real PR against `cto-sandbox`, then
  reseed.

## Ports

- coder service: `:8096` (next free after platform_mcp `:8094`, cto `:8095`).

## Open provisioning steps (one-time, documented in coder/README + demo README)

1. Create `bvcmartins/cto-sandbox` with the baseline defect + stub and a
   `baseline` tag.
2. Mint a gh token scoped to that repo; store as the `coder_gh_token` k8s secret.
3. Confirm cluster egress to github.com + ollama.com.
