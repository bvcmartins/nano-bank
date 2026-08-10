# Agent CTO — Phase B (Infra Levers) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the CTO two fully-autonomous, self-verifying, audited k8s recovery levers — `execute_rollout_restart` and `execute_rollback` — over stateless app deployments in both clusters, every attempt landing in the shared tamper-evident `agent_action_ledger` as `actor='cto'`.

**Architecture:** The LLM never touches k8s; it calls MCP tools, and `platform_mcp` is the trusted verifier-and-actor. Each `execute_*` tool checks an allow-list, re-reads live k8s to re-verify a deterministic precondition, acts via a write-scoped ServiceAccount, and posts the attempt (executed or refused) to a new bank ledger endpoint that pins `actor='cto'`. Three defense-in-depth guardrails: RBAC `resourceNames` scoping, an MCP-side allow-list, and the live self-verify.

**Tech Stack:** Python 3.12 (platform_mcp/cto over `csuite`), the official `kubernetes` client, `httpx`, FastMCP; Rust/axum + sqlx (the bank endpoint); kind, Docker.

## Global Constraints

- **Model / agent unchanged** except the CTO prompt: kimi-k2.6, thin over `csuite`.
- **No arithmetic in the model**; levers are deterministic server-side.
- **Fully autonomous** — no human confirm (COO parity). The guardrail is the server-side self-verify + allow-list + RBAC, never a prompt instruction.
- **Levers are recovery-only**: `execute_rollout_restart`, `execute_rollback`. No scale (no load signal → no deterministic precondition). No stateful/system/own-stack targets.
- **Allow-list** (`(cluster_label, deployment)`): nano-bank → `bank-api, coo, cfo, operations-mcp, finance-mcp`; modern-core → `modern-core`. Everything else is denied.
- **Audit ordering is verify → act → audit**, and the audit is **loud**: an audit failure after a successful act is surfaced as an error, never swallowed.
- **Actor is pinned server-side** to `'cto'` by the bank endpoint; the caller cannot choose it.
- **Ports** unchanged: platform MCP `8094`, CTO agent `8095`, bank `8081`.
- **Package name** is `platform_mcp` (NOT `platform` — stdlib shadow). Imports `from platform_mcp.…`.
- **Snap env (host shell)**: before any `kubectl`/`docker`/`kind`, `export XDG_RUNTIME_DIR=/run/user/1000 XDG_DATA_HOME=/home/bmartins/.local/share`.
- **Run Python tests** from the worktree root with the project `.venv`: `.venv/bin/python -m pytest …`. (A `.venv` already exists in the `agent-cto` worktree; if this worktree lacks one, create it as in Phase A: a Python-3.12 venv with `coo/requirements.txt` + `kubernetes`.)
- **Run Rust** from `api/`: `cargo build` / `cargo test`. `cargo fmt` needs `--edition 2021` if invoked bare. Integration tests use the graceful-skip harness (probe `GET /health`, return early if the stack is down), so `cargo test` passes with nothing running and truly asserts against a live stack.
- **Branch:** `agent-cto-levers`, stacked on `agent-cto`. Base confirmed at execution start.

---

### Task 1: Bank — the CTO audit endpoint

A service-authed endpoint that appends one `actor='cto'` row to the ledger. Actor is pinned in code.

**Files:**
- Create: `api/src/handlers/agent_ledger.rs`
- Modify: `api/src/handlers/mod.rs` (add `pub mod agent_ledger;`)
- Modify: `api/src/main.rs` (nest the route ~line 209, beside `ops-levers`)
- Test: `api/tests/agent_ledger.rs`

**Interfaces:**
- Produces: `POST /api/v1/agent-ledger/actions` — body `{action: string, params: object, effect: object}`, service-auth required → `200 {seq, entry_hash}`. Calls `append_agent_action('cto', action, params::jsonb, effect::jsonb)`.

- [ ] **Step 1: Write the failing integration test**

```rust
// api/tests/agent_ledger.rs
//! Integration test for the CTO audit endpoint. Graceful-skip harness (mirrors
//! tests/back_office_ops.rs): probes GET /health and returns early (passing)
//! when the API is unreachable, so `cargo test` passes with nothing running.
//! Live: cd api && cargo test --test agent_ledger -- --nocapture
use serde_json::{json, Value};

const SERVICE_SECRET: &str = "nano-bank-visa-network-secret-change-me";

fn base_url() -> String {
    std::env::var("NANO_BANK_TEST_URL").unwrap_or_else(|_| "http://localhost:8081".to_string())
}
fn client() -> reqwest::Client { reqwest::Client::new() }
async fn stack_up(c: &reqwest::Client) -> bool {
    c.get(format!("{}/health", base_url())).send().await.is_ok()
}
async fn service_token(c: &reqwest::Client) -> String {
    let r = c.post(format!("{}/api/v1/auth/service-token", base_url()))
        .json(&json!({ "client_secret": SERVICE_SECRET })).send().await.unwrap();
    r.json::<Value>().await.unwrap()["access_token"].as_str().unwrap().to_string()
}

#[tokio::test]
async fn records_a_cto_action_and_returns_seq_and_hash() {
    let c = client();
    if !stack_up(&c).await { eprintln!("skip: stack down"); return; }
    let token = service_token(&c).await;
    let r = c.post(format!("{}/api/v1/agent-ledger/actions", base_url()))
        .bearer_auth(&token)
        .json(&json!({
            "action": "rollout_restart",
            "params": {"cluster": "nano-bank", "deployment": "coo"},
            "effect": {"outcome": "executed", "effect": {"restarted_at": "t"}}
        }))
        .send().await.unwrap();
    assert!(r.status().is_success(), "status {}", r.status());
    let body: Value = r.json().await.unwrap();
    assert!(body["seq"].as_i64().is_some(), "seq missing: {body}");
    assert!(body["entry_hash"].as_str().is_some(), "entry_hash missing: {body}");
}

#[tokio::test]
async fn rejects_a_request_without_a_service_token() {
    let c = client();
    if !stack_up(&c).await { eprintln!("skip: stack down"); return; }
    let r = c.post(format!("{}/api/v1/agent-ledger/actions", base_url()))
        .json(&json!({"action": "x", "params": {}, "effect": {}}))
        .send().await.unwrap();
    assert_eq!(r.status().as_u16(), 401, "unauthenticated must be rejected");
}
```

- [ ] **Step 2: Run tests (they compile-fail: route absent)**

Run: `cd api && cargo test --test agent_ledger 2>&1 | tail -20`
Expected: compiles, tests run and **skip** if no stack — but with a live stack lacking the route, the first test FAILS (404). (Either way the deliverable is the endpoint; the live assertion happens in Task 9.)

- [ ] **Step 3: Write the handler**

