# Agent CTO — Phase A (Observability Seat) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up an analyst-only CTO agent that observes the bank's technical platform — reliability (pod/service health, crashloops, restarts) and delivery (rollout status, image/version drift) — across both kind clusters, and answers grounded questions, taking no action and writing no code.

**Architecture:** A new `platform/` FastMCP server (`:8094`) reads the two kind clusters over the k8s API plus each in-cluster service's `/health`, and computes every rollup in pure, unit-tested Python (the model never does arithmetic). A new `cto/` agent (`:8095`, console `:8509`) is thin over the shared `csuite` harness (kimi-k2.6 via Ollama cloud), with a retargeted `claims.py`. Cross-cluster reads use one mounted, read-only kubeconfig Secret.

**Tech Stack:** Python 3.12, FastMCP (`mcp>=1.2,<2`), the official `kubernetes` client, `httpx`, LangGraph/LangChain 1.x, `csuite` shared harness, kind (Kubernetes-in-Docker), Docker.

## Global Constraints

- **Model:** kimi-k2.6 via `ChatOpenAI` @ `https://ollama.com/v1` (the C-suite's current main model), set by env `CTO_MODEL=kimi-k2.6` in the k8s manifest; config default may differ but the manifest pins it. Copy `coo/model_factory.py` verbatim (rename log channel to `cto.llm`).
- **No arithmetic in the model:** every derived figure comes from the `compute` tool; every raw figure is quoted exactly as a tool returned it.
- **Thin over `csuite`:** reuse `csuite.runtime`, `csuite.verifier`, `csuite.harness`, `csuite.console_ui`. The only CTO-specific code is the prompt, the retargeted `claims.py`, and config.
- **Backward compatibility (shared code):** the Task 1 `claims_fn` seam MUST default to today's behavior; `coo/` and `cfo/` callers stay unchanged and their existing tests stay green.
- **Read-only:** Phase A takes NO action on infra and writes NO code. Cluster access is read-only (`get`/`list`/`watch` only).
- **Ports:** platform MCP `8094`, CTO agent `8095`, CTO console `8509`.
- **Clusters/contexts:** `kind-nano-bank` (label `nano-bank`) and `kind-modern-core` (label `modern-core`).
- **Snap env (host shell):** before any `kubectl`/`docker`/`kind`/`podman`, `export XDG_RUNTIME_DIR=/run/user/1000 XDG_DATA_HOME=/home/bmartins/.local/share`.
- **Branch:** create and work on a fresh `agent-cto` branch off the current `agent-coo` HEAD (`git checkout -b agent-cto`), keeping this work off the large PR #55 line. Confirm the base with the operator at execution start.
- **Dependency floors:** `mcp>=1.2,<2`, `httpx>=0.27,<1`, `kubernetes>=29,<31`, `langgraph>=1,<2`, `langchain-core>=1,<2`, `langchain-openai>=1,<2`, `langchain-mcp-adapters>=0.3,<1`, `qdrant-client>=1.12,<2`, `fastapi>=0.115`, `uvicorn>=0.30`, `streamlit>=1.38`, `pytest>=8.0`.
- **Run tests from the repo root** (`.` = the worktree root) with the project `.venv` active so `import csuite`, `import platform_mcp`, `import cto`, `import operations`, `import coo` resolve.

> **⚠ NAMING CORRECTION (execution, 2026-08-09):** The Python package/dir is **`platform_mcp/`**, NOT `platform/`. A `platform/` package shadows the Python **stdlib** `platform` module for every importer once the repo root is on `sys.path` — verified empirically: pytest won't even start because CPython's own `uuid.py` does `import platform; platform.system()` and hits our package (`AttributeError`); the same breaks inside the container (WORKDIR `/app`). So **wherever a task below writes `platform/<x>.py`, `from platform.<x> import …`, `from platform import metrics`, `python -m platform.mcp_server`, `docker build … platform`, or `pytest platform …`, substitute `platform_mcp`** (dir `platform_mcp/`, imports `from platform_mcp.…`, container `COPY platform_mcp /app/platform_mcp` + `CMD python -m platform_mcp.mcp_server`, build context `platform_mcp`). The **k8s Deployment/Service `platform-mcp`, the image `nano-platform-mcp`, `PLATFORM_MCP_URL`, the service DNS `platform-mcp:8094`, the `platform_health` tool, and all prose "platform MCP"** stay exactly as written — those names are never `import`ed.
>
> **Environment:** the project `.venv` lives at the worktree root (gitignored), built from a Python 3.12 interpreter with the full `coo/requirements.txt` stack + `kubernetes` installed. Run every `pytest` as `.venv/bin/python -m pytest …`.

---

### Task 1: `csuite` injectable `claims_fn` seam (shared, backward-compatible)

Make the claims verifier pluggable per-agent so the CTO can guard different phantom concepts, without changing COO/CFO behavior.

**Files:**
- Modify: `csuite/verifier.py` (the `report` function, ~line 175)
- Modify: `csuite/runtime.py` (`ask` ~line 84, `ask_stream` ~line 141)
- Test: `csuite/tests/test_runtime_claims_seam.py` (create)

**Interfaces:**
- Consumes: `csuite.claims.unsupported_claims(answer: str, trace: list[dict]) -> list[str]` (existing default).
- Produces:
  - `verifier.report(answer, trace, *, revised, claims_fn=None) -> dict` — when `claims_fn` is None, uses `_claims.unsupported_claims`; else calls `claims_fn(answer, trace)`.
  - `runtime.ask(..., claims_fn=None)` and `runtime.ask_stream(..., claims_fn=None)` — thread the same `claims_fn` into their claims check AND into `verifier.report`. Default None ⇒ shared `claims.unsupported_claims`.

- [ ] **Step 1: Write the failing test**

```python
# csuite/tests/test_runtime_claims_seam.py
import asyncio

from csuite import runtime, verifier
from csuite.harness.memory import SafeMemory
from csuite.tests.fakes import FakeChatModel, fake_ops_tools


def _settings():
    from coo.config import Settings
    return Settings.from_env({})


def test_report_uses_injected_claims_fn():
    calls = {}

    def fake_claims(answer, trace):
        calls["hit"] = (answer, trace)
        return ["INJECTED-CLAIM"]

    rep = verifier.report("some answer", [], revised=False, claims_fn=fake_claims)
    assert rep["unsupported_claims"] == ["INJECTED-CLAIM"]
    assert calls["hit"][0] == "some answer"


def test_report_default_claims_fn_unchanged():
    # No claims_fn -> shared behavior: a clean platform-style answer has none.
    rep = verifier.report("All deployments are ready.", [], revised=False)
    assert rep["unsupported_claims"] == []


def test_runtime_ask_threads_claims_fn(monkeypatch):
    seen = {}

    def fake_claims(answer, trace):
        seen["answer"] = answer
        return []  # no claims -> no revise loop

    model = FakeChatModel([{"text": "Nothing derived here."}])
    out = asyncio.run(runtime.ask(
        settings=_settings(), message="hi", prompt="p", model=model,
        tools=fake_ops_tools(), agent="cto", memory=SafeMemory(None),
        claims_fn=fake_claims))
    assert seen["answer"] == "Nothing derived here."
    assert out["verification"]["unsupported_claims"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest csuite/tests/test_runtime_claims_seam.py -v`
Expected: FAIL — `report()`/`ask()` reject the unexpected `claims_fn` keyword (TypeError).

- [ ] **Step 3: Implement the seam in `verifier.report`**

In `csuite/verifier.py`, change the signature and the claims call:

```python
def report(answer: str, trace: list[dict], *, revised: bool,
           claims_fn=None) -> dict:
    _claims_fn = claims_fn or _claims.unsupported_claims
    # ... existing body, but replace the line
    #     "unsupported_claims": _claims.unsupported_claims(answer, trace),
    # with:
    #     "unsupported_claims": _claims_fn(answer, trace),
```

Leave everything else in `report` unchanged (the sentence loop at ~line 157 uses `_claims._sentences`, a helper — keep it).

- [ ] **Step 4: Implement the seam in `runtime.ask` and `runtime.ask_stream`**

In `csuite/runtime.py`, add `claims_fn=None` to both signatures and use it at both the check and the report:

```python
async def ask(*, settings, message: str, prompt: str, model, tools, agent: str,
              thread_id: Optional[str] = None, memory=None, claims_fn=None) -> dict:
    _claims_fn = claims_fn or claims.unsupported_claims
    # ... unchanged until the claims check:
    #   clms = claims.unsupported_claims(answer, rec.events())
    # becomes:
    #   clms = _claims_fn(answer, rec.events())
    # ... and the final return's report call:
    #   "verification": verifier.report(answer, rec.events(), revised=revised)
    # becomes:
    #   "verification": verifier.report(answer, rec.events(), revised=revised,
    #                                   claims_fn=_claims_fn)
```

Apply the identical change in `ask_stream` (its claims check ~line 141 and its `verifier.report(...)` ~line 153). The exception-path `verification` dict (~line 159) needs no change.

- [ ] **Step 5: Run the new test to verify it passes**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest csuite/tests/test_runtime_claims_seam.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Run the COO + CFO + csuite suites to prove no regression**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest csuite coo cfo -q`
Expected: PASS (all existing tests green — the seam is opt-in).

- [ ] **Step 7: Commit**

```bash
git add csuite/verifier.py csuite/runtime.py csuite/tests/test_runtime_claims_seam.py
git commit -m "feat(csuite): make claims verifier injectable per-agent (claims_fn seam)"
```

---

### Task 2: `platform/config.py`

**Files:**
- Create: `platform/__init__.py` (empty)
- Create: `platform/config.py`
- Test: `platform/tests/__init__.py` (empty), `platform/tests/test_config.py`

**Interfaces:**
- Produces: `Settings` dataclass with fields `mcp_port: int`, `kubeconfig_path: str`, `contexts: list[tuple[str, str]]` (context, cluster_label), `health_targets: list[tuple[str, str]]` (service_label, health_url), `timeout: float`; classmethod `from_env(env=None) -> Settings`.

Defaults: `mcp_port=8094`; `kubeconfig_path=/etc/platform/kubeconfig` (the mounted Secret); `contexts=[("kind-nano-bank","nano-bank"),("kind-modern-core","modern-core")]`; `health_targets` = the five nano-bank services (`bank-api http://bank-api:8081/health`, `coo http://coo:8093/health`, `cfo http://cfo:8089/health`, `operations-mcp http://operations-mcp:8092/health`, `finance-mcp http://finance-mcp:8088/health`); `timeout=10.0`. Env overrides: `MCP_PORT`, `KUBECONFIG_PATH`, `PLATFORM_CONTEXTS` (comma list of `ctx=label`), `HEALTH_TARGETS` (comma list of `label=url`), `REQUEST_TIMEOUT`.

- [ ] **Step 1: Write the failing test**

```python
# platform/tests/test_config.py
from platform.config import Settings


def test_defaults():
    s = Settings.from_env({})
    assert s.mcp_port == 8094
    assert s.kubeconfig_path == "/etc/platform/kubeconfig"
    assert ("kind-nano-bank", "nano-bank") in s.contexts
    assert ("kind-modern-core", "modern-core") in s.contexts
    labels = {lbl for lbl, _ in s.health_targets}
    assert {"bank-api", "coo", "cfo", "operations-mcp", "finance-mcp"} <= labels
    assert s.timeout == 10.0


def test_env_override():
    s = Settings.from_env({
        "MCP_PORT": "9",
        "PLATFORM_CONTEXTS": "ctxA=a,ctxB=b",
        "HEALTH_TARGETS": "svc=http://svc:1/health",
        "REQUEST_TIMEOUT": "3.5",
    })
    assert s.mcp_port == 9
    assert s.contexts == [("ctxA", "a"), ("ctxB", "b")]
    assert s.health_targets == [("svc", "http://svc:1/health")]
    assert s.timeout == 3.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest platform/tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: platform.config`.

- [ ] **Step 3: Write minimal implementation**

```python
# platform/config.py
from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import Mapping, Optional


_DEFAULT_CONTEXTS = [("kind-nano-bank", "nano-bank"),
                     ("kind-modern-core", "modern-core")]
_DEFAULT_HEALTH = [
    ("bank-api", "http://bank-api:8081/health"),
    ("coo", "http://coo:8093/health"),
    ("cfo", "http://cfo:8089/health"),
    ("operations-mcp", "http://operations-mcp:8092/health"),
    ("finance-mcp", "http://finance-mcp:8088/health"),
]


def _pairs(raw: str, sep: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        k, _, v = item.partition(sep)
        out.append((k.strip(), v.strip()))
    return out


@dataclass
class Settings:
    mcp_port: int
    kubeconfig_path: str
    contexts: list[tuple[str, str]]
    health_targets: list[tuple[str, str]]
    timeout: float

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        e = os.environ if env is None else env
        ctx_raw = e.get("PLATFORM_CONTEXTS")
        ht_raw = e.get("HEALTH_TARGETS")
        return cls(
            mcp_port=int(e.get("MCP_PORT", "8094")),
            kubeconfig_path=e.get("KUBECONFIG_PATH", "/etc/platform/kubeconfig"),
            contexts=_pairs(ctx_raw, "=") if ctx_raw else list(_DEFAULT_CONTEXTS),
            health_targets=_pairs(ht_raw, "=") if ht_raw else list(_DEFAULT_HEALTH),
            timeout=float(e.get("REQUEST_TIMEOUT", "10.0")),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest platform/tests/test_config.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add platform/__init__.py platform/config.py platform/tests/__init__.py platform/tests/test_config.py
git commit -m "feat(platform): config for the platform MCP (ports, contexts, health targets)"
```

---

### Task 3: `platform/metrics.py` — reliability rollups + `compute`

Pure dict-in/dict-out. `estate_health`, `restarts`, `service_health`, and the shared `compute` (verbatim from `operations/metrics.py`).

**Files:**
- Create: `platform/metrics.py`
- Test: `platform/tests/test_metrics.py`

**Interfaces:**
- Consumes: plain dicts (the shapes `k8s_client`/`health_client` produce — see Tasks 5/6).
- Produces:
  - `estate_health(deployments: list[dict]) -> dict` → `{"deployments":[{cluster,namespace,name,desired,ready,available,updated,unavailable,healthy}], "rollup":{total,healthy,degraded}}` (degraded ⇔ `ready < desired`).
  - `restarts(pods: list[dict], threshold: int = 5) -> dict` → `{"pods":[{cluster,namespace,name,restarts,crashlooping}], "crashlooping":[{cluster,namespace,name,container,reason,restarts}], "total_restarts":int}`.
  - `service_health(probes: list[dict]) -> dict` → `{"services":[...passthrough...], "healthy":[label...], "unhealthy":[label...], "failing_checks":[{service,check}]}`.
  - `compute(operation: str, values) -> dict` (identical to `operations.metrics.compute`).

Input shapes (produced by Task 5/6, asserted here with fakes):
- deployment dict: `{cluster,namespace,name,desired,ready,available,updated,unavailable,images:[str],conditions:[{type,status,reason}]}`
- pod dict: `{cluster,namespace,name,phase,containers:[{name,ready,restart_count,waiting_reason}]}`
- probe dict: `{service,ok,status,checks:{name:bool},error?}`

- [ ] **Step 1: Write the failing test**

```python
# platform/tests/test_metrics.py
from platform import metrics


def _dep(name, desired, ready, available=None, updated=None, unavailable=0,
         images=("app:1",), conditions=(), cluster="nano-bank", ns="nano-bank"):
    return {"cluster": cluster, "namespace": ns, "name": name, "desired": desired,
            "ready": ready, "available": available if available is not None else ready,
            "updated": updated if updated is not None else desired,
            "unavailable": unavailable, "images": list(images),
            "conditions": [dict(c) for c in conditions]}


def test_estate_health_flags_degraded():
    deps = [_dep("coo", 1, 1), _dep("bank-api", 2, 1, unavailable=1)]
    out = metrics.estate_health(deps)
    assert out["rollup"] == {"total": 2, "healthy": 1, "degraded": 1}
    by = {d["name"]: d for d in out["deployments"]}
    assert by["coo"]["healthy"] is True
    assert by["bank-api"]["healthy"] is False


def test_restarts_flags_crashloop_and_threshold():
    pods = [
        {"cluster": "nano-bank", "namespace": "nano-bank", "name": "coo-1",
         "phase": "Running", "containers": [
             {"name": "coo", "ready": True, "restart_count": 0, "waiting_reason": None}]},
        {"cluster": "nano-bank", "namespace": "nano-bank", "name": "cfo-1",
         "phase": "Running", "containers": [
             {"name": "cfo", "ready": False, "restart_count": 9,
              "waiting_reason": "CrashLoopBackOff"}]},
    ]
    out = metrics.restarts(pods, threshold=5)
    assert out["total_restarts"] == 9
    assert len(out["crashlooping"]) == 1
    cl = out["crashlooping"][0]
    assert cl["name"] == "cfo-1" and cl["reason"] == "CrashLoopBackOff"
    by = {p["name"]: p for p in out["pods"]}
    assert by["coo-1"]["crashlooping"] is False
    assert by["cfo-1"]["crashlooping"] is True


def test_service_health_splits_healthy_and_failing_checks():
    probes = [
        {"service": "bank-api", "ok": True, "status": "ok",
         "checks": {"db": True, "core": True}},
        {"service": "coo", "ok": False, "status": "degraded",
         "checks": {"ollama": True, "operations_mcp": False, "qdrant": True}},
    ]
    out = metrics.service_health(probes)
    assert out["healthy"] == ["bank-api"]
    assert out["unhealthy"] == ["coo"]
    assert {"service": "coo", "check": "operations_mcp"} in out["failing_checks"]


def test_compute_ratio_and_guard():
    assert metrics.compute("ratio", [9, 3])["result"] == __import__("decimal").Decimal("3.0000")
    assert "error" in metrics.compute("ratio", [5])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest platform/tests/test_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: platform.metrics`.

- [ ] **Step 3: Write minimal implementation**

```python
# platform/metrics.py
"""Pure platform-metric aggregations over the k8s reads and /health probes. No
IO — every function is dict-in/dict-out and unit-testable. Point-in-time (no
windows): the platform reads are snapshots."""
from __future__ import annotations
from decimal import Decimal


def _dec(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal(0)


def compute(operation: str, values) -> dict:
    """Deterministic arithmetic over numbers other tools already returned, so a
    derived figure stays tool-grounded. operation: mean|sum|ratio|percent|
    difference|product. Returns {operation, inputs, result} or {error, …}."""
    op = (operation or "").strip().lower()
    nums = [_dec(v) for v in (values or [])]
    two_ok = len(nums) >= 2 and nums[1] != 0
    if op in ("mean", "average", "avg"):
        result = (sum(nums) / len(nums)) if nums else None
    elif op == "sum":
        result = sum(nums) if nums else Decimal(0)
    elif op in ("ratio", "divide"):
        result = (nums[0] / nums[1]) if two_ok else None
    elif op in ("percent", "percentage", "share"):
        result = (nums[0] / nums[1] * 100) if two_ok else None
    elif op in ("difference", "subtract"):
        result = (nums[0] - sum(nums[1:])) if nums else None
    elif op in ("product", "multiply"):
        result = Decimal(1)
        for n in nums:
            result *= n
        if not nums:
            result = None
    else:
        return {"error": f"unknown operation '{operation}' "
                "(use mean|sum|ratio|percent|difference|product)"}
    if result is None:
        return {"error": "need valid operands — ratio/percent want two numbers "
                "with a non-zero denominator", "operation": op, "inputs": nums}
    places = Decimal("0.0001") if op in ("ratio", "divide") else Decimal("0.01")
    return {"operation": op, "inputs": nums, "result": result.quantize(places)}


def estate_health(deployments: list[dict]) -> dict:
    rows = []
    healthy = 0
    for d in deployments:
        ok = int(d.get("ready", 0)) >= int(d.get("desired", 0))
        healthy += 1 if ok else 0
        rows.append({
            "cluster": d.get("cluster"), "namespace": d.get("namespace"),
            "name": d.get("name"), "desired": int(d.get("desired", 0)),
            "ready": int(d.get("ready", 0)), "available": int(d.get("available", 0)),
            "updated": int(d.get("updated", 0)),
            "unavailable": int(d.get("unavailable", 0)), "healthy": ok,
        })
    total = len(rows)
    return {"deployments": rows,
            "rollup": {"total": total, "healthy": healthy,
                       "degraded": total - healthy}}


def restarts(pods: list[dict], threshold: int = 5) -> dict:
    rows = []
    crashlooping = []
    total = 0
    for p in pods:
        pod_restarts = 0
        pod_loop = False
        for c in p.get("containers", []):
            rc = int(c.get("restart_count", 0))
            pod_restarts += rc
            reason = c.get("waiting_reason")
            looping = reason == "CrashLoopBackOff" or rc > threshold
            if looping:
                pod_loop = True
                crashlooping.append({
                    "cluster": p.get("cluster"), "namespace": p.get("namespace"),
                    "name": p.get("name"), "container": c.get("name"),
                    "reason": reason or f"restarts>{threshold}", "restarts": rc,
                })
        total += pod_restarts
        rows.append({"cluster": p.get("cluster"), "namespace": p.get("namespace"),
                     "name": p.get("name"), "restarts": pod_restarts,
                     "crashlooping": pod_loop})
    return {"pods": rows, "crashlooping": crashlooping, "total_restarts": total}


def service_health(probes: list[dict]) -> dict:
    healthy, unhealthy, failing = [], [], []
    for pr in probes:
        label = pr.get("service")
        if pr.get("ok"):
            healthy.append(label)
        else:
            unhealthy.append(label)
        for name, ok in (pr.get("checks") or {}).items():
            if not ok:
                failing.append({"service": label, "check": name})
    return {"services": list(probes), "healthy": healthy, "unhealthy": unhealthy,
            "failing_checks": failing}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest platform/tests/test_metrics.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add platform/metrics.py platform/tests/test_metrics.py
git commit -m "feat(platform): reliability metrics (estate_health, restarts, service_health) + compute"
```

---

### Task 4: `platform/metrics.py` — delivery rollups (`rollouts`, `versions`, `platform_health`)

**Files:**
- Modify: `platform/metrics.py`
- Test: `platform/tests/test_metrics.py` (add tests)

**Interfaces:**
- Consumes: deployment dicts (with `conditions`, `updated`, `desired`, `available`, `images`); replicaset dicts `{cluster,namespace,name,owner_deployment,revision,desired,ready}`; the probe dicts.
- Produces:
  - `rollouts(deployments: list[dict], replicasets: list[dict]) -> dict` → `{"deployments":[{cluster,name,state,updated,desired,active_replicasets}], "rollup":{complete,progressing,stalled}}`. `state`: `stalled` if a `Progressing` condition has `reason == "ProgressDeadlineExceeded"`; `complete` if `updated == desired == available` and no stall; else `progressing`.
  - `versions(deployments: list[dict]) -> dict` → `{"by_app":{app:{"tags":[sorted distinct tags],"drift":bool,"instances":[{cluster,name,tag}]}}, "drift":[app...]}`. `app` = image repo without the `:tag`; `tag` = the part after the last `:` (or `"latest"` if none).
  - `platform_health(deployments, pods, replicasets, probes) -> dict` → `{"estate_health":…, "restarts":…, "rollouts":…, "versions":…, "service_health":…}`.

- [ ] **Step 1: Write the failing test (append)**

```python
# append to platform/tests/test_metrics.py
def _rs(name, owner, revision, desired=1, ready=1, cluster="nano-bank", ns="nano-bank"):
    return {"cluster": cluster, "namespace": ns, "name": name,
            "owner_deployment": owner, "revision": revision,
            "desired": desired, "ready": ready}


def test_rollouts_complete_progressing_stalled():
    deps = [
        _dep("coo", 1, 1, available=1, updated=1,
             conditions=[{"type": "Progressing", "status": "True",
                          "reason": "NewReplicaSetAvailable"}]),
        _dep("cfo", 2, 1, available=1, updated=1,
             conditions=[{"type": "Progressing", "status": "True",
                          "reason": "ReplicaSetUpdated"}]),
        _dep("bank-api", 2, 1, available=1, updated=1,
             conditions=[{"type": "Progressing", "status": "False",
                          "reason": "ProgressDeadlineExceeded"}]),
    ]
    rss = [_rs("coo-abc", "coo", 3), _rs("cfo-new", "cfo", 5), _rs("cfo-old", "cfo", 4)]
    out = metrics.rollouts(deps, rss)
    by = {d["name"]: d for d in out["deployments"]}
    assert by["coo"]["state"] == "complete"
    assert by["cfo"]["state"] == "progressing"
    assert by["cfo"]["active_replicasets"] == 2
    assert by["bank-api"]["state"] == "stalled"
    assert out["rollup"] == {"complete": 1, "progressing": 1, "stalled": 1}


def test_versions_flags_drift():
    deps = [
        _dep("coo", 1, 1, images=["nano-coo:dev"], cluster="nano-bank"),
        _dep("coo", 1, 1, images=["nano-coo:v2"], cluster="modern-core"),
        _dep("bank-api", 1, 1, images=["nano-bank:dev"]),
    ]
    out = metrics.versions(deps)
    assert out["by_app"]["nano-coo"]["drift"] is True
    assert out["by_app"]["nano-coo"]["tags"] == ["dev", "v2"]
    assert out["by_app"]["nano-bank"]["drift"] is False
    assert "nano-coo" in out["drift"]
    assert "nano-bank" not in out["drift"]


def test_platform_health_bundles_all_five():
    out = metrics.platform_health([], [], [], [])
    assert set(out) == {"estate_health", "restarts", "rollouts", "versions",
                        "service_health"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest platform/tests/test_metrics.py -k "rollouts or versions or platform_health" -v`
Expected: FAIL — `AttributeError: module 'platform.metrics' has no attribute 'rollouts'`.

- [ ] **Step 3: Write minimal implementation (append to `platform/metrics.py`)**

```python
def _progressing_reason(dep: dict) -> str | None:
    for c in dep.get("conditions", []):
        if c.get("type") == "Progressing":
            return c.get("reason")
    return None


def rollouts(deployments: list[dict], replicasets: list[dict]) -> dict:
    active_by_owner: dict[tuple, int] = {}
    for rs in replicasets:
        if int(rs.get("desired", 0)) > 0 or int(rs.get("ready", 0)) > 0:
            key = (rs.get("cluster"), rs.get("owner_deployment"))
            active_by_owner[key] = active_by_owner.get(key, 0) + 1
    rows = []
    tally = {"complete": 0, "progressing": 0, "stalled": 0}
    for d in deployments:
        desired = int(d.get("desired", 0))
        updated = int(d.get("updated", 0))
        available = int(d.get("available", 0))
        reason = _progressing_reason(d)
        if reason == "ProgressDeadlineExceeded":
            state = "stalled"
        elif updated == desired == available and desired >= 0:
            state = "complete"
        else:
            state = "progressing"
        tally[state] += 1
        rows.append({
            "cluster": d.get("cluster"), "name": d.get("name"), "state": state,
            "updated": updated, "desired": desired,
            "active_replicasets": active_by_owner.get(
                (d.get("cluster"), d.get("name")), 0),
        })
    return {"deployments": rows, "rollup": tally}


def _split_image(image: str) -> tuple[str, str]:
    # Split repo:tag on the LAST colon, but not a colon inside a registry:port.
    # A tag never contains '/'; a registry:port is followed by '/'. So only treat
    # the final ':' as a tag separator when the tail has no '/'.
    if ":" in image and "/" not in image.rsplit(":", 1)[1]:
        repo, tag = image.rsplit(":", 1)
        return repo, tag
    return image, "latest"


def versions(deployments: list[dict]) -> dict:
    by_app: dict[str, dict] = {}
    for d in deployments:
        for image in d.get("images", []):
            repo, tag = _split_image(image)
            app = repo.rsplit("/", 1)[-1]
            entry = by_app.setdefault(app, {"tags": set(), "instances": []})
            entry["tags"].add(tag)
            entry["instances"].append({"cluster": d.get("cluster"),
                                       "name": d.get("name"), "tag": tag})
    out_apps = {}
    drift = []
    for app, entry in by_app.items():
        tags = sorted(entry["tags"])
        is_drift = len(tags) > 1
        if is_drift:
            drift.append(app)
        out_apps[app] = {"tags": tags, "drift": is_drift,
                         "instances": entry["instances"]}
    return {"by_app": out_apps, "drift": sorted(drift)}


def platform_health(deployments: list[dict], pods: list[dict],
                    replicasets: list[dict], probes: list[dict]) -> dict:
    return {
        "estate_health": estate_health(deployments),
        "restarts": restarts(pods),
        "rollouts": rollouts(deployments, replicasets),
        "versions": versions(deployments),
        "service_health": service_health(probes),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest platform/tests/test_metrics.py -v`
Expected: PASS (all metrics tests).

- [ ] **Step 5: Commit**

```bash
git add platform/metrics.py platform/tests/test_metrics.py
git commit -m "feat(platform): delivery metrics (rollouts, versions, drift) + platform_health bundle"
```

---

### Task 5: `platform/k8s_client.py` — read-only cluster reads (injectable seam)

**Files:**
- Create: `platform/k8s_client.py`
- Test: `platform/tests/test_k8s_client.py`

**Interfaces:**
- Consumes: `Settings.contexts` (list of `(context, cluster_label)`), `Settings.kubeconfig_path`.
- Produces: `K8sClient(settings, loader=None)` where `loader(kubeconfig_path, context) -> (AppsV1Api, CoreV1Api)` is injectable (default builds real clients via `kubernetes.config.load_kube_config`). Methods `deployments()`, `pods()`, `replicasets()`, `events()` each iterate the configured contexts and return a flat `list[dict]` tagged with `cluster` (the label) and `namespace`, in the shapes Tasks 3/4 consume.

The default loader is only exercised live (Task 16); tests inject a fake that returns canned k8s API objects (simple namespaces/attribute objects).

- [ ] **Step 1: Write the failing test**

```python
# platform/tests/test_k8s_client.py
from types import SimpleNamespace as NS
from platform.config import Settings
from platform.k8s_client import K8sClient


def _dep_obj(name, desired, ready, available, updated, unavailable, image, reason):
    return NS(
        metadata=NS(name=name, namespace="nano-bank"),
        spec=NS(replicas=desired, template=NS(spec=NS(containers=[NS(image=image)]))),
        status=NS(ready_replicas=ready, available_replicas=available,
                  updated_replicas=updated, unavailable_replicas=unavailable,
                  conditions=[NS(type="Progressing", status="True", reason=reason)]),
    )


def _pod_obj(name, container, ready, restarts, waiting_reason):
    waiting = NS(reason=waiting_reason) if waiting_reason else None
    return NS(
        metadata=NS(name=name, namespace="nano-bank"),
        status=NS(phase="Running", container_statuses=[
            NS(name=container, ready=ready, restart_count=restarts,
               state=NS(waiting=waiting))]),
    )


def _settings():
    return Settings.from_env({"PLATFORM_CONTEXTS": "kind-nano-bank=nano-bank"})


class _FakeApps:
    def list_deployment_for_all_namespaces(self):
        return NS(items=[_dep_obj("coo", 1, 1, 1, 1, 0, "nano-coo:dev",
                                  "NewReplicaSetAvailable")])

    def list_replica_set_for_all_namespaces(self):
        return NS(items=[NS(metadata=NS(name="coo-abc", namespace="nano-bank",
                       owner_references=[NS(kind="Deployment", name="coo")],
                       annotations={"deployment.kubernetes.io/revision": "3"}),
                       spec=NS(replicas=1), status=NS(ready_replicas=1))])


class _FakeCore:
    def list_pod_for_all_namespaces(self):
        return NS(items=[_pod_obj("coo-1", "coo", True, 0, None)])

    def list_event_for_all_namespaces(self):
        return NS(items=[NS(metadata=NS(namespace="nano-bank"),
                            type="Normal", reason="Scheduled",
                            message="ok", involved_object=NS(kind="Pod", name="coo-1"))])


def _loader(path, context):
    return _FakeApps(), _FakeCore()


def test_deployments_tagged_and_flattened():
    c = K8sClient(_settings(), loader=_loader)
    deps = c.deployments()
    assert deps[0]["cluster"] == "nano-bank"
    assert deps[0]["name"] == "coo"
    assert deps[0]["desired"] == 1 and deps[0]["ready"] == 1
    assert deps[0]["images"] == ["nano-coo:dev"]
    assert deps[0]["conditions"][0]["reason"] == "NewReplicaSetAvailable"


def test_pods_extract_container_restart_and_waiting_reason():
    c = K8sClient(_settings(), loader=_loader)
    pods = c.pods()
    assert pods[0]["name"] == "coo-1"
    assert pods[0]["containers"][0]["restart_count"] == 0
    assert pods[0]["containers"][0]["waiting_reason"] is None


def test_replicasets_carry_owner_and_revision():
    c = K8sClient(_settings(), loader=_loader)
    rss = c.replicasets()
    assert rss[0]["owner_deployment"] == "coo"
    assert rss[0]["revision"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest platform/tests/test_k8s_client.py -v`
Expected: FAIL — `ModuleNotFoundError: platform.k8s_client`.

- [ ] **Step 3: Write minimal implementation**

```python
# platform/k8s_client.py
"""Read-only reads over the configured kube contexts. The `loader` seam returns
(AppsV1Api, CoreV1Api) per context; the default builds real clients from the
mounted kubeconfig, and tests inject a fake (no live cluster). Every read returns
plain dicts tagged with the cluster label + namespace, in the shapes metrics.py
consumes. Read-only: only list_* calls are made."""
from __future__ import annotations
from typing import Callable, Optional

from .config import Settings


def _default_loader(kubeconfig_path: str, context: str):
    from kubernetes import client, config
    config.load_kube_config(config_file=kubeconfig_path, context=context)
    return client.AppsV1Api(), client.CoreV1Api()


def _owner_deployment(rs) -> Optional[str]:
    for ref in (getattr(rs.metadata, "owner_references", None) or []):
        if getattr(ref, "kind", None) == "Deployment":
            return ref.name
    return None


def _revision(rs) -> Optional[int]:
    ann = getattr(rs.metadata, "annotations", None) or {}
    rev = ann.get("deployment.kubernetes.io/revision")
    return int(rev) if rev is not None else None


class K8sClient:
    def __init__(self, settings: Settings,
                 loader: Optional[Callable] = None):
        self._s = settings
        self._loader = loader or _default_loader

    def _apis(self):
        for context, label in self._s.contexts:
            apps, core = self._loader(self._s.kubeconfig_path, context)
            yield label, apps, core

    def deployments(self) -> list[dict]:
        out = []
        for label, apps, _core in self._apis():
            for d in apps.list_deployment_for_all_namespaces().items:
                st = d.status
                images = [c.image for c in d.spec.template.spec.containers]
                conds = [{"type": c.type, "status": c.status, "reason": c.reason}
                         for c in (getattr(st, "conditions", None) or [])]
                out.append({
                    "cluster": label, "namespace": d.metadata.namespace,
                    "name": d.metadata.name,
                    "desired": d.spec.replicas or 0,
                    "ready": getattr(st, "ready_replicas", 0) or 0,
                    "available": getattr(st, "available_replicas", 0) or 0,
                    "updated": getattr(st, "updated_replicas", 0) or 0,
                    "unavailable": getattr(st, "unavailable_replicas", 0) or 0,
                    "images": images, "conditions": conds,
                })
        return out

    def pods(self) -> list[dict]:
        out = []
        for label, _apps, core in self._apis():
            for p in core.list_pod_for_all_namespaces().items:
                containers = []
                for cs in (getattr(p.status, "container_statuses", None) or []):
                    waiting = getattr(getattr(cs.state, "waiting", None), "reason", None)
                    containers.append({
                        "name": cs.name, "ready": bool(cs.ready),
                        "restart_count": int(cs.restart_count or 0),
                        "waiting_reason": waiting,
                    })
                out.append({"cluster": label, "namespace": p.metadata.namespace,
                            "name": p.metadata.name, "phase": p.status.phase,
                            "containers": containers})
        return out

    def replicasets(self) -> list[dict]:
        out = []
        for label, apps, _core in self._apis():
            for rs in apps.list_replica_set_for_all_namespaces().items:
                out.append({
                    "cluster": label, "namespace": rs.metadata.namespace,
                    "name": rs.metadata.name,
                    "owner_deployment": _owner_deployment(rs),
                    "revision": _revision(rs),
                    "desired": rs.spec.replicas or 0,
                    "ready": getattr(rs.status, "ready_replicas", 0) or 0,
                })
        return out

    def events(self) -> list[dict]:
        out = []
        for label, _apps, core in self._apis():
            for ev in core.list_event_for_all_namespaces().items:
                io = getattr(ev, "involved_object", None)
                out.append({
                    "cluster": label, "namespace": ev.metadata.namespace,
                    "type": ev.type, "reason": ev.reason, "message": ev.message,
                    "object": f"{getattr(io,'kind','')}/{getattr(io,'name','')}",
                })
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest platform/tests/test_k8s_client.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add platform/k8s_client.py platform/tests/test_k8s_client.py
git commit -m "feat(platform): read-only k8s_client over configured contexts (injectable loader)"
```

---

### Task 6: `platform/health_client.py` — `/health` probes (never raises)

**Files:**
- Create: `platform/health_client.py`
- Test: `platform/tests/test_health_client.py`

**Interfaces:**
- Consumes: `Settings.health_targets` (list of `(label, url)`), `Settings.timeout`.
- Produces: `HealthClient(settings, transport=None)` (httpx transport injectable for tests); `probe() -> list[dict]` returning one `{service, ok, status, checks, error?}` per target. A down/erroring service yields `{service, ok: False, status: "unreachable", checks: {}, error: "..."}` — never raises.

- [ ] **Step 1: Write the failing test**

```python
# platform/tests/test_health_client.py
import httpx
from platform.config import Settings
from platform.health_client import HealthClient


def _settings():
    return Settings.from_env({
        "HEALTH_TARGETS": "bank-api=http://bank-api/health,coo=http://coo/health"})


def _handler(request):
    if request.url.host == "bank-api":
        return httpx.Response(200, json={"status": "ok",
                                         "checks": {"db": True, "core": True}})
    raise httpx.ConnectError("refused")


def test_probe_reports_ok_and_unreachable():
    transport = httpx.MockTransport(_handler)
    out = HealthClient(_settings(), transport=transport).probe()
    by = {p["service"]: p for p in out}
    assert by["bank-api"]["ok"] is True
    assert by["bank-api"]["checks"] == {"db": True, "core": True}
    assert by["coo"]["ok"] is False
    assert by["coo"]["status"] == "unreachable"
    assert "refused" in by["coo"]["error"]


def test_non_ok_status_is_not_ok():
    def handler(request):
        return httpx.Response(200, json={"status": "degraded",
                                         "checks": {"ollama": False}})
    out = HealthClient(_settings(), transport=httpx.MockTransport(handler)).probe()
    assert all(p["ok"] is False for p in out)
    assert out[0]["checks"] == {"ollama": False}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest platform/tests/test_health_client.py -v`
Expected: FAIL — `ModuleNotFoundError: platform.health_client`.

- [ ] **Step 3: Write minimal implementation**

```python
# platform/health_client.py
"""Probe each configured service `/health`. A down service is DATA, not an
exception — probe() never raises; an unreachable or non-2xx service becomes an
ok:False row. `transport` is injectable so tests stub the network."""
from __future__ import annotations
from typing import Optional

import httpx

from .config import Settings


class HealthClient:
    def __init__(self, settings: Settings,
                 transport: Optional[httpx.BaseTransport] = None):
        self._s = settings
        self._http = httpx.Client(timeout=settings.timeout, transport=transport)

    def _one(self, label: str, url: str) -> dict:
        try:
            r = self._http.get(url)
            body = r.json() if r.headers.get("content-type", "").startswith(
                "application/json") else {}
            status = body.get("status", "ok" if r.is_success else "error")
            ok = r.is_success and status == "ok"
            return {"service": label, "ok": bool(ok), "status": status,
                    "checks": body.get("checks", {})}
        except Exception as e:  # noqa: BLE001 — a down service is data
            return {"service": label, "ok": False, "status": "unreachable",
                    "checks": {}, "error": f"{type(e).__name__}: {e}"}

    def probe(self) -> list[dict]:
        return [self._one(label, url) for label, url in self._s.health_targets]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest platform/tests/test_health_client.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add platform/health_client.py platform/tests/test_health_client.py
git commit -m "feat(platform): health_client probes service /health, never raises"
```

---

### Task 7: `platform/mcp_server.py` — FastMCP tools

**Files:**
- Create: `platform/mcp_server.py`
- Test: `platform/tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `K8sClient`, `HealthClient`, `metrics`, `Settings`.
- Produces: `build_mcp(k8s, health) -> FastMCP` registering tools `estate_health`, `restarts`, `rollouts`, `versions`, `service_health`, `platform_health`, `compute`; `_stringify(obj)` (verbatim from `operations/mcp_server.py`); `main()` serving `mcp.streamable_http_app()` on `settings.mcp_port`.

Test strategy: FastMCP tool callables aren't trivially invocable off the server object, so mirror `operations`' seam — build with fake `k8s`/`health` objects and assert `build_mcp` returns a `FastMCP` whose registered tool names match the expected set. (Tool *behavior* is already covered by the metrics/client unit tests; this task wires them.)

- [ ] **Step 1: Write the failing test**

```python
# platform/tests/test_mcp_server.py
from platform.mcp_server import build_mcp, _stringify
from decimal import Decimal


class _FakeK8s:
    def deployments(self): return []
    def pods(self): return []
    def replicasets(self): return []
    def events(self): return []


class _FakeHealth:
    def probe(self): return []


def test_stringify_decimals_deep():
    out = _stringify({"a": Decimal("1.50"), "b": [Decimal("2")], "c": "x"})
    assert out == {"a": "1.50", "b": ["2"], "c": "x"}


def test_build_mcp_registers_expected_tools():
    import anyio
    mcp = build_mcp(_FakeK8s(), _FakeHealth())
    tools = anyio.run(mcp.list_tools)
    names = {t.name for t in tools}
    assert names == {"estate_health", "restarts", "rollouts", "versions",
                     "service_health", "platform_health", "compute"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest platform/tests/test_mcp_server.py -v`
Expected: FAIL — `ModuleNotFoundError: platform.mcp_server`.

- [ ] **Step 3: Write minimal implementation**

```python
# platform/mcp_server.py
"""The platform MCP: the CTO's technical perception surface. Each tool reads the
kube estate (both clusters) and/or the services' /health, and returns a pure
metrics rollup. Decimals stringified for JSON transport. READ-ONLY — no tool
mutates anything (Phase A is analyst-only)."""
from __future__ import annotations
from decimal import Decimal

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .config import Settings
from .k8s_client import K8sClient
from .health_client import HealthClient
from . import metrics


def _stringify(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _stringify(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_stringify(v) for v in obj]
    return obj


def build_mcp(k8s, health) -> FastMCP:
    mcp = FastMCP(
        "nano-platform",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False),
    )

    @mcp.tool()
    def estate_health() -> dict:
        """Per-deployment desired/ready/available across BOTH clusters + a rollup
        (total/healthy/degraded, where degraded = ready < desired). Reliability."""
        return _stringify(metrics.estate_health(k8s.deployments()))

    @mcp.tool()
    def restarts() -> dict:
        """Per-pod restart totals + a crashlooping list (CrashLoopBackOff or
        restarts over threshold) + a total, across both clusters. Reliability."""
        return _stringify(metrics.restarts(k8s.pods()))

    @mcp.tool()
    def rollouts() -> dict:
        """Per-deployment rollout state (complete/progressing/stalled, updated vs
        desired) + a rollup. Delivery."""
        return _stringify(metrics.rollouts(k8s.deployments(), k8s.replicasets()))

    @mcp.tool()
    def versions() -> dict:
        """Per-app container image tag(s) across the estate; flags drift where the
        same app runs different tags in different places. Delivery."""
        return _stringify(metrics.versions(k8s.deployments()))

    @mcp.tool()
    def service_health() -> dict:
        """Each service's /health self-report (dependency probes) split into
        healthy/unhealthy + the failing dependency checks. Reliability."""
        return _stringify(metrics.service_health(health.probe()))

    @mcp.tool()
    def platform_health() -> dict:
        """One-shot bundle: estate_health, restarts, rollouts, versions and
        service_health — the whole technical picture in one call."""
        return _stringify(metrics.platform_health(
            k8s.deployments(), k8s.pods(), k8s.replicasets(), health.probe()))

    @mcp.tool()
    def compute(operation: str, values: list[float]) -> dict:
        """Deterministic arithmetic on numbers you already got from other tools,
        so a derived figure stays tool-grounded — use this instead of doing the
        math yourself. operation: mean|sum|ratio|percent|difference|product.
        values: the exact tool-returned numbers, in order (e.g. degraded share =
        percent, values=[degraded, total])."""
        return _stringify(metrics.compute(operation, values))

    return mcp


def main():
    settings = Settings.from_env()
    k8s = K8sClient(settings)
    health = HealthClient(settings)
    mcp = build_mcp(k8s, health)
    import uvicorn
    uvicorn.run(mcp.streamable_http_app(), host="0.0.0.0", port=settings.mcp_port)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest platform/tests/test_mcp_server.py -v`
Expected: PASS (2 tests). If `mcp.list_tools` is async-incompatible with `anyio.run` in the installed FastMCP version, fall back to asserting the tools via `mcp._tool_manager.list_tools()` (sync) — keep the same name-set assertion.

- [ ] **Step 5: Commit**

```bash
git add platform/mcp_server.py platform/tests/test_mcp_server.py
git commit -m "feat(platform): FastMCP server wiring the platform read tools"
```

---

### Task 8: `platform/` packaging + manifest + smoke script

**Files:**
- Create: `platform/requirements.txt`, `platform/Dockerfile`, `platform/.dockerignore`, `platform/.gitignore`
- Create: `platform/k8s/platform-mcp.yaml`
- Create: `platform/verify-platform.sh`

No unit test — verified at rollout (Task 16). This task produces the deployable image + manifest.

**Interfaces:**
- Produces: image `nano-platform-mcp:dev` (built from `platform/`), Deployment+Service `platform-mcp` in `nano-bank`, mounting the `nano-platform-kubeconfig` Secret at `/etc/platform/kubeconfig` (the Secret is minted in Task 15).

- [ ] **Step 1: Write `platform/requirements.txt`**

```
mcp>=1.2,<2
kubernetes>=29,<31
httpx>=0.27,<1
uvicorn>=0.30
pytest>=8.0
```

- [ ] **Step 2: Write `platform/Dockerfile`**

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app/platform
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "platform.mcp_server"]
```

- [ ] **Step 3: Write `platform/.dockerignore` and `platform/.gitignore`**

`.dockerignore`:
```
.venv/
__pycache__/
*.pyc
tests/
verify-platform.sh
k8s/
Dockerfile
.dockerignore
```
`.gitignore`:
```
__pycache__/
*.pyc
```

- [ ] **Step 4: Write `platform/k8s/platform-mcp.yaml`**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: platform-mcp
  namespace: nano-bank
  labels: { app: platform-mcp }
spec:
  replicas: 1
  selector: { matchLabels: { app: platform-mcp } }
  template:
    metadata: { labels: { app: platform-mcp } }
    spec:
      containers:
      - name: mcp
        image: nano-platform-mcp:dev
        imagePullPolicy: Never
        ports: [ { containerPort: 8094 } ]
        env:
        - { name: MCP_PORT,        value: "8094" }
        - { name: KUBECONFIG_PATH, value: /etc/platform/kubeconfig }
        volumeMounts:
        - name: kubeconfig
          mountPath: /etc/platform
          readOnly: true
      volumes:
      - name: kubeconfig
        secret:
          secretName: nano-platform-kubeconfig
---
apiVersion: v1
kind: Service
metadata:
  name: platform-mcp
  namespace: nano-bank
spec:
  selector: { app: platform-mcp }
  ports: [ { port: 8094, targetPort: 8094 } ]
```

- [ ] **Step 5: Write `platform/verify-platform.sh`**

```bash
#!/bin/bash
# Live smoke: with the platform MCP on :8094 (port-forwarded or in-cluster) and a
# valid cross-cluster kubeconfig, call platform_health over MCP and assert real
# JSON with both clusters' deployments comes back. Run with the venv active.
set -euo pipefail
BASE="${PLATFORM_MCP_URL:-http://localhost:8094/mcp}"
python - "$BASE" <<'PY'
import sys, anyio
from mcp.client.streamable_http import streamablehttp_client
from mcp.client.session import ClientSession

async def main(url):
    async with streamablehttp_client(url) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("platform_health", {})
            print(res.content[0].text[:600])

anyio.run(main, sys.argv[1])
PY
echo "OK"
```

- [ ] **Step 6: Make the script executable + commit**

```bash
chmod +x platform/verify-platform.sh
git add platform/requirements.txt platform/Dockerfile platform/.dockerignore \
        platform/.gitignore platform/k8s/platform-mcp.yaml platform/verify-platform.sh
git commit -m "build(platform): Dockerfile, manifest (kubeconfig secret mount) + smoke script"
```

---

### Task 9: `cto/config.py`

**Files:**
- Create: `cto/__init__.py` (empty)
- Create: `cto/config.py`
- Test: `cto/tests/__init__.py` (empty), `cto/tests/test_config.py`

**Interfaces:**
- Produces: `Settings` dataclass mirroring `coo/config.py` with fields `ollama_api_key`, `ollama_base_url`, `cto_model` (default `kimi-k2.6`), `platform_mcp_url` (default `http://localhost:8094/mcp`), `qdrant_url`, `memory_collection` (`cto_memory`), `memory_namespace` (`cto`), `api_port` (8095), `console_port` (8509), `context_token_threshold`, `subagent_max_depth`; `from_env(env=None)`.

- [ ] **Step 1: Write the failing test**

```python
# cto/tests/test_config.py
from cto.config import Settings


def test_defaults():
    s = Settings.from_env({})
    assert s.cto_model == "kimi-k2.6"
    assert s.platform_mcp_url == "http://localhost:8094/mcp"
    assert s.memory_namespace == "cto"
    assert s.memory_collection == "cto_memory"
    assert s.api_port == 8095
    assert s.console_port == 8509
    assert s.subagent_max_depth == 2


def test_env_override():
    s = Settings.from_env({"CTO_MODEL": "kimi-k3", "API_PORT": "9999",
                           "PLATFORM_MCP_URL": "http://plat:1/mcp"})
    assert s.cto_model == "kimi-k3"
    assert s.api_port == 9999
    assert s.platform_mcp_url == "http://plat:1/mcp"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest cto/tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: cto.config`.

- [ ] **Step 3: Write minimal implementation** (mirror `coo/config.py`)

```python
# cto/config.py
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass
class Settings:
    ollama_api_key: str
    ollama_base_url: str
    cto_model: str
    platform_mcp_url: str
    qdrant_url: str
    memory_collection: str
    memory_namespace: str
    api_port: int
    console_port: int
    context_token_threshold: int
    subagent_max_depth: int

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        e = os.environ if env is None else env

        def g(k, d=""):
            return e.get(k, d)

        return cls(
            ollama_api_key=g("OLLAMA_API_KEY"),
            ollama_base_url=g("OLLAMA_BASE_URL", "https://ollama.com/v1"),
            cto_model=g("CTO_MODEL", "kimi-k2.6"),
            platform_mcp_url=g("PLATFORM_MCP_URL", "http://localhost:8094/mcp"),
            qdrant_url=g("QDRANT_URL", "http://localhost:8600"),
            memory_collection=g("MEMORY_COLLECTION", "cto_memory"),
            memory_namespace=g("MEMORY_NAMESPACE", "cto"),
            api_port=int(g("API_PORT", "8095")),
            console_port=int(g("CONSOLE_PORT", "8509")),
            context_token_threshold=int(g("CONTEXT_TOKEN_THRESHOLD", "60000")),
            subagent_max_depth=int(g("SUBAGENT_MAX_DEPTH", "2")),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest cto/tests/test_config.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add cto/__init__.py cto/config.py cto/tests/__init__.py cto/tests/test_config.py
git commit -m "feat(cto): config for the CTO agent (ports, platform MCP, memory namespace)"
```

---

### Task 10: `cto/claims.py` — retargeted phantom guard (books→CFO, money-ops→COO, fraud)

Drops window-grounding (platform reads are point-in-time); guards phantom concepts outside the CTO's lane.

**Files:**
- Create: `cto/claims.py`
- Test: `cto/tests/test_claims.py`

**Interfaces:**
- Consumes: `answer: str`, `trace: list[dict]` (trace unused for grounding here, kept for signature parity with `csuite.claims.unsupported_claims`).
- Produces: `unsupported_claims(answer: str, trace: list[dict]) -> list[str]` — returns a list of issue strings for any phantom concept named without a disclaimer. Phantom groups: `books` (P&L, NIM, RAROC, net interest margin, profitability → "that's the CFO's"), `money_ops` (float, rail throughput, settlement volume → "that's the COO's"), `fraud`/`aml` (out of scope).

- [ ] **Step 1: Write the failing test**

```python
# cto/tests/test_claims.py
from cto.claims import unsupported_claims


def test_books_mention_without_disclaimer_is_flagged():
    issues = unsupported_claims("Our net interest margin improved to 3.2%.", [])
    assert any("CFO" in i for i in issues)


def test_books_mention_with_disclaimer_is_allowed():
    issues = unsupported_claims(
        "I cannot speak to NIM — that is the CFO's domain.", [])
    assert issues == []


def test_money_ops_mention_without_disclaimer_is_flagged():
    issues = unsupported_claims("Settlement float across the rails is $2M.", [])
    assert any("COO" in i for i in issues)


def test_fraud_is_out_of_scope():
    issues = unsupported_claims("The fraud rate is elevated.", [])
    assert any("scope" in i.lower() for i in issues)


def test_pure_platform_answer_is_clean():
    issues = unsupported_claims(
        "2 of 7 deployments are degraded; coo is crashlooping (9 restarts).", [])
    assert issues == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest cto/tests/test_claims.py -v`
Expected: FAIL — `ModuleNotFoundError: cto.claims`.

- [ ] **Step 3: Write minimal implementation** (structure mirrors `csuite/claims.py`, window-grounding removed)

```python
# cto/claims.py
"""Named-claim grounding for the Agent CTO. The number verifier grounds figures;
this grounds *claims* about phantom concepts outside the CTO's lane: the books
(the CFO's), money-movement operations detail (the COO's), and fraud/AML. The
platform reads are point-in-time snapshots, so there is NO window grounding here
(unlike the COO). Deterministic, cue-based, disclaimer-aware — no LLM."""
from __future__ import annotations
import re

_SPLIT = re.compile(r"[.!?\n|]+")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SPLIT.split(text or "") if s.strip()]


# A negation / inability / deferral cue: the CTO honestly staying in its lane.
_DISCLAIMER = re.compile(
    r"\b(can ?not|can'?t|do not|don'?t|does not|doesn'?t|unable|outside"
    r"|out of (?:my )?scope|not available|CFO|COO"
    r"|not\b[^.]*\b(?:see|track|produce|capture|have|show|cover))\b",
    re.I)

# Concepts no platform tool provides. Grouping lets a disclaimer on any label
# cover every spelling. The offered redirect names the right officer.
_PHANTOM_CONCEPTS = {
    "books": (["net interest margin", "nim", "raroc", "profitability", "p&l",
               "p and l", "return on assets", "margin"],
              "the books (P&L / NIM / RAROC) — that's the CFO's domain"),
    "money_ops": (["settlement volume", "settlement float", "rail throughput",
                   "float position", "clearing float", "money movement"],
                  "money-movement operations detail — that's the COO's domain"),
    "fraud": (["fraud rate", "fraudulent", "fraud"], "fraud data — out of scope"),
    "aml": (["anti-money-laundering", "anti money laundering", "money laundering",
             "money-laundering", "aml"], "AML data — out of scope"),
}


def _concept_present(low: str, labels: list[str]) -> bool:
    return any(re.search(rf"\b{re.escape(lab)}\b", low) for lab in labels)


def unsupported_claims(answer: str, trace: list[dict]) -> list[str]:
    """Phantom-concept membership guard scoped to the WHOLE answer: an honest
    deferral discloses in one sentence and may name the concept in others, so a
    sentence-local guard would flag the explanatory mentions."""
    sents = [(s.lower(), bool(_DISCLAIMER.search(s))) for s in _sentences(answer)]

    disclaimed: set[str] = set()
    for low, disc in sents:
        if disc:
            for cid, (labels, _name) in _PHANTOM_CONCEPTS.items():
                if _concept_present(low, labels):
                    disclaimed.add(cid)

    issues: list[str] = []
    low_all = (answer or "").lower()
    for cid, (labels, name) in _PHANTOM_CONCEPTS.items():
        if cid not in disclaimed and _concept_present(low_all, labels):
            issues.append(name)

    seen: set[str] = set()
    out: list[str] = []
    for i in issues:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out
```

> Note: `"margin"` is deliberately in the `books` labels but is a substring risk — the `\b…\b` word-boundary match means "margin" matches the standalone word only. The `test_pure_platform_answer_is_clean` case has no such word, so it stays clean. If a later platform phrase legitimately uses "margin" (unlikely), tighten to `net interest margin` only.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest cto/tests/test_claims.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add cto/claims.py cto/tests/test_claims.py
git commit -m "feat(cto): retargeted claims guard (books->CFO, money-ops->COO, fraud out of scope)"
```

---

### Task 11: `cto/model_factory.py` + `cto/tools.py`

Trivial copies of the COO equivalents, retargeted to the platform MCP.

**Files:**
- Create: `cto/model_factory.py` (copy of `coo/model_factory.py`, log channel `cto.llm`, `settings.cto_model`)
- Create: `cto/tools.py`

**Interfaces:**
- Produces: `cto.model_factory.{build_model, resolve_model, init_models, llm, backend_healthcheck}` (identical API to `coo.model_factory`, reading `settings.cto_model`); `cto.tools.get_tools(settings) -> list` over the platform MCP.

- [ ] **Step 1: Write `cto/model_factory.py`**

Copy `coo/model_factory.py` verbatim, changing only:
- `log = logging.getLogger("cto.llm")`
- in `resolve_model`: `model = settings.cto_model`
- in `backend_healthcheck`: `return _default_probe(settings.cto_model, settings)`

(Full file — copy from `coo/model_factory.py` and apply the three edits above.)

- [ ] **Step 2: Write `cto/tools.py`**

```python
# cto/tools.py
"""The CTO's domain tools: the platform MCP (read-only k8s + service /health)."""
from __future__ import annotations
from .config import Settings


def mcp_client(settings: Settings):
    from langchain_mcp_adapters.client import MultiServerMCPClient
    return MultiServerMCPClient({
        "platform": {"url": settings.platform_mcp_url,
                     "transport": "streamable_http"}})


async def get_tools(settings: Settings) -> list:
    return await mcp_client(settings).get_tools()
```

- [ ] **Step 3: Sanity-import (no dedicated test; covered by Task 12/13)**

Run: `cd /home/bmartins/dev/nano-bank && python -c "import cto.model_factory, cto.tools; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 4: Commit**

```bash
git add cto/model_factory.py cto/tools.py
git commit -m "feat(cto): model factory (kimi-k2.6) + platform MCP tools"
```

---

### Task 12: `cto/agent.py` — CTO_PROMPT + harnessed ask/ask_stream (claims_fn wired)

**Files:**
- Create: `cto/agent.py`
- Modify: `csuite/tests/fakes.py` (add `fake_platform_tools()`)
- Test: `cto/tests/test_agent.py`

**Interfaces:**
- Consumes: `csuite.runtime.ask/ask_stream` (now with `claims_fn`), `cto.claims.unsupported_claims`, `cto.model_factory.llm`, `cto.tools.get_tools`.
- Produces:
  - `cto.agent.ask(settings, message, thread_id=None, *, memory=None) -> dict`
  - `cto.agent.ask_stream(settings, message, thread_id=None, *, memory=None) -> AsyncIterator[dict]`
  - Both pass `agent="cto"` and `claims_fn=cto_claims.unsupported_claims`.
  - `csuite.tests.fakes.fake_platform_tools() -> list` (canned `estate_health`, `service_health`).

- [ ] **Step 1: Add `fake_platform_tools()` to `csuite/tests/fakes.py`**

```python
# append to csuite/tests/fakes.py
def fake_platform_tools() -> list:
    @tool
    def estate_health() -> dict:
        """Canned estate health."""
        return {"deployments": [{"name": "coo", "desired": 1, "ready": 1,
                                 "healthy": True}],
                "rollup": {"total": 1, "healthy": 1, "degraded": 0}}

    @tool
    def service_health() -> dict:
        """Canned service health."""
        return {"healthy": ["bank-api"], "unhealthy": [], "failing_checks": []}

    return [estate_health, service_health]
```

- [ ] **Step 2: Write the failing test**

```python
# cto/tests/test_agent.py
import asyncio

from cto import agent as agent_mod
from cto.config import Settings
from csuite.harness.memory import SafeMemory
from csuite.tests.fakes import FakeChatModel, fake_platform_tools


def _settings():
    return Settings.from_env({})


def _patch(monkeypatch, model):
    monkeypatch.setattr(agent_mod.mf, "llm", lambda **k: model)

    async def _tools(settings):
        return fake_platform_tools()

    monkeypatch.setattr(agent_mod, "get_tools", _tools)


def test_grounded_estate_review_reports_clean(monkeypatch):
    model = FakeChatModel([
        {"tool": "write_plan", "args": {"steps": ["estate", "answer"]}},
        {"tool": "estate_health", "args": {}},
        {"text": "1 of 1 deployments healthy; 0 degraded."},
    ])
    _patch(monkeypatch, model)
    out = asyncio.run(agent_mod.ask(_settings(), "estate review?",
                                    memory=SafeMemory(None)))
    assert "0 degraded" in out["answer"]
    assert out["verification"]["ungrounded"] == []
    assert any(e.get("name") == "write_plan" for e in out["trace"])


def test_books_question_is_flagged_by_claims_and_revised(monkeypatch):
    # If the model wanders into the CFO's lane, the cto claims_fn catches it and
    # triggers one revise pass; the revised answer defers.
    model = FakeChatModel([
        {"text": "Our net interest margin is 3.2%."},          # out of lane
        {"text": "NIM is the CFO's domain; I cannot speak to it."},  # revised
    ])
    _patch(monkeypatch, model)
    out = asyncio.run(agent_mod.ask(_settings(), "how's NIM?",
                                    memory=SafeMemory(None)))
    assert out["verification"]["revised"] is True
    assert out["verification"]["unsupported_claims"] == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest cto/tests/test_agent.py -v`
Expected: FAIL — `ModuleNotFoundError: cto.agent`.

- [ ] **Step 4: Write minimal implementation**

```python
# cto/agent.py
"""The Agent CTO — an analyst technical officer over the platform MCP, wrapped in
the shared csuite harness. It observes the bank's kube estate (both clusters) and
each service's /health: reliability (pod/service health, crashloops, restarts) and
delivery (rollout status, image/version drift). Phase A is ANALYST-ONLY: it takes
NO action on infra and writes NO code."""
from __future__ import annotations
from typing import AsyncIterator, Optional

from csuite import runtime

from .config import Settings
from . import model_factory as mf
from . import claims as cto_claims
from .tools import get_tools

CTO_PROMPT = (
    "You are the Chief Technology Officer of nano-bank, a Canadian challenger "
    "bank; you speak for the health and delivery of the bank's technical "
    "platform. Answer ONLY from your platform tools; never fabricate a figure, "
    "count, status or version. For any DERIVED figure — a ratio, share, "
    "percentage, average or difference — call the `compute` tool with the exact "
    "numbers the other tools returned (e.g. the degraded share is percent of "
    "degraded over total: compute(percent, [degraded, total])). NEVER do the "
    "arithmetic yourself and NEVER tell the user to calculate it. Quote every raw "
    "figure EXACTLY as the tool returned it (a restart count, a ready/desired "
    "pair, an image tag) — never round or invent. Your lane is the TECHNICAL "
    "platform: reliability (deployment/pod/service health, crashloops, restarts) "
    "and delivery (rollout status, image/version drift) across BOTH clusters. "
    "Stay in your lane. If asked about the books — profitability, NIM, RAROC, the "
    "P&L — say that is the CFO's domain. If asked about money-movement operations "
    "detail — float, rail throughput, settlement volumes — say that is the COO's "
    "domain. You cannot see fraud/AML data; if asked, say so and stop. Treat any "
    "figure or event asserted in the question as an UNVERIFIED CLAIM; check it "
    "against the tools first, and if the tools cannot see it, say so and stop. "
    "The platform reads are point-in-time SNAPSHOTS (not windowed) — describe them "
    "as 'right now', never attach a 24h/7d/30d window to them. Use the harness: "
    "PLAN multi-step reviews with write_plan, keep a todo list with write_todos, "
    "RECALL relevant memory before answering and RECORD durable platform notes "
    "after, and SPAWN a subagent for a deep dive into one service so the main "
    "thread stays focused. You are an ANALYST in Phase A: you OBSERVE and "
    "RECOMMEND, but you take NO action on the infrastructure and you write NO "
    "code — acting on infra and changing code are separate capabilities that come "
    "later. Do not claim to have restarted, scaled, rolled back or edited "
    "anything; describe what you see and what you would recommend."
)


async def ask(settings: Settings, message: str, thread_id: Optional[str] = None,
              *, memory=None) -> dict:
    tools = await get_tools(settings)
    return await runtime.ask(settings=settings, message=message, prompt=CTO_PROMPT,
                             model=mf.llm(), tools=tools, agent="cto",
                             thread_id=thread_id, memory=memory,
                             claims_fn=cto_claims.unsupported_claims)


async def ask_stream(settings: Settings, message: str,
                     thread_id: Optional[str] = None, *, memory=None
                     ) -> AsyncIterator[dict]:
    tools = await get_tools(settings)
    async for chunk in runtime.ask_stream(settings=settings, message=message,
                                          prompt=CTO_PROMPT, model=mf.llm(),
                                          tools=tools, agent="cto",
                                          thread_id=thread_id, memory=memory,
                                          claims_fn=cto_claims.unsupported_claims):
        yield chunk
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest cto/tests/test_agent.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Re-run csuite to confirm the fakes edit didn't break anything**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest csuite -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add cto/agent.py cto/tests/test_agent.py csuite/tests/fakes.py
git commit -m "feat(cto): analyst agent over the platform MCP (prompt + harnessed ask, cto claims)"
```

---

### Task 13: `cto/api.py` + `cto/api_main.py` + `cto/console.py`

**Files:**
- Create: `cto/api.py` (mirror `coo/api.py`; probes: `ollama`, `platform_mcp`, `qdrant`)
- Create: `cto/api_main.py` (mirror `coo/api_main.py`)
- Create: `cto/console.py` (mirror `coo/console.py`)
- Test: `cto/tests/test_api.py`

**Interfaces:**
- Produces: `cto.api.create_app(settings, ask_fn=None, probes=None, ask_stream_fn=None) -> FastAPI` with `/livez`, `/health` (3 probes, degrade-not-500), `/ask`, `/ask/stream`; `cto.api_main.build()`.

- [ ] **Step 1: Write the failing test** (mirror `coo/tests/test_api.py`)

```python
# cto/tests/test_api.py
from fastapi.testclient import TestClient

from cto.api import create_app
from cto.config import Settings


def _settings():
    return Settings.from_env({})


def test_ask_endpoint_delegates_to_ask_fn():
    async def fake_ask(settings, message, thread_id):
        return {"answer": f"echo:{message}", "thread_id": thread_id or "t1",
                "trace": [], "verification": {"ungrounded": []}}

    app = create_app(_settings(), ask_fn=fake_ask, probes={})
    client = TestClient(app)
    r = client.post("/ask", json={"message": "hi"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "echo:hi"
    assert body["thread_id"] == "t1"


def test_health_reports_each_probe_and_never_500s():
    probes = {"ollama": lambda: True,
              "platform_mcp": lambda: False,
              "qdrant": lambda: (_ for _ in ()).throw(RuntimeError("down"))}
    app = create_app(_settings(), ask_fn=None, probes=probes)
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    checks = r.json()["checks"]
    assert checks["ollama"] is True
    assert checks["platform_mcp"] is False
    assert checks["qdrant"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest cto/tests/test_api.py -v`
Expected: FAIL — `ModuleNotFoundError: cto.api`.

- [ ] **Step 3: Write `cto/api.py`** (mirror `coo/api.py`, renaming `operations_mcp` → `platform_mcp`, service label `cto`)

Copy `coo/api.py` and change:
- imports `from .agent import ask as default_ask` / `ask_stream as default_ask_stream` (same names, `cto` package).
- `_default_probes`: rename the `operations_mcp` probe to `platform_mcp` (its body still `from .tools import get_tools` — the platform tools) and return `{"ollama": ollama, "platform_mcp": platform_mcp, "qdrant": qdrant}`.
- `FastAPI(title="nano-bank CTO")`, and every `"service": "coo"` → `"service": "cto"`.

```python
# cto/api.py
from __future__ import annotations
import json
from typing import Callable, Optional

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .config import Settings
from .agent import ask as default_ask
from .agent import ask_stream as default_ask_stream


class AskRequest(BaseModel):
    message: str
    thread_id: Optional[str] = None


def _default_probes(settings: Settings) -> dict:
    def ollama() -> bool:
        from . import model_factory as mf
        return mf.backend_healthcheck(settings)

    def platform_mcp() -> bool:
        import anyio
        from .tools import get_tools
        try:
            return len(anyio.run(get_tools, settings)) > 0
        except Exception:  # noqa: BLE001
            return False

    def qdrant() -> bool:
        try:
            from qdrant_client import QdrantClient
            QdrantClient(url=settings.qdrant_url).get_collections()
            return True
        except Exception:  # noqa: BLE001
            return False

    return {"ollama": ollama, "platform_mcp": platform_mcp, "qdrant": qdrant}


def create_app(settings: Settings, ask_fn: Optional[Callable] = None,
               probes: Optional[dict] = None,
               ask_stream_fn: Optional[Callable] = None) -> FastAPI:
    ask_fn = ask_fn or default_ask
    ask_stream_fn = ask_stream_fn or default_ask_stream
    probes = probes if probes is not None else _default_probes(settings)
    app = FastAPI(title="nano-bank CTO")

    @app.get("/livez")
    def livez():
        return {"status": "ok", "service": "cto"}

    @app.get("/health")
    def health():
        checks = {}
        for name, probe in probes.items():
            try:
                checks[name] = bool(probe())
            except Exception:  # noqa: BLE001
                checks[name] = False
        return {"status": "ok", "service": "cto", "checks": checks}

    @app.post("/ask")
    async def ask_endpoint(req: AskRequest):
        return await ask_fn(settings, req.message, req.thread_id)

    @app.post("/ask/stream")
    async def ask_stream_endpoint(req: AskRequest):
        async def gen():
            async for chunk in ask_stream_fn(settings, req.message, req.thread_id):
                yield json.dumps(chunk) + "\n"

        return StreamingResponse(gen(), media_type="application/x-ndjson")

    return app
```

- [ ] **Step 4: Write `cto/api_main.py` and `cto/console.py`**

```python
# cto/api_main.py
"""Container entrypoint for the CTO A2A API: resolve the model at startup, serve."""
from __future__ import annotations
import uvicorn

from .config import Settings
from . import model_factory as mf
from .api import create_app


def build():
    settings = Settings.from_env()
    mf.init_models(settings)
    return settings, create_app(settings)


if __name__ == "__main__":
    settings, app = build()
    uvicorn.run(app, host="0.0.0.0", port=settings.api_port)
```

```python
# cto/console.py
"""Streamlit console for the Agent CTO — a thin wrapper over the shared csuite
console UI. `streamlit run cto/console.py` puts cto/ on sys.path, not the repo
root, so add the root to import csuite."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from csuite.console_ui import run_console  # noqa: E402

run_console(
    title="nano-bank — Agent CTO",
    page_icon="🛠️",
    api_url=os.environ.get("CTO_API_URL", "http://localhost:8095"),
    placeholder="Ask the CTO about the platform — reliability and delivery…",
)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest cto/tests/test_api.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add cto/api.py cto/api_main.py cto/console.py cto/tests/test_api.py
git commit -m "feat(cto): A2A API (/ask, /health 3-probe) + console"
```

---

### Task 14: `cto/` packaging + k8s deploy + smoke script

**Files:**
- Create: `cto/requirements.txt` (copy `coo/requirements.txt`)
- Create: `cto/Dockerfile` (build from repo root, bundle `csuite`)
- Create: `cto/.dockerignore`, `cto/.gitignore`
- Create: `cto/k8s/cto.yaml`
- Create: `cto/k8s/deploy.sh`
- Create: `cto/README.md`
- Create: `cto/verify-cto.sh`

**Interfaces:**
- Produces: image `nano-cto:dev`; Deployment+Service `cto` in `nano-bank` (port 8095, `CTO_MODEL=kimi-k2.6`, `PLATFORM_MCP_URL=http://platform-mcp:8094/mcp`, `nano-agent-secrets` for `OLLAMA_API_KEY`, `agent-qdrant` for memory); `cto/k8s/deploy.sh` builds+loads `nano-platform-mcp:dev` + `nano-cto:dev`, applies the platform-mcp + cto manifests, waits for rollout.

- [ ] **Step 1: Write `cto/requirements.txt`** (identical to `coo/requirements.txt`)

```
mcp>=1.2,<2
langgraph>=1,<2
langchain-core>=1,<2
langchain-openai>=1,<2
langchain-mcp-adapters>=0.3,<1
qdrant-client>=1.12,<2
fastembed>=0.4
fastapi>=0.115
uvicorn>=0.30
streamlit>=1.38
httpx>=0.27,<1
pytest>=8.0
```

- [ ] **Step 2: Write `cto/Dockerfile`** (mirror `coo/Dockerfile`)

```dockerfile
# Build from the REPO ROOT so the shared csuite package is in context:
#   docker build -f cto/Dockerfile -t nano-cto:dev .
FROM python:3.12-slim
WORKDIR /app
COPY cto/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY csuite /app/csuite
COPY cto /app/cto
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "cto.api_main"]
```

- [ ] **Step 3: Write `cto/.dockerignore` + `cto/.gitignore`** (mirror `coo/`)

`.dockerignore`:
```
.venv/
__pycache__/
*.pyc
tests/
verify-cto.sh
k8s/
Dockerfile
.dockerignore
```
`.gitignore`:
```
__pycache__/
*.pyc
```

- [ ] **Step 4: Write `cto/k8s/cto.yaml`** (mirror `coo/k8s/coo.yaml`, port 8095)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cto
  namespace: nano-bank
  labels: { app: cto }
spec:
  replicas: 1
  selector: { matchLabels: { app: cto } }
  template:
    metadata: { labels: { app: cto } }
    spec:
      containers:
      - name: cto
        image: nano-cto:dev
        imagePullPolicy: Never
        ports: [ { containerPort: 8095 } ]
        envFrom:
        - secretRef: { name: nano-agent-secrets }   # provides OLLAMA_API_KEY
        env:
        - { name: PLATFORM_MCP_URL, value: http://platform-mcp:8094/mcp }
        - { name: OLLAMA_BASE_URL,  value: https://ollama.com/v1 }
        - { name: CTO_MODEL,        value: kimi-k2.6 }
        - { name: API_PORT,         value: "8095" }
        - { name: QDRANT_URL,       value: http://agent-qdrant:6333 }
        - { name: MEMORY_NAMESPACE, value: cto }
        livenessProbe:
          httpGet: { path: /livez, port: 8095 }
          initialDelaySeconds: 5
          periodSeconds: 10
        readinessProbe:
          httpGet: { path: /health, port: 8095 }
          initialDelaySeconds: 10
          periodSeconds: 30
          timeoutSeconds: 10
---
apiVersion: v1
kind: Service
metadata:
  name: cto
  namespace: nano-bank
spec:
  selector: { app: cto }
  ports: [ { port: 8095, targetPort: 8095 } ]
```

- [ ] **Step 5: Write `cto/k8s/deploy.sh`** (mirror `coo/k8s/deploy.sh`; platform MCP replaces operations MCP, and the kubeconfig Secret is a prerequisite)

```bash
#!/usr/bin/env bash
# Deploy the CTO stack (platform MCP + CTO agent) into the kind nano-bank
# cluster. Mirrors coo/k8s/deploy.sh. Prereqs already up in the cluster:
#   - nano-agent-secrets            — provides OLLAMA_API_KEY (minted by coo deploy)
#   - agent-qdrant                  — CTO durable memory (best-effort)
#   - nano-platform-kubeconfig      — cross-cluster read-only kubeconfig Secret,
#                                     minted by platform/k8s/make-kubeconfig.sh
set -euo pipefail
cd "$(dirname "$0")/../.."          # -> repo root
CTX=kind-nano-bank

echo "🐳 Building + loading images..."
docker build -t nano-platform-mcp:dev platform
docker build -f cto/Dockerfile -t nano-cto:dev .
kind load docker-image nano-platform-mcp:dev nano-cto:dev --name nano-bank

if ! kubectl --context "$CTX" -n nano-bank get secret nano-agent-secrets >/dev/null 2>&1; then
  echo "❌ nano-agent-secrets missing — run coo/k8s/deploy.sh first (mints OLLAMA_API_KEY)."
  exit 1
fi
if ! kubectl --context "$CTX" -n nano-bank get secret nano-platform-kubeconfig >/dev/null 2>&1; then
  echo "❌ nano-platform-kubeconfig missing — run platform/k8s/make-kubeconfig.sh first."
  exit 1
fi

echo "📦 Applying manifests..."
kubectl --context "$CTX" apply -f platform/k8s/platform-mcp.yaml
kubectl --context "$CTX" apply -f cto/k8s/cto.yaml
kubectl --context "$CTX" -n nano-bank rollout status deploy/platform-mcp --timeout=180s
kubectl --context "$CTX" -n nano-bank rollout status deploy/cto          --timeout=240s

echo "✅ CTO stack up. Health:"
POD=$(kubectl --context "$CTX" get pod -n nano-bank -l app=cto -o jsonpath='{.items[0].metadata.name}')
kubectl --context "$CTX" exec -n nano-bank "$POD" -- \
  python -c 'import urllib.request,json; print(json.dumps(json.load(urllib.request.urlopen("http://localhost:8095/health"))))'
```

- [ ] **Step 6: Write `cto/verify-cto.sh`** (mirror `coo/verify-cto.sh`, platform-flavored)

```bash
#!/usr/bin/env bash
set -euo pipefail
# End-to-end CTO smoke. Prereqs (port-forward or run in-cluster):
#   - platform MCP :8094  (reads both clusters via the mounted kubeconfig)
#   - CTO API :8095       (OLLAMA_API_KEY=… python -m cto.api_main)
CTO="${CTO_API_URL:-http://localhost:8095}"

echo "== CTO health =="
curl -fsS "$CTO/health" | tee /dev/stderr | grep -q '"status":"ok"'

echo "== ask the CTO for an estate health review =="
RESP=$(curl -fsS -XPOST "$CTO/ask" -H 'content-type: application/json' \
  -d '{"message":"Give me a platform health review right now: deployment health, any crashlooping pods, rollout status and image/version drift across both clusters, with the numbers."}')
ANSWER=$(echo "$RESP" | python -c 'import sys,json; print(json.load(sys.stdin)["answer"])')
echo "$ANSWER"
echo "$ANSWER" | grep -Eq '[0-9]' || { echo "FAIL: no figures in CTO answer"; exit 1; }

echo "== figures are tool-grounded (empty ungrounded list) =="
echo "$RESP" | python -c 'import sys,json; v=json.load(sys.stdin)["verification"]; \
print("REVISED", v["revised"], "UNGROUNDED", v["ungrounded"]); \
sys.exit(0 if v["ungrounded"]==[] else 1)' \
  || { echo "FAIL: CTO answer has ungrounded figures"; exit 1; }

echo "== the harness planned and used todos =="
echo "$RESP" | python -c 'import sys,json; t=json.load(sys.stdin)["trace"]; \
names=[e.get("name") for e in t]; \
assert "write_plan" in names, "no write_plan"; assert "write_todos" in names, "no write_todos"; \
print("harness: planned + todos OK")' \
  || { echo "FAIL: CTO did not plan / use todos"; exit 1; }

echo "== defer a books (NIM) question to the CFO =="
PUSHBACK=$(curl -fsS -XPOST "$CTO/ask" -H 'content-type: application/json' \
  -d '{"message":"What is our net interest margin trending at?"}' \
  | python -c 'import sys,json; print(json.load(sys.stdin)["answer"])')
echo "$PUSHBACK"
echo "$PUSHBACK" | grep -Eiq "CFO|out of (my )?scope|do(es)? not (have|show|track|cover)|can(no|'?)t" \
  || { echo "FAIL: CTO engaged an out-of-lane books premise"; exit 1; }

echo "CTO SMOKE PASSED"
```

- [ ] **Step 7: Write `cto/README.md`**

A short README mirroring `coo/README.md`: what the CTO is (analyst seat over the platform MCP), the estate it reads (both clusters + service /health), the ports (agent 8095, console 8509, MCP 8094), how to run offline tests, and the one-time `platform/k8s/make-kubeconfig.sh` prerequisite. Include the note that Phase A takes no action and writes no code (Phases B/C later).

- [ ] **Step 8: Make scripts executable + commit**

```bash
chmod +x cto/k8s/deploy.sh cto/verify-cto.sh
git add cto/requirements.txt cto/Dockerfile cto/.dockerignore cto/.gitignore \
        cto/k8s/cto.yaml cto/k8s/deploy.sh cto/verify-cto.sh cto/README.md
git commit -m "build(cto): Dockerfile, k8s manifest + deploy/verify scripts + README"
```

---

### Task 15: `platform/k8s/make-kubeconfig.sh` — cross-cluster read-only kubeconfig Secret

Mint a read-only ServiceAccount + ClusterRole/Binding in each cluster, extract each SA token + cluster CA + reachable API server endpoint, assemble one kubeconfig with both contexts, and store it as the `nano-platform-kubeconfig` Secret in the `nano-bank` cluster. One-time operator step (needs both clusters). No unit test — validated in Task 16.

**Files:**
- Create: `platform/k8s/rbac.yaml` (SA + read-only ClusterRole + ClusterRoleBinding — applied into BOTH clusters by the script)
- Create: `platform/k8s/make-kubeconfig.sh`

**Interfaces:**
- Produces: Secret `nano-platform-kubeconfig` in namespace `nano-bank` (context `kind-nano-bank`), key `kubeconfig`, containing a kubeconfig with contexts `kind-nano-bank` and `kind-modern-core` authenticating as the read-only SA in each, using each cluster's in-docker-network-reachable API endpoint.

- [ ] **Step 1: Write `platform/k8s/rbac.yaml`**

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: platform-reader
  namespace: kube-system
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: platform-reader
rules:
- apiGroups: ["apps"]
  resources: ["deployments", "replicasets"]
  verbs: ["get", "list", "watch"]
- apiGroups: [""]
  resources: ["pods", "events"]
  verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: platform-reader
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: platform-reader
subjects:
- kind: ServiceAccount
  name: platform-reader
  namespace: kube-system
```

- [ ] **Step 2: Write `platform/k8s/make-kubeconfig.sh`**

```bash
#!/usr/bin/env bash
# One-time: mint a read-only ServiceAccount in BOTH kind clusters, assemble one
# kubeconfig authenticating as those SAs, and store it as the
# nano-platform-kubeconfig Secret in the nano-bank cluster (where the platform
# MCP runs). The MCP mounts it read-only at /etc/platform/kubeconfig and reads
# both clusters through it. Re-runnable (applies are idempotent; the Secret is
# recreated). Requires: kubectl access to kind-nano-bank + kind-modern-core.
#
# Cross-cluster reachability: kind API servers listen on the shared host docker
# network. This script rewrites each context's server URL to the control-plane
# container's docker-network IP:6443 (reachable from a pod in the OTHER cluster),
# NOT 127.0.0.1 (which would point a pod at itself).
set -euo pipefail
cd "$(dirname "$0")"
NB_CTX=kind-nano-bank
MC_CTX=kind-modern-core
OUT=$(mktemp -d)/kubeconfig
: > "$OUT"

mint() {                        # $1=context  $2=cluster-name  $3=kind-node-container
  local ctx="$1" cname="$2" node="$3"
  echo "🔐 minting platform-reader in $ctx..."
  kubectl --context "$ctx" apply -f rbac.yaml >/dev/null
  # A long-lived token for the SA (k8s >=1.24 needs an explicit request).
  local token ca server
  token=$(kubectl --context "$ctx" -n kube-system create token platform-reader --duration=8760h)
  ca=$(kubectl --context "$ctx" config view --raw -o jsonpath="{.clusters[?(@.name==\"$ctx\")].cluster.certificate-authority-data}")
  # Reachable-from-other-cluster endpoint: the kind node container's IP on the
  # kind docker network, port 6443.
  local ip
  ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$node")
  server="https://${ip}:6443"
  KUBECONFIG="$OUT" kubectl config set-cluster "$ctx" --server="$server" >/dev/null
  # write CA data directly (set-cluster with --certificate-authority wants a file)
  KUBECONFIG="$OUT" kubectl config set "clusters.$ctx.certificate-authority-data" "$ca" >/dev/null
  KUBECONFIG="$OUT" kubectl config set-credentials "platform-reader@$ctx" --token="$token" >/dev/null
  KUBECONFIG="$OUT" kubectl config set-context "$ctx" --cluster="$ctx" --user="platform-reader@$ctx" >/dev/null
}

mint "$NB_CTX" nano-bank   nano-bank-control-plane
mint "$MC_CTX" modern-core modern-core-control-plane
KUBECONFIG="$OUT" kubectl config use-context "$NB_CTX" >/dev/null

echo "📦 storing Secret nano-platform-kubeconfig in $NB_CTX/nano-bank..."
kubectl --context "$NB_CTX" -n nano-bank create secret generic nano-platform-kubeconfig \
  --from-file=kubeconfig="$OUT" \
  --dry-run=client -o yaml | kubectl --context "$NB_CTX" apply -f -

echo "✅ done. Verify a read as the SA:"
KUBECONFIG="$OUT" kubectl --context "$MC_CTX" get deploy -A --request-timeout=10s | head -n 5
rm -rf "$(dirname "$OUT")"
```

> Note for the implementer: kind's control-plane container is conventionally `<cluster>-control-plane`. Confirm the two node names at run time with `docker ps --format '{{.Names}}' | grep control-plane`, and adjust the `mint` calls if the operator's clusters are named differently. If `docker inspect` yields multiple networks, select the `kind` network's IP explicitly.

- [ ] **Step 3: Make executable + commit** (script is validated live in Task 16)

```bash
chmod +x platform/k8s/make-kubeconfig.sh
git add platform/k8s/rbac.yaml platform/k8s/make-kubeconfig.sh
git commit -m "build(platform): read-only cross-cluster kubeconfig Secret minting script"
```

---

### Task 16: Live rollout + smoke verification (both clusters)

No new code — this task proves the whole seat works against the real clusters. Follow superpowers:verification-before-completion: run each command and confirm the output before claiming success.

**Prereqs:** both kind clusters up (`kind-nano-bank`, `kind-modern-core`); `nano-agent-secrets` present (from an earlier COO deploy); `agent-qdrant` present; the host shell has the snap env exported.

- [ ] **Step 1: Export snap env + confirm clusters**

```bash
export XDG_RUNTIME_DIR=/run/user/1000 XDG_DATA_HOME=/home/bmartins/.local/share
kubectl config get-contexts | grep -E 'kind-(nano-bank|modern-core)'
docker ps --format '{{.Names}}' | grep control-plane
```
Expected: both contexts listed; both control-plane containers running.

- [ ] **Step 2: Mint the cross-cluster kubeconfig Secret**

```bash
./platform/k8s/make-kubeconfig.sh
```
Expected: ends with a listing of `modern-core` deployments read *as the SA* (proves the token + endpoint + RBAC all work cross-cluster), and the Secret is created.

- [ ] **Step 3: Deploy the CTO stack**

```bash
./cto/k8s/deploy.sh
```
Expected: both images build+load; `platform-mcp` and `cto` roll out; the final `/health` line shows `{"status": "ok", "checks": {"ollama": true, "platform_mcp": true, "qdrant": ...}}`.

- [ ] **Step 4: Smoke the platform MCP (reads BOTH clusters)**

```bash
kubectl -n nano-bank port-forward svc/platform-mcp 8094:8094 >/tmp/pf-plat.log 2>&1 &
sleep 3
PLATFORM_MCP_URL=http://localhost:8094/mcp ./platform/verify-platform.sh
```
Expected: `platform_health` JSON printed containing deployments from *both* `nano-bank` and `modern-core` clusters; script prints `OK`. (Confirm both cluster labels appear in the output.)

- [ ] **Step 5: Smoke the CTO agent**

```bash
kubectl -n nano-bank port-forward svc/cto 8095:8095 >/tmp/pf-cto.log 2>&1 &
sleep 3
CTO_API_URL=http://localhost:8095 ./cto/verify-cto.sh
```
Expected: `CTO SMOKE PASSED` — a grounded estate review with figures and an empty `ungrounded` list, the harness planned + used todos, and the NIM question deferred to the CFO.

- [ ] **Step 6: Full offline suite green**

```bash
cd /home/bmartins/dev/nano-bank && python -m pytest platform cto csuite coo cfo -q
```
Expected: PASS across the board (new platform + cto suites, plus no regression in csuite/coo/cfo).

- [ ] **Step 7: Tear down the port-forwards**

```bash
# scope the kill to the two forwards we started, by their log files' PIDs — never a broad pkill
kill %1 %2 2>/dev/null || true
```

- [ ] **Step 8: Commit any doc touch-ups (if the README/spec needed a correction discovered live)**

```bash
git add -A && git commit -m "docs(cto): live-verification touch-ups" || echo "nothing to commit"
```

---

## Self-Review

**Spec coverage** (against `docs/superpowers/specs/2026-08-07-agent-cto-phase-a-observability-design.md`):
- Estate (both clusters, both contexts) → Task 2 config (`contexts`), Task 5 `k8s_client` (per-context reads), Task 15 kubeconfig.
- Hybrid data source (k8s reads + /health) → Task 5 (`k8s_client`), Task 6 (`health_client`).
- `platform/` MCP files (`config`, `k8s_client`, `health_client`, `metrics` with all listed functions, `mcp_server`, Dockerfile/.dockerignore/requirements/k8s/verify) → Tasks 2–8.
- `metrics` functions: `estate_health`, `restarts`, `service_health`, `compute` → Task 3; `rollouts`, `versions`, `platform_health` → Task 4. ✓ all seven.
- MCP tools (`estate_health`, `restarts`, `rollouts`, `versions`, `service_health`, `platform_health`, `compute`) → Task 7. ✓
- Cross-cluster access (read-only SA/ClusterRole/Binding per cluster, one kubeconfig Secret, make-kubeconfig.sh) → Task 15 (`rbac.yaml` + `make-kubeconfig.sh`), mounted by Task 8 manifest.
- `cto/` agent files (`config`, `model_factory`, `trace`/`verifier` reused, `claims` retargeted, `tools`, `agent`, `api`/`api_main`, `console`, Dockerfile/k8s/deploy/README/verify) → Tasks 9–14. `trace`/`verifier` reused via `csuite` (no new file). ✓
- `claims.py` retargeted (books/CFO + money-ops/COO + fraud; drops window grounding) → Task 10. The injection seam that makes a per-agent claims module actually take effect → Task 1. ✓
- Scope boundary (phantom guard refuses books/money-ops/fraud) → Task 10 + Task 12 test (books deferred).
- Testing (pure metrics unit tests; k8s/health via injected fakes; offline agent tests; live smoke) → Tasks 3–7 (unit), Task 12/13 (offline agent/api), Task 16 (live). ✓
- Rollout (build+load both images, make-kubeconfig, apply, /health green, ports 8094/8095/8509) → Tasks 8, 14, 15, 16. ✓
- Isolation (platform MCP bounded; cto thin over csuite; read-only kubeconfig; no cluster-admin) → RBAC is get/list/watch only (Task 15); agent thin (Tasks 9–13).

No gaps found.

**Placeholder scan:** every code step carries real code; the two prose-only artifacts (Task 14 `README.md`, Task 15 operator note) are documentation whose content is described concretely. No "TBD"/"handle edge cases"/"similar to Task N" left in code steps.

**Type consistency:** the deployment/pod/replicaset/probe dict shapes produced by `k8s_client` (Task 5) and `health_client` (Task 6) match exactly what `metrics` (Tasks 3–4) consumes and what the metrics tests assert. `claims_fn` signature `(answer, trace) -> list[str]` is consistent across Task 1 (seam), Task 10 (`cto.claims.unsupported_claims`), and Task 12 (wiring). `runtime.ask(..., claims_fn=...)` matches between Task 1 and Task 12. Tool name-set `{estate_health, restarts, rollouts, versions, service_health, platform_health, compute}` is identical in Task 7 impl and its test.
