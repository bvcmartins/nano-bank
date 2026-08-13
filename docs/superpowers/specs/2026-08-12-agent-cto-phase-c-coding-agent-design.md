# Agent CTO — Phase C: the PR-gated coding agent

**Status:** design approved 2026-08-12. Successor to Phase A (analyst) and Phase B
(infra levers: `execute_rollout_restart` / `execute_rollback`).

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
  the dedicated sandbox repo** (allow-list enforced at the lever).
- No autonomous multi-repo or free-form code changes. One sandbox, scoped tasks.

## Decisions (locked in brainstorming)

| Question | Decision |
|---|---|
| Narrative | Both incident-remediation **and** delivery — one capability, two beats. |
| Coder engine | A **new thin kimi/ollama coder** (same stack as the CTO; no new API keys). |
| Artifact | A **real GitHub PR**. |
| Target repo | A **dedicated sandbox repo**, reseeded each run. |
| Coder home | An **in-cluster service** (the "engineering seat"), called by the CTO's lever over HTTP. |
| Lever home | Registered in `platform_mcp` (reuses the CTO's already-wired audit/writer path). |
| Sandbox language | **Python / pytest** (slim coder image, fast test loop). |

## Architecture

```
CTO agent (kimi)                      platform_mcp (:8094)                coder service (:8096)         cto-sandbox (GitHub)
  /ask ─► delegate_coding_task ─────►  @tool delegate_coding_task ──HTTP─► POST /code-task ─┐
                                        (allow-list + precondition           clone sandbox   │
                                         self-verify; writes ledger)          run coder loop  │
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

### 2. The coder service — `coder/`

A thin LangGraph react agent (kimi via ollama) with a **narrow tool surface**:
`read_file`, `apply_patch`, `run_tests`. The **model only proposes the code
change**. Everything privileged and deterministic is Python around the loop —
mirroring the CTO's "model reasons; Python acts, verifies, audits" split:

- `POST /code-task {kind, task}`:
  1. clone the sandbox into a temp workdir,
  2. run the coder loop (bounded steps) so the model edits files + reads test output,
  3. **run the tests** (deterministic),
  4. **self-verify gate:** if tests fail → return `{"outcome":"failed", ...}` with
     no PR; if green → branch `cto/<slug>-<ts>`, commit, push, `gh pr create`,
     return `{"outcome":"executed","pr_url":...,"branch":...,"tests":...,"summary":...}`.
- FastAPI with `/livez` + `/health`, same shape as the other C-suite seats.
- k8s manifests (`coder/k8s/`). The image bundles `git`, `gh`, and python. A
  **gh token** is a k8s secret; kimi creds come from the same ollama secret the
  CTO uses. Egress to github.com + ollama.com required.

Pure, unit-testable helpers are separated from IO: task→branch-slug, the
`gh pr create` argument builder, and the result-shaping (`code_task_result`).

### 3. The CTO's delegation lever — in `platform_mcp/mcp_server.py`

A new **acting tool**, registered only when the write path is wired (exactly like
the Phase B levers):

```python
@mcp.tool()
def delegate_coding_task(kind: str, task: str) -> dict:
    """Delegate a scoped coding task to the engineering coder, which opens a
    PR-gated pull request against the sandbox repo. REFUSED unless the target is
    on the coder allow-list (sandbox only) and, for kind='remediation', an actual
    failing/degraded signal is observed. Autonomous + audited; a human merges the
    PR. Report the PR link verbatim."""
```

- **Allow-list:** sandbox repo only. Any other target → refused.
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

Guardrails: **allow-list** (sandbox only) · **self-verify** (no red PRs) ·
**human-merge gate** · every delegation **audited** in the tamper-evident ledger.

Testing:
- Pure helpers (branch-slug, pr-arg builder, `code_task_result`, `beat_outcome`
  `delegated` case, `outcome_style`) — unit tests.
- Coder service — a **fake model + a temp git repo** (no network): verifies the
  loop edits files, the test-gate opens a PR only on green, and refuses-on-red
  with no PR. `gh` and `push` are seams stubbed in the test.
- Lever — a **fake coder client**: self-verify refusal, allow-list refusal, and
  the exact ledger record on each of executed/refused/failed.
- One **live smoke**: a real run producing a real PR against `cto-sandbox`, then
  reseed.

## Ports

- coder service: `:8096` (next free after platform_mcp `:8094`, cto `:8095`).

## Open provisioning steps (one-time, documented in coder/README + demo README)

1. Create `bvcmartins/cto-sandbox` with the baseline defect + stub and a
   `baseline` tag.
2. Mint a gh token scoped to that repo; store as the `coder_gh_token` k8s secret.
3. Confirm cluster egress to github.com + ollama.com.