```rust
// api/src/handlers/agent_ledger.rs
//! The CTO's audit endpoint. platform_mcp acts on k8s (which the bank cannot
//! see), then posts the attempt here. The actor is PINNED to 'cto' server-side —
//! a caller cannot forge a 'coo'/'cfo' entry — and the append goes through the
//! same hash-chained, immutable `agent_action_ledger` machinery the COO uses.
use axum::{extract::State, routing::post, Json, Router};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::errors::AppError;
use crate::handlers::AppState;
use crate::middleware::auth::AuthenticatedService;

pub fn agent_ledger_routes() -> Router<AppState> {
    Router::new().route("/actions", post(record_action))
}

#[derive(Deserialize)]
struct ActionBody {
    action: String,
    #[serde(default)]
    params: Value,
    #[serde(default)]
    effect: Value,
}

async fn record_action(
    _svc: AuthenticatedService,
    State(state): State<AppState>,
    Json(body): Json<ActionBody>,
) -> Result<Json<Value>, AppError> {
    // Actor is pinned to 'cto' here — never taken from the request.
    let params = if body.params.is_null() { json!({}) } else { body.params };
    let effect = if body.effect.is_null() { json!({}) } else { body.effect };
    let (seq, entry_hash): (i64, String) = sqlx::query_as(
        "SELECT seq, entry_hash FROM append_agent_action('cto', $1, $2::jsonb, $3::jsonb)",
    )
    .bind(&body.action)
    .bind(params.to_string())
    .bind(effect.to_string())
    .fetch_one(&state.pool)
    .await?;
    Ok(Json(json!({ "seq": seq, "entry_hash": entry_hash })))
}
```

- [ ] **Step 4: Register the module and route**

In `api/src/handlers/mod.rs`, add beside the other `pub mod` lines:
```rust
pub mod agent_ledger;
```
In `api/src/main.rs`, after the `ops-levers` nest (~line 209):
```rust
        .nest(
            "/api/v1/agent-ledger",
            handlers::agent_ledger::agent_ledger_routes(),
        )
```

- [ ] **Step 5: Build + test compile**

Run: `cd api && cargo build 2>&1 | tail -5 && cargo test --test agent_ledger 2>&1 | tail -10`
Expected: builds; tests pass (skip if no stack).

- [ ] **Step 6: Format + commit**

```bash
cd api && rustfmt --edition 2021 src/handlers/agent_ledger.rs
cd .. && git add api/src/handlers/agent_ledger.rs api/src/handlers/mod.rs api/src/main.rs api/tests/agent_ledger.rs
git commit -m "feat(bank): CTO audit endpoint (actor-pinned append_agent_action)"
```

---

### Task 2: platform_mcp config — allow-list, actor contexts, ledger creds

**Files:**
- Modify: `platform_mcp/config.py`
- Test: `platform_mcp/tests/test_config.py` (add cases)

**Interfaces:**
- Consumes: existing `Settings` (Phase A: `mcp_port, kubeconfig_path, contexts, health_targets, timeout`).
- Produces: `Settings` gains `actor_contexts: list[tuple[str,str]]` (actor_context, cluster_label), `allow_list: list[tuple[str,str]]` (cluster_label, deployment), `bank_api: str`, `service_client_secret: str`, `restart_threshold: int`. New env: `PLATFORM_ACTOR_CONTEXTS`, `ALLOW_LIST` (comma of `cluster/deployment`), `NANO_BANK_API`, `SERVICE_CLIENT_SECRET`, `RESTART_THRESHOLD`.

- [ ] **Step 1: Write the failing test (append to test_config.py)**

```python
def test_lever_settings_defaults():
    s = Settings.from_env({})
    assert ("kind-nano-bank-actor", "nano-bank") in s.actor_contexts
    assert ("kind-modern-core-actor", "modern-core") in s.actor_contexts
    assert ("nano-bank", "bank-api") in s.allow_list
    assert ("nano-bank", "coo") in s.allow_list
    assert ("modern-core", "modern-core") in s.allow_list
    # never stateful / own-stack in the default allow-list
    denied = {"postgres", "modern-core-db", "agent-qdrant", "platform-mcp", "cto"}
    assert not (denied & {d for _, d in s.allow_list})
    assert s.bank_api == "http://bank-api:8081"
    assert s.restart_threshold == 5


def test_lever_settings_override():
    s = Settings.from_env({
        "ALLOW_LIST": "nano-bank/coo,modern-core/modern-core",
        "PLATFORM_ACTOR_CONTEXTS": "ctxA-actor=a",
        "NANO_BANK_API": "http://x:1",
        "RESTART_THRESHOLD": "9",
    })
    assert s.allow_list == [("nano-bank", "coo"), ("modern-core", "modern-core")]
    assert s.actor_contexts == [("ctxA-actor", "a")]
    assert s.bank_api == "http://x:1"
    assert s.restart_threshold == 9
```

- [ ] **Step 2: Run — expect fail**

Run: `.venv/bin/python -m pytest platform_mcp/tests/test_config.py -q`
Expected: FAIL (`AttributeError: 'Settings' object has no attribute 'actor_contexts'`).

- [ ] **Step 3: Extend `Settings`**

Add the defaults and fields to `platform_mcp/config.py`:
```python
_DEFAULT_ACTOR_CONTEXTS = [("kind-nano-bank-actor", "nano-bank"),
                           ("kind-modern-core-actor", "modern-core")]
_DEFAULT_ALLOW_LIST = [
    ("nano-bank", "bank-api"), ("nano-bank", "coo"), ("nano-bank", "cfo"),
    ("nano-bank", "operations-mcp"), ("nano-bank", "finance-mcp"),
    ("modern-core", "modern-core"),
]
```
Add to the `Settings` dataclass fields: `actor_contexts`, `allow_list`, `bank_api: str`, `service_client_secret: str`, `restart_threshold: int`. In `from_env`, parse (reuse the existing `_pairs` helper; allow-list splits on `/`):
```python
        ac_raw = e.get("PLATFORM_ACTOR_CONTEXTS")
        al_raw = e.get("ALLOW_LIST")
        ...
            actor_contexts=_pairs(ac_raw, "=") if ac_raw else list(_DEFAULT_ACTOR_CONTEXTS),
            allow_list=_pairs(al_raw, "/") if al_raw else list(_DEFAULT_ALLOW_LIST),
            bank_api=e.get("NANO_BANK_API", "http://bank-api:8081"),
            service_client_secret=e.get("SERVICE_CLIENT_SECRET", ""),
            restart_threshold=int(e.get("RESTART_THRESHOLD", "5")),
```
(`_pairs("a/b,c/d", "/")` yields `[("a","b"),("c","d")]` — it partitions on the first `/`.)

- [ ] **Step 4: Run — expect pass**

Run: `.venv/bin/python -m pytest platform_mcp/tests/test_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add platform_mcp/config.py platform_mcp/tests/test_config.py
git commit -m "feat(platform_mcp): lever config — allow-list, actor contexts, ledger creds"
```

---

### Task 3: platform_mcp/levers.py — preconditions + allow-list (pure)

**Files:**
- Create: `platform_mcp/levers.py`
- Test: `platform_mcp/tests/test_levers.py`

**Interfaces:**
- Consumes: deployment/pod/replicaset dicts (the shapes `k8s_client` produces).
- Produces:
  - `is_allowed(allow_list: list[tuple[str,str]], cluster: str, name: str) -> bool`
  - `restart_warranted(deployment: dict, pods: list[dict], threshold: int = 5) -> bool`
  - `rollback_warranted(deployment: dict, replicasets: list[dict]) -> tuple[bool, int | None]` (the bool and, if true, the target revision to roll back to).

- [ ] **Step 1: Write the failing test**

```python
# platform_mcp/tests/test_levers.py
from platform_mcp import levers


def _dep(name, desired, ready, conditions=(), cluster="nano-bank", ns="nano-bank"):
    return {"cluster": cluster, "namespace": ns, "name": name, "desired": desired,
            "ready": ready, "available": ready, "updated": desired, "unavailable": 0,
            "images": ["x:1"], "conditions": [dict(c) for c in conditions]}


def _pod(name, restart_count=0, waiting=None, cluster="nano-bank"):
    return {"cluster": cluster, "namespace": "nano-bank", "name": name,
            "phase": "Running", "containers": [
                {"name": "c", "ready": waiting is None, "restart_count": restart_count,
                 "waiting_reason": waiting}]}


def _rs(name, owner, revision, cluster="nano-bank"):
    return {"cluster": cluster, "namespace": "nano-bank", "name": name,
            "owner_deployment": owner, "revision": revision, "desired": 1, "ready": 1}


def test_is_allowed():
    al = [("nano-bank", "coo"), ("modern-core", "modern-core")]
    assert levers.is_allowed(al, "nano-bank", "coo") is True
    assert levers.is_allowed(al, "nano-bank", "postgres") is False
    assert levers.is_allowed(al, "modern-core", "coo") is False


def test_restart_warranted_on_crashloop():
    dep = _dep("coo", 1, 1)
    pods = [_pod("coo-1", restart_count=9, waiting="CrashLoopBackOff")]
    assert levers.restart_warranted(dep, pods) is True


def test_restart_warranted_on_unready():
    assert levers.restart_warranted(_dep("coo", 2, 1), []) is True


def test_restart_not_warranted_when_healthy():
    assert levers.restart_warranted(_dep("coo", 1, 1), [_pod("coo-1")]) is False


def test_rollback_warranted_when_stalled_with_prior_revision():
    dep = _dep("cfo", 2, 1, conditions=[
        {"type": "Progressing", "status": "False", "reason": "ProgressDeadlineExceeded"}])
    rss = [_rs("cfo-a", "cfo", 4), _rs("cfo-b", "cfo", 5), _rs("other-x", "bank-api", 2)]
    ok, target = levers.rollback_warranted(dep, rss)
    assert ok is True and target == 4          # second-highest revision for cfo


def test_rollback_not_warranted_without_a_prior_revision():
    dep = _dep("cfo", 2, 1, conditions=[
        {"type": "Progressing", "status": "False", "reason": "ProgressDeadlineExceeded"}])
    ok, target = levers.rollback_warranted(dep, [_rs("cfo-b", "cfo", 5)])
    assert ok is False and target is None


def test_rollback_not_warranted_when_progressing_normally():
    dep = _dep("cfo", 1, 1, conditions=[
        {"type": "Progressing", "status": "True", "reason": "NewReplicaSetAvailable"}])
    ok, target = levers.rollback_warranted(dep, [_rs("a", "cfo", 4), _rs("b", "cfo", 5)])
    assert ok is False and target is None
```

- [ ] **Step 2: Run — expect fail**

Run: `.venv/bin/python -m pytest platform_mcp/tests/test_levers.py -q`
Expected: FAIL (`ModuleNotFoundError: platform_mcp.levers`).

- [ ] **Step 3: Implement**

```python
# platform_mcp/levers.py
"""Deterministic precondition + allow-list logic for the CTO's infra levers.
Pure: dict-in/bool-out, no IO. `platform_mcp` re-runs these against LIVE reads at
execute time, so the agent cannot argue past a false precondition."""
from __future__ import annotations


def is_allowed(allow_list, cluster: str, name: str) -> bool:
    return (cluster, name) in set(allow_list)


def restart_warranted(deployment: dict, pods: list[dict], threshold: int = 5) -> bool:
    """A restart is a valid recovery iff the deployment is crashlooping OR not
    fully ready right now."""
    name = deployment.get("name")
    cluster = deployment.get("cluster")
    for p in pods:
        if p.get("name", "").startswith(f"{name}-") and p.get("cluster") == cluster:
            for c in p.get("containers", []):
                if c.get("waiting_reason") == "CrashLoopBackOff":
                    return True
                if int(c.get("restart_count", 0)) > threshold:
                    return True
    return int(deployment.get("ready", 0)) < int(deployment.get("desired", 0))


def _is_stalled(deployment: dict) -> bool:
    for c in deployment.get("conditions", []):
        if c.get("type") == "Progressing" and c.get("reason") == "ProgressDeadlineExceeded":
            return True
    return False


def rollback_warranted(deployment: dict, replicasets: list[dict]) -> tuple[bool, int | None]:
    """Roll back iff the rollout is stalled AND a prior ReplicaSet revision exists.
    Target = the second-highest revision owned by this deployment (the previous
    good one). Returns (True, target_revision) or (False, None)."""
    if not _is_stalled(deployment):
        return False, None
    name = deployment.get("name")
    cluster = deployment.get("cluster")
    revs = sorted(
        {int(rs["revision"]) for rs in replicasets
         if rs.get("owner_deployment") == name and rs.get("cluster") == cluster
         and rs.get("revision") is not None},
        reverse=True,
    )
    if len(revs) < 2:
        return False, None
    return True, revs[1]
```

- [ ] **Step 4: Run — expect pass**

Run: `.venv/bin/python -m pytest platform_mcp/tests/test_levers.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add platform_mcp/levers.py platform_mcp/tests/test_levers.py
git commit -m "feat(platform_mcp): deterministic lever preconditions + allow-list (pure)"
```

---

### Task 4: platform_mcp/k8s_writer.py — the two mutating actions

**Files:**
- Create: `platform_mcp/k8s_writer.py`
- Test: `platform_mcp/tests/test_k8s_writer.py`

**Interfaces:**
- Consumes: `Settings.actor_contexts`, `Settings.kubeconfig_path`.
- Produces: `K8sWriter(settings, loader=None)` where `loader(kubeconfig_path, actor_context) -> AppsV1Api`. Methods:
  - `rollout_restart(cluster: str, name: str) -> dict` → patches `spec.template.metadata.annotations["kubectl.kubernetes.io/restartedAt"]`; returns `{"restarted_at": <rfc3339>}`.
  - `rollback(cluster: str, name: str, target_revision: int) -> dict` → finds the owning ReplicaSet at `target_revision`, patches the Deployment's `spec.template` to that RS's template; returns `{"rolled_back_to": target_revision}`.

- [ ] **Step 1: Write the failing test**

```python
# platform_mcp/tests/test_k8s_writer.py
from types import SimpleNamespace as NS
from platform_mcp.config import Settings
from platform_mcp.k8s_writer import K8sWriter


def _settings():
    return Settings.from_env({"PLATFORM_ACTOR_CONTEXTS": "kind-nano-bank-actor=nano-bank"})


class _FakeApps:
    def __init__(self):
        self.patched = []   # (name, body)

    def patch_namespaced_deployment(self, name, namespace, body):
        self.patched.append((name, body))
        return NS(metadata=NS(name=name))

    def list_namespaced_replica_set(self, namespace):
        # cfo has revisions 4 and 5; the rollback target is 4.
        return NS(items=[
            NS(metadata=NS(name="cfo-old", namespace="nano-bank",
                           owner_references=[NS(kind="Deployment", name="cfo")],
                           annotations={"deployment.kubernetes.io/revision": "4"}),
               spec=NS(template=NS(metadata=NS(labels={"pod": "old"}), spec=NS(containers=[])))),
            NS(metadata=NS(name="cfo-new", namespace="nano-bank",
                           owner_references=[NS(kind="Deployment", name="cfo")],
                           annotations={"deployment.kubernetes.io/revision": "5"}),
               spec=NS(template=NS(metadata=NS(labels={"pod": "new"}), spec=NS(containers=[])))),
        ])


def _loader_factory(fake):
    def _loader(path, context):
        assert context == "kind-nano-bank-actor"
        return fake
    return _loader


def test_rollout_restart_patches_restarted_at():
    fake = _FakeApps()
    w = K8sWriter(_settings(), loader=_loader_factory(fake))
    out = w.rollout_restart("nano-bank", "coo")
    assert "restarted_at" in out
    name, body = fake.patched[0]
    assert name == "coo"
    ann = body["spec"]["template"]["metadata"]["annotations"]
    assert ann["kubectl.kubernetes.io/restartedAt"] == out["restarted_at"]


def test_rollback_patches_prior_template():
    fake = _FakeApps()
    w = K8sWriter(_settings(), loader=_loader_factory(fake))
    out = w.rollback("nano-bank", "cfo", 4)
    assert out["rolled_back_to"] == 4
    name, body = fake.patched[0]
    assert name == "cfo"
    # the deployment's template is replaced by revision 4's ("old")
    assert body["spec"]["template"]["metadata"]["labels"] == {"pod": "old"}
```

- [ ] **Step 2: Run — expect fail**

Run: `.venv/bin/python -m pytest platform_mcp/tests/test_k8s_writer.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

```python
# platform_mcp/k8s_writer.py
"""The only MUTATING capability in platform_mcp — two narrow patches over the
write-scoped `platform-actor` context. rollout_restart bumps the restartedAt
annotation (what `kubectl rollout restart` does); rollback replaces the
deployment's pod template with a prior ReplicaSet's (what `kubectl rollout undo`
does). `loader` is injectable so tests run with no live cluster."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Callable, Optional

from .config import Settings


def _default_loader(kubeconfig_path: str, context: str):
    from kubernetes import client, config
    config.load_kube_config(config_file=kubeconfig_path, context=context)
    return client.AppsV1Api()


def _namespace_for(cluster: str) -> str:
    # Each cluster's app deployments live in the namespace of the same name.
    return "modern-core" if cluster == "modern-core" else "nano-bank"


class K8sWriter:
    def __init__(self, settings: Settings, loader: Optional[Callable] = None):
        self._s = settings
        self._loader = loader or _default_loader
        self._actor_ctx = {label: ctx for ctx, label in settings.actor_contexts}

    def _apps(self, cluster: str):
        ctx = self._actor_ctx[cluster]
        return self._loader(self._s.kubeconfig_path, ctx)

    def rollout_restart(self, cluster: str, name: str) -> dict:
        ts = datetime.now(timezone.utc).isoformat()
        body = {"spec": {"template": {"metadata": {"annotations": {
            "kubectl.kubernetes.io/restartedAt": ts}}}}}
        self._apps(cluster).patch_namespaced_deployment(
            name=name, namespace=_namespace_for(cluster), body=body)
        return {"restarted_at": ts}

    def rollback(self, cluster: str, name: str, target_revision: int) -> dict:
        apps = self._apps(cluster)
        ns = _namespace_for(cluster)
        target = None
        for rs in apps.list_namespaced_replica_set(namespace=ns).items:
            ann = getattr(rs.metadata, "annotations", None) or {}
            owners = getattr(rs.metadata, "owner_references", None) or []
            owned = any(getattr(o, "kind", None) == "Deployment" and o.name == name
                        for o in owners)
            if owned and ann.get("deployment.kubernetes.io/revision") == str(target_revision):
                target = rs
                break
        if target is None:
            raise ValueError(f"no ReplicaSet at revision {target_revision} for {name}")
        # Serialize the RS pod template back onto the deployment (rollout undo).
        from kubernetes.client import ApiClient
        template = ApiClient().sanitize_for_serialization(target.spec.template)
        body = {"spec": {"template": template}}
        apps.patch_namespaced_deployment(name=name, namespace=ns, body=body)
        return {"rolled_back_to": target_revision}
```

> Note: the test's fake `spec.template` is a `SimpleNamespace`; `ApiClient().sanitize_for_serialization` turns real k8s objects into dicts but passes plain objects/dicts through. If the test's namespace object does not serialize to the expected dict, adjust the fake to return a plain dict template (`spec=NS(template={"metadata": {"labels": {"pod": "old"}}, "spec": {"containers": []}})`) — the production path receives real k8s model objects, which sanitize correctly. Keep the assertion on `labels == {"pod": "old"}`.

- [ ] **Step 4: Run — expect pass**

Run: `.venv/bin/python -m pytest platform_mcp/tests/test_k8s_writer.py -q`
Expected: PASS (2 tests). If `sanitize_for_serialization` mangles the `SimpleNamespace`, switch the fake's `template` to a plain dict per the note and re-run.

- [ ] **Step 5: Commit**

```bash
git add platform_mcp/k8s_writer.py platform_mcp/tests/test_k8s_writer.py
git commit -m "feat(platform_mcp): k8s_writer — rollout restart + rollback (injectable)"
```

---

### Task 5: platform_mcp/audit.py — post the attempt to the bank ledger (loud)

**Files:**
- Create: `platform_mcp/audit.py`
- Test: `platform_mcp/tests/test_audit.py`

**Interfaces:**
- Consumes: `Settings.bank_api`, `Settings.service_client_secret`, `Settings.timeout`.
- Produces: `LedgerAudit(settings, transport=None)`; `post_action(action: str, params: dict, effect: dict) -> dict` — mints/caches a bank service token, POSTs to `/api/v1/agent-ledger/actions`, returns `{seq, entry_hash}`; **raises** on any failure (never silent).

- [ ] **Step 1: Write the failing test**

```python
# platform_mcp/tests/test_audit.py
import httpx
import pytest
from platform_mcp.config import Settings
from platform_mcp.audit import LedgerAudit


def _settings():
    return Settings.from_env({"NANO_BANK_API": "http://bank",
                              "SERVICE_CLIENT_SECRET": "sekret"})


def _handler(record):
    def h(request):
        if request.url.path == "/api/v1/auth/service-token":
            record.append(("token", request.url.path))
            return httpx.Response(200, json={"access_token": "jwt", "expires_in": 900})
        if request.url.path == "/api/v1/agent-ledger/actions":
            record.append(("action", request.read().decode()))
            return httpx.Response(200, json={"seq": 7, "entry_hash": "abc"})
        return httpx.Response(404)
    return h


def test_post_action_mints_token_then_records():
    rec = []
    a = LedgerAudit(_settings(), transport=httpx.MockTransport(_handler(rec)))
    out = a.post_action("rollout_restart", {"deployment": "coo"}, {"outcome": "executed"})
    assert out == {"seq": 7, "entry_hash": "abc"}
    kinds = [k for k, _ in rec]
    assert kinds == ["token", "action"]


def test_post_action_raises_on_failure():
    def h(request):
        if request.url.path.endswith("service-token"):
            return httpx.Response(200, json={"access_token": "jwt", "expires_in": 900})
        return httpx.Response(500, json={"error": "boom"})
    a = LedgerAudit(_settings(), transport=httpx.MockTransport(h))
    with pytest.raises(Exception):
        a.post_action("x", {}, {})
```

- [ ] **Step 2: Run — expect fail**

Run: `.venv/bin/python -m pytest platform_mcp/tests/test_audit.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

```python
# platform_mcp/audit.py
"""Post a CTO action to the bank's ledger endpoint. Loud, not best-effort: an
autonomous action must never land without its audit row, so any failure raises
and the lever reports it. Mints + caches a service token like operations'
BankClient."""
from __future__ import annotations
import time
from typing import Optional

import httpx

from .config import Settings


class LedgerAudit:
    def __init__(self, settings: Settings, transport: Optional[httpx.BaseTransport] = None):
        self._s = settings
        self._http = httpx.Client(base_url=settings.bank_api, timeout=settings.timeout,
                                  transport=transport)
        self._token: Optional[str] = None
        self._exp: float = 0.0

    def _bearer(self) -> str:
        if self._token is None or time.time() >= self._exp:
            r = self._http.post("/api/v1/auth/service-token",
                                json={"client_secret": self._s.service_client_secret})
            r.raise_for_status()
            b = r.json()
            self._token = b["access_token"]
            self._exp = time.time() + float(b.get("expires_in", 900)) * 0.8
        return self._token

    def post_action(self, action: str, params: dict, effect: dict) -> dict:
        r = self._http.post(
            "/api/v1/agent-ledger/actions",
            headers={"authorization": f"Bearer {self._bearer()}"},
            json={"action": action, "params": params, "effect": effect})
        r.raise_for_status()
        return r.json()
```

- [ ] **Step 4: Run — expect pass**

Run: `.venv/bin/python -m pytest platform_mcp/tests/test_audit.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add platform_mcp/audit.py platform_mcp/tests/test_audit.py
git commit -m "feat(platform_mcp): loud ledger audit client for CTO actions"
```

---

### Task 6: platform_mcp/mcp_server.py — the two execute tools

**Files:**
- Modify: `platform_mcp/mcp_server.py`
- Test: `platform_mcp/tests/test_mcp_levers.py`

**Interfaces:**
- Consumes: `K8sClient` (live re-read), `K8sWriter`, `LedgerAudit`, `levers`, `Settings`.
- Produces: `build_mcp(k8s, health, writer=None, audit=None, settings=None)` — when `writer`, `audit`, and `settings` are all provided, registers `execute_rollout_restart(cluster, deployment)` and `execute_rollback(cluster, deployment)`. Each returns `{"outcome": "executed"|"refused", ...}` and audits every attempt. Backward compatible: called with only `k8s, health` (Phase A), the execute tools are simply not registered.

- [ ] **Step 1: Write the failing test**

```python
# platform_mcp/tests/test_mcp_levers.py
import anyio
import pytest
from platform_mcp.config import Settings
from platform_mcp.mcp_server import build_mcp


def _settings():
    return Settings.from_env({"ALLOW_LIST": "nano-bank/coo"})


class _K8s:
    def __init__(self, deployments, pods=None, replicasets=None):
        self._d, self._p, self._r = deployments, pods or [], replicasets or []
    def deployments(self): return self._d
    def pods(self): return self._p
    def replicasets(self): return self._r


class _Writer:
    def __init__(self): self.calls = []
    def rollout_restart(self, cluster, name):
        self.calls.append(("restart", cluster, name)); return {"restarted_at": "t"}
    def rollback(self, cluster, name, target):
        self.calls.append(("rollback", cluster, name, target)); return {"rolled_back_to": target}


class _Audit:
    def __init__(self, fail=False): self.posts = []; self._fail = fail
    def post_action(self, action, params, effect):
        self.posts.append((action, params, effect))
        if self._fail: raise RuntimeError("ledger down")
        return {"seq": 1, "entry_hash": "h"}


def _call(mcp, tool, args):
    return anyio.run(lambda: mcp.call_tool(tool, args))


def _crashloop_dep():
    return {"cluster": "nano-bank", "namespace": "nano-bank", "name": "coo",
            "desired": 1, "ready": 0, "available": 0, "updated": 1, "unavailable": 1,
            "images": ["x:1"], "conditions": []}


def _crashloop_pod():
    return {"cluster": "nano-bank", "namespace": "nano-bank", "name": "coo-1",
            "phase": "Running", "containers": [
                {"name": "c", "ready": False, "restart_count": 9,
                 "waiting_reason": "CrashLoopBackOff"}]}


def test_restart_executes_when_warranted_and_audits():
    k8s = _K8s([_crashloop_dep()], pods=[_crashloop_pod()])
    w, a = _Writer(), _Audit()
    mcp = build_mcp(k8s, None, writer=w, audit=a, settings=_settings())
    names = {t.name for t in anyio.run(mcp.list_tools)}
    assert {"execute_rollout_restart", "execute_rollback"} <= names
    w.calls.clear()
    # exercise the underlying logic directly via the registered tool
    # (FastMCP tool bodies close over k8s/writer/audit)
    from platform_mcp import mcp_server
    out = mcp_server._do_restart(k8s, w, a, _settings(), "nano-bank", "coo")
    assert out["outcome"] == "executed"
    assert ("restart", "nano-bank", "coo") in w.calls
    assert a.posts and a.posts[0][0] == "rollout_restart"


def test_restart_refuses_when_not_allowed_and_still_audits():
    from platform_mcp import mcp_server
    k8s = _K8s([_crashloop_dep()], pods=[_crashloop_pod()])
    w, a = _Writer(), _Audit()
    out = mcp_server._do_restart(k8s, w, a, _settings(), "nano-bank", "postgres")
    assert out["outcome"] == "refused"
    assert w.calls == []                       # never acted
    assert a.posts and a.posts[0][2]["outcome"] == "refused"


def test_restart_refuses_when_precondition_false():
    from platform_mcp import mcp_server
    healthy = dict(_crashloop_dep(), ready=1, available=1, unavailable=0)
    k8s = _K8s([healthy], pods=[])
    w, a = _Writer(), _Audit()
    out = mcp_server._do_restart(k8s, w, a, _settings(), "nano-bank", "coo")
    assert out["outcome"] == "refused"
    assert w.calls == []


def test_audit_failure_after_acting_raises_loud():
    from platform_mcp import mcp_server
    k8s = _K8s([_crashloop_dep()], pods=[_crashloop_pod()])
    w, a = _Writer(), _Audit(fail=True)
    with pytest.raises(RuntimeError):
        mcp_server._do_restart(k8s, w, a, _settings(), "nano-bank", "coo")
    assert w.calls, "acted before the audit failed"
```

- [ ] **Step 2: Run — expect fail**

Run: `.venv/bin/python -m pytest platform_mcp/tests/test_mcp_levers.py -q`
Expected: FAIL (`_do_restart` / execute tools absent).

- [ ] **Step 3: Implement — add the helpers + tools to `mcp_server.py`**

Add module-level helpers (testable without FastMCP) and extend `build_mcp`:
```python
from . import levers


def _find(deployments, cluster, name):
    for d in deployments:
        if d.get("cluster") == cluster and d.get("name") == name:
            return d
    return None


def _refused(reason):
    return {"outcome": "refused", "reason": reason}


def _executed(effect):
    return {"outcome": "executed", "effect": effect}


def _do_restart(k8s, writer, audit, settings, cluster, deployment):
    params = {"cluster": cluster, "deployment": deployment}
    if not levers.is_allowed(settings.allow_list, cluster, deployment):
        outcome = _refused(f"{cluster}/{deployment} is not in the CTO action allow-list")
        audit.post_action("rollout_restart", params, outcome)
        return outcome
    dep = _find(k8s.deployments(), cluster, deployment)   # LIVE re-read
    if dep is None:
        outcome = _refused(f"{cluster}/{deployment} not found")
        audit.post_action("rollout_restart", params, outcome)
        return outcome
    if not levers.restart_warranted(dep, k8s.pods(), settings.restart_threshold):
        outcome = _refused(f"{deployment} is not crashlooping or unready; restart unwarranted")
        audit.post_action("rollout_restart", params, outcome)
        return outcome
    effect = writer.rollout_restart(cluster, deployment)   # act
    outcome = _executed(effect)
    audit.post_action("rollout_restart", params, outcome)  # loud audit
    return outcome


def _do_rollback(k8s, writer, audit, settings, cluster, deployment):
    params = {"cluster": cluster, "deployment": deployment}
    if not levers.is_allowed(settings.allow_list, cluster, deployment):
        outcome = _refused(f"{cluster}/{deployment} is not in the CTO action allow-list")
        audit.post_action("rollback", params, outcome)
        return outcome
    dep = _find(k8s.deployments(), cluster, deployment)
    if dep is None:
        outcome = _refused(f"{cluster}/{deployment} not found")
        audit.post_action("rollback", params, outcome)
        return outcome
    ok, target = levers.rollback_warranted(dep, k8s.replicasets())
    if not ok:
        outcome = _refused(f"{deployment} rollout is not stalled with a prior revision")
        audit.post_action("rollback", params, outcome)
        return outcome
    effect = writer.rollback(cluster, deployment, target)
    outcome = _executed(effect)
    audit.post_action("rollback", params, outcome)
    return outcome
```
Then in `build_mcp(k8s, health, writer=None, audit=None, settings=None)`, after the read tools, register the levers only when the acting deps are present:
```python
    if writer is not None and audit is not None and settings is not None:
        @mcp.tool()
        def execute_rollout_restart(cluster: str, deployment: str) -> dict:
            """Restart a stateless app deployment's pods (rolling). REFUSED unless
            it is actually crashlooping/unready right now and on the CTO action
            allow-list. Autonomous + audited; report the outcome verbatim."""
            return _stringify(_do_restart(k8s, writer, audit, settings, cluster, deployment))

        @mcp.tool()
        def execute_rollback(cluster: str, deployment: str) -> dict:
            """Roll a stateless app deployment back to its prior revision. REFUSED
            unless its rollout is actually stalled with a prior revision and it is
            on the allow-list. Autonomous + audited; report the outcome verbatim."""
            return _stringify(_do_rollback(k8s, writer, audit, settings, cluster, deployment))
```
Finally, wire real deps in `main()`:
```python
def main():
    settings = Settings.from_env()
    k8s = K8sClient(settings)
    health = HealthClient(settings)
    from .k8s_writer import K8sWriter
    from .audit import LedgerAudit
    writer = K8sWriter(settings)
    audit = LedgerAudit(settings)
    mcp = build_mcp(k8s, health, writer=writer, audit=audit, settings=settings)
    import uvicorn
    uvicorn.run(mcp.streamable_http_app(), host="0.0.0.0", port=settings.mcp_port)
```

- [ ] **Step 4: Run — expect pass (+ Phase A tests still green)**

Run: `.venv/bin/python -m pytest platform_mcp -q`
Expected: PASS (new lever tests + all Phase A tests; `test_mcp_server.py`'s Phase-A `build_mcp(k8s, health)` call still registers exactly the 7 read tools).

- [ ] **Step 5: Commit**

```bash
git add platform_mcp/mcp_server.py platform_mcp/tests/test_mcp_levers.py
git commit -m "feat(platform_mcp): execute_rollout_restart + execute_rollback tools (verify->act->audit)"
```

---

### Task 7: RBAC (write-scoped actor SA) + kubeconfig + manifest env

Provision the `platform-actor` ServiceAccount whose write is scoped by `resourceNames`, add its context to the mounted kubeconfig, and give the platform-mcp pod the bank creds.

**Files:**
- Create: `platform_mcp/k8s/rbac-actor-nano-bank.yaml`, `platform_mcp/k8s/rbac-actor-modern-core.yaml`
- Modify: `platform_mcp/k8s/make-kubeconfig.sh`
- Modify: `platform_mcp/k8s/platform-mcp.yaml`

No unit test — verified live in Task 9.

- [ ] **Step 1: Write `rbac-actor-nano-bank.yaml`**

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: platform-actor
  namespace: kube-system
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: platform-actor
  namespace: nano-bank
rules:
# Reading replicasets (for rollback target templates) is low-risk, unrestricted.
- apiGroups: ["apps"]
  resources: ["replicasets"]
  verbs: ["get", "list"]
# The dangerous verb — patch — is restricted by name to the allow-listed apps.
- apiGroups: ["apps"]
  resources: ["deployments"]
  verbs: ["get", "patch"]
  resourceNames: ["bank-api", "coo", "cfo", "operations-mcp", "finance-mcp"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: platform-actor
  namespace: nano-bank
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: platform-actor
subjects:
- kind: ServiceAccount
  name: platform-actor
  namespace: kube-system
```

- [ ] **Step 2: Write `rbac-actor-modern-core.yaml`** (same, `namespace: modern-core`, `resourceNames: ["modern-core"]`)

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: platform-actor
  namespace: kube-system
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: platform-actor
  namespace: modern-core
rules:
- apiGroups: ["apps"]
  resources: ["replicasets"]
  verbs: ["get", "list"]
- apiGroups: ["apps"]
  resources: ["deployments"]
  verbs: ["get", "patch"]
  resourceNames: ["modern-core"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: platform-actor
  namespace: modern-core
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: platform-actor
subjects:
- kind: ServiceAccount
  name: platform-actor
  namespace: kube-system
```

- [ ] **Step 3: Extend `make-kubeconfig.sh`** to also mint the actor SA and add its context

In the `mint()` function, after minting `platform-reader`, also apply the per-cluster actor RBAC and add an actor context. Change the reader-only body to additionally do (keeping the reader logic intact):
```bash
  # --- write-scoped actor SA (Phase B) ---
  local actor_rbac="rbac-actor-${cluster_label}.yaml"
  echo "🔐 minting platform-actor in $ctx ($actor_rbac)..."
  kubectl --context "$ctx" apply -f "$actor_rbac" >/dev/null
  local atoken
  atoken=$(kubectl --context "$ctx" -n kube-system create token platform-actor --duration=8760h)
  KUBECONFIG="$OUT" kubectl config set-cluster "${ctx}-actor" --server="$server" >/dev/null
  KUBECONFIG="$OUT" kubectl config set "clusters.${ctx}-actor.certificate-authority-data" "$ca" >/dev/null
  KUBECONFIG="$OUT" kubectl config set-credentials "platform-actor@$ctx" --token="$atoken" >/dev/null
  KUBECONFIG="$OUT" kubectl config set-context "${ctx}-actor" --cluster="${ctx}-actor" --user="platform-actor@$ctx" >/dev/null
```
This requires `mint()` to receive the `cluster_label` (nano-bank / modern-core). Update the two call sites:
```bash
mint "$NB_CTX" nano-bank-control-plane nano-bank
mint "$MC_CTX" modern-core-control-plane modern-core
```
and the signature `mint() { local ctx="$1" node="$2" cluster_label="$3"; ... }`. The actor contexts are `kind-nano-bank-actor` / `kind-modern-core-actor` — matching `_DEFAULT_ACTOR_CONTEXTS` in config.

- [ ] **Step 4: Extend `platform-mcp.yaml`** — give the pod the bank creds

Add to the platform-mcp container `env:`:
```yaml
        - { name: NANO_BANK_API, value: http://bank-api:8081 }
        - name: SERVICE_CLIENT_SECRET
          valueFrom:
            secretKeyRef: { name: nano-agent-secrets, key: SERVICE_CLIENT_SECRET }
```
(The kubeconfig Secret mount is unchanged — it now also carries the `*-actor` contexts.)

- [ ] **Step 5: Lint the manifests + script**

Run:
```bash
.venv/bin/python -c "import yaml,glob; [list(yaml.safe_load_all(open(f))) for f in glob.glob('platform_mcp/k8s/rbac-actor-*.yaml')]; print('yaml ok')"
bash -n platform_mcp/k8s/make-kubeconfig.sh && echo "script ok"
```
Expected: `yaml ok` + `script ok`.

- [ ] **Step 6: Commit**

```bash
git add platform_mcp/k8s/rbac-actor-nano-bank.yaml platform_mcp/k8s/rbac-actor-modern-core.yaml platform_mcp/k8s/make-kubeconfig.sh platform_mcp/k8s/platform-mcp.yaml
git commit -m "build(platform_mcp): write-scoped platform-actor SA (resourceNames) + kubeconfig + bank creds"
```

---

### Task 8: CTO prompt — the autonomous operator paragraph

**Files:**
- Modify: `cto/agent.py` (`CTO_PROMPT`)
- Test: `cto/tests/test_agent.py` (add a lever test) + `csuite/tests/fakes.py` (a fake lever tool)

**Interfaces:**
- Consumes: `csuite.tests.fakes.fake_platform_tools` (extend with an `execute_rollout_restart` fake).
- Produces: the prompt instructs autonomous, audited lever use; a test proves the agent will call a lever when the metrics warrant it.

- [ ] **Step 1: Extend `fake_platform_tools()` in `csuite/tests/fakes.py`**

Add an execute tool to the returned list:
```python
    @tool
    def execute_rollout_restart(cluster: str, deployment: str) -> dict:
        """Canned restart lever."""
        return {"outcome": "executed", "effect": {"restarted_at": "t"}}
```
(append `execute_rollout_restart` to the `return [estate_health, service_health]` list → `return [estate_health, service_health, execute_rollout_restart]`.)

- [ ] **Step 2: Write the failing test (append to cto/tests/test_agent.py)**

```python
def test_cto_pulls_a_lever_when_warranted(monkeypatch):
    model = FakeChatModel([
        {"tool": "estate_health", "args": {}},
        {"tool": "execute_rollout_restart",
         "args": {"cluster": "nano-bank", "deployment": "coo"}},
        {"text": "Restarted coo; the bank returned executed (restarted_at t)."},
    ])
    _patch(monkeypatch, model)
    out = asyncio.run(agent_mod.ask(_settings(), "coo is crashlooping, handle it",
                                    memory=SafeMemory(None)))
    assert any(e.get("name") == "execute_rollout_restart" for e in out["trace"])
    assert "executed" in out["answer"]
```

- [ ] **Step 3: Run — expect fail**

Run: `.venv/bin/python -m pytest cto/tests/test_agent.py::test_cto_pulls_a_lever_when_warranted -q`
Expected: FAIL (the fake tool isn't in the list yet, or the trace lacks the call) — confirm it fails before the prompt/fakes change is complete.

- [ ] **Step 4: Add the operator paragraph to `CTO_PROMPT`**

Append to the prompt string in `cto/agent.py` (before the closing paren), mirroring the COO's autonomous-operator language:
```python
    " You are also an AUTONOMOUS OPERATOR of the platform and may PULL LEVERS on "
    "your own judgment, with no human confirmation. Your levers are "
    "execute_rollout_restart and execute_rollback. Before acting, look at the "
    "metrics to confirm the action is warranted (a crashlooping/unready "
    "deployment for a restart; a stalled rollout with a prior revision for a "
    "rollback); then pull the lever. Each lever is self-verifying — platform_mcp "
    "independently re-checks a deterministic precondition against live cluster "
    "state and will REFUSE an unwarranted or out-of-scope action — and every "
    "attempt, executed or refused, is written to a tamper-evident audit ledger "
    "you cannot read or alter. Do not ask permission and do not tell the user to "
    "run it themselves; take the action and report plainly what came back "
    "(executed with its effect, or refused with the reason). Act only on the "
    "platform (stateless app deployments); never touch the books or money "
    "operations — those are the CFO's and COO's."
```

- [ ] **Step 5: Run — expect pass (+ existing cto/csuite tests green)**

Run: `.venv/bin/python -m pytest cto csuite -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add cto/agent.py cto/tests/test_agent.py csuite/tests/fakes.py
git commit -m "feat(cto): autonomous-operator prompt for the infra levers"
```

---

### Task 9: Live rollout + smoke (both clusters)

No new code — prove the whole lever path works against the real clusters, per superpowers:verification-before-completion (run each command, confirm output before claiming success).

**Prereqs:** both kind clusters up; `nano-agent-secrets` + `agent-qdrant` present; the CTO stack from Phase A deployed; snap env exported. (If the `agent-cto-levers` worktree has no `.venv`, create it first as in Phase A.)

- [ ] **Step 1: Rebuild the bank + platform-mcp + cto images and reload**

```bash
export XDG_RUNTIME_DIR=/run/user/1000 XDG_DATA_HOME=/home/bmartins/.local/share
# bank-api (new endpoint) — build per the repo's bank image flow, then:
kind load docker-image <bank-image>:dev --name nano-bank
docker build -t nano-platform-mcp:dev platform_mcp
docker build -f cto/Dockerfile -t nano-cto:dev .
kind load docker-image nano-platform-mcp:dev nano-cto:dev --name nano-bank
```
Expected: images load on all nodes. (Confirm the exact bank image name/build with the operator; the bank deploy manifest is under `k8s/`.)

- [ ] **Step 2: Re-mint the kubeconfig (now with actor contexts) + roll the deployments**

```bash
./platform_mcp/k8s/make-kubeconfig.sh
kubectl --context kind-nano-bank -n nano-bank apply -f k8s/  # bank-api rollout (as the repo does)
kubectl --context kind-nano-bank -n nano-bank rollout restart deploy/platform-mcp deploy/cto
kubectl --context kind-nano-bank -n nano-bank rollout status deploy/platform-mcp --timeout=180s
kubectl --context kind-nano-bank -n nano-bank rollout status deploy/cto --timeout=240s
```
Expected: the mint prints a successful actor-SA read; rollouts succeed.

- [ ] **Step 3: Verify RBAC scoping is real (negative check)**

```bash
# the actor SA MAY patch coo, MUST NOT patch postgres
kubectl --context kind-nano-bank -n nano-bank auth can-i patch deploy/coo \
  --as=system:serviceaccount:kube-system:platform-actor
kubectl --context kind-nano-bank -n nano-bank auth can-i patch deploy/postgres \
  --as=system:serviceaccount:kube-system:platform-actor
```
Expected: `yes` for `coo`, `no` for `postgres`.

- [ ] **Step 4: Write + run the live lever smoke `platform_mcp/verify-cto-levers.sh`**

Create the script:
```bash
#!/bin/bash
# Live: with the CTO stack up, induce a crashloop on an allow-listed app, have
# the CTO restart it, and confirm a fresh actor='cto' ledger entry with the chain
# intact. Requires kubectl + a CTO API port-forward on :8095.
set -euo pipefail
CTX=kind-nano-bank; NS=nano-bank; DEPLOY="${DEPLOY:-cfo}"
CTO="${CTO_API_URL:-http://localhost:8095}"
echo "== break $DEPLOY (bad command → crashloop) =="
ORIG=$(kubectl --context $CTX -n $NS get deploy/$DEPLOY -o jsonpath='{.spec.template.spec.containers[0].command}')
kubectl --context $CTX -n $NS patch deploy/$DEPLOY --type=json \
  -p='[{"op":"add","path":"/spec/template/spec/containers/0/command","value":["/bin/false"]}]'
kubectl --context $CTX -n $NS rollout status deploy/$DEPLOY --timeout=40s || true
echo "== ask the CTO to handle it =="
curl -fsS -XPOST "$CTO/ask" -H 'content-type: application/json' \
  -d "{\"message\":\"$DEPLOY looks crashlooping — investigate and, if warranted, restart it.\"}" \
  | python -c 'import sys,json; d=json.load(sys.stdin); print(d["answer"]); \
import re; assert "execute_rollout_restart" in str(d["trace"]), "CTO did not pull the lever"'
echo "== ledger shows the actor=cto entry, chain intact =="
kubectl --context $CTX -n $NS exec deploy/postgres -- psql -U nanobank_user -d nano_bank_db -tAc \
  "SELECT actor, action, effect->>'outcome' FROM agent_action_ledger WHERE actor='cto' ORDER BY seq DESC LIMIT 3;"
kubectl --context $CTX -n $NS exec deploy/postgres -- psql -U nanobank_user -d nano_bank_db -tAc \
  "SELECT verify_agent_ledger();"   # 0 = intact
echo "== restore $DEPLOY =="
kubectl --context $CTX -n $NS patch deploy/$DEPLOY --type=json \
  -p='[{"op":"remove","path":"/spec/template/spec/containers/0/command"}]' || true
echo "SMOKE DONE"
```
Then run it (with a `svc/cto` port-forward on 8095 up):
```bash
chmod +x platform_mcp/verify-cto-levers.sh
kubectl --context kind-nano-bank -n nano-bank port-forward svc/cto 8095:8095 &   # via run_in_background at execution time
CTO_API_URL=http://localhost:8095 ./platform_mcp/verify-cto-levers.sh
```
Expected: the CTO's answer reports an executed restart; the ledger query shows a fresh `cto | rollout_restart | executed` row; `verify_agent_ledger()` returns `0`; `cfo` recovers after restore.

- [ ] **Step 5: Full offline suite green**

Run: `.venv/bin/python -m pytest platform_mcp cto csuite coo cfo -q`
Expected: PASS across the board; `cd api && cargo test -q` compiles + passes (skips live).

- [ ] **Step 6: Tear down the port-forward (scoped) + commit the smoke script**

```bash
# kill only the svc/cto forward by its PID (never a broad pkill)
git add platform_mcp/verify-cto-levers.sh
git commit -m "test(platform_mcp): live CTO infra-lever smoke (induce crashloop → restart → audited)"
```

---

## Self-Review

**Spec coverage** (against `2026-08-10-agent-cto-phase-b-infra-levers-design.md`):
- Two levers (restart, rollback), fully autonomous → Tasks 3/4/6/8.
- Trust model / LLM never touches k8s → Task 6 (server-side `_do_*`), Task 8 (prompt only instructs, doesn't gate).
- Guardrail 1 RBAC `resourceNames` → Task 7 (+ live negative check Task 9 Step 3).
- Guardrail 2 MCP allow-list → Task 2 (config) + Task 3 (`is_allowed`) + Task 6 (checked first).
- Guardrail 3 live self-verify → Task 6 (`k8s.deployments()`/`pods()`/`replicasets()` re-read inside `_do_*`).
- `levers.py`, `k8s_writer.py`, `audit.py`, `mcp_server.py` tools → Tasks 3/4/5/6.
- Bank endpoint, actor pinned to `cto`, no schema change → Task 1.
- `cto/agent.py` operator prompt → Task 8.
- RBAC/kubeconfig/config → Tasks 7/2.
- Audit ordering verify→act→audit, loud → Task 6 (`_do_*` order; audit after act) + Task 5 (raises) + Task 6 audit-failure test.
- Allow-list exact membership (apps only; excludes stateful/system/own-stack) → Task 2 default + test asserting `denied` set absent.
- Testing (pure levers, writer fake, audit mock, tool executed/refused/audit-fail, bank integration, live smoke) → Tasks 3/4/5/6/1/9.
- Rollout → Task 9.

No gaps.

**Placeholder scan:** every code step carries real code. The one operator-confirmation note in Task 9 (exact bank image name/`k8s/` apply) is a genuine environment detail to confirm at run time, not a code placeholder. No "TBD"/"handle edge cases"/"similar to Task N".

**Type consistency:** `is_allowed(allow_list, cluster, name)`, `restart_warranted(deployment, pods, threshold)`, `rollback_warranted(deployment, replicasets) -> (bool, int|None)` are used identically in Task 3 (def) and Task 6 (`_do_*`). `K8sWriter.rollout_restart(cluster, name)` / `rollback(cluster, name, target)` match between Task 4 and Task 6. `LedgerAudit.post_action(action, params, effect)` matches Task 5 and Task 6. `build_mcp(k8s, health, writer=None, audit=None, settings=None)` matches Task 6 and stays backward-compatible with Phase A's `build_mcp(k8s, health)` (Task 6 Step 4 note). The bank endpoint `{action, params, effect} → {seq, entry_hash}` matches between Task 1 and Task 5.
