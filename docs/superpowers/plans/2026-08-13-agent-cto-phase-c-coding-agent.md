# Agent CTO — Phase C: PR-gated coding agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Agent CTO a `delegate_coding_task` lever that hands a scoped coding task to a new in-cluster coder service, which authors a real fix in a dedicated sandbox repo and opens a PR-gated pull request — audited, human-merged, never auto-merged.

**Architecture:** A new `coder/` package ports `agentic_patterns/…/coding_agent_gemini.py` verbatim in structure with only its model-factory seam swapped Gemini→kimi/ollama (reusing the CTO's conventions). A thin FastAPI service (`coder/api.py`, `:8096`) wraps it with git/`gh` plumbing: clone the sandbox → drive the coder in agentic mode against the repo's own pytest → self-verify gate → branch/commit/push/`gh pr create`. The CTO reaches it through a new `delegate_coding_task` acting-tool in `platform_mcp`, registered exactly like the Phase B levers (verify → act → audit), calling the coder over HTTP and writing one row to the tamper-evident `agent_action_ledger`.

**Tech Stack:** Python 3.12, LangChain/LangGraph + `langchain-openai` (`ChatOpenAI` @ `https://ollama.com/v1`), FastMCP, FastAPI/uvicorn, `httpx`, `git` + `gh` CLIs, pytest, Docker + Kind (`kind-nano-bank`).

## Global Constraints

- **Backend seam only.** The coder ports `coding_agent_gemini.py`; *only* `_build_model`/`llm`/`backend_healthcheck` change (→ kimi/ollama). Everything above the model factory is copied structure-for-structure. No new API keys — reuse the same ollama secret the CTO uses.
- **Model resolution mirrors the CTO:** `ChatOpenAI(base_url=https://ollama.com/v1, api_key=OLLAMA_API_KEY or "ollama")`, primary→fallback `_candidates`, default `kimi-k2.6`.
- **The coder edits ONLY the dedicated sandbox repo** (`bvcmartins/cto-sandbox`). It never touches the nano-bank repo or anything in prod. The coder service is pinned to that one repo — there is no repo argument on the lever, so "other target" is structurally impossible, not merely rejected at runtime.
- **Never merge.** The coder opens a PR; a human reviews and merges. `executed` = *PR opened*, not merged. Tests-red → no PR, ledger `failed`.
- **Every delegation is audited** — one `agent_action_ledger` row via the existing `POST /api/v1/agent-ledger/actions`, `action="delegate_coding_task"`, `outcome ∈ {executed, refused, failed}`.
- **`kind="remediation"` requires an observed failing/degraded platform signal** (the same k8s reads Phase B self-verifies against). `kind="delivery"` needs only a non-empty task.
- **Snap env before any kubectl/docker/kind:** `export XDG_RUNTIME_DIR=/run/user/1000 XDG_DATA_HOME=/home/bmartins/.local/share`.
- **Do NOT open a GitHub PR for this branch** (per the user). Build on `agent-cto-demo`; commit locally; do not `gh pr create` for nano-bank. The live coder smoke (Task 13), which opens a *sandbox* PR, is gated on the user provisioning `cto-sandbox` + a gh token and is not run unattended.
- Ports: platform_mcp `:8094`, cto `:8095`, **coder `:8096`**.
- Tests run offline (fake model + temp git repo + injected transports); no network in CI. The one live check is explicitly separate.

---

## File Structure

**New `coder/` package** (mirrors `cto/` and `platform_mcp/`):
- `coder/__init__.py` — empty package marker.
- `coder/config.py` — `Settings` from env (port, ollama creds, model roles+fallback, sandbox repo, workspace root, timeouts, gh token path).
- `coder/model_factory.py` — the swapped backend seam: `build_model`/`llm`/`init_models`/`resolve_role`/`backend_healthcheck` over kimi/ollama, role-aware (`reasoning`/`fast`), primary→fallback.
- `coder/coding_agent.py` — the **port** of `coding_agent_gemini.py`; model factory calls delegated to `coder.model_factory`; adds `set_workspace()`.
- `coder/git_ops.py` — pure helpers: `branch_slug`, `pr_create_args`, `code_task_result`.
- `coder/service.py` — `run_code_task(kind, task, *, settings, seams=None)`: clone → agentic repo loop (verify against repo pytest) → self-verify gate → branch/commit/push/`gh pr create` → shaped result. IO seams injectable.
- `coder/api.py` — FastAPI `POST /code-task`, `/livez`, `/health` (mirrors `cto/api.py`).
- `coder/api_main.py` — uvicorn entry.
- `coder/Dockerfile`, `coder/requirements.txt`, `coder/README.md`.
- `coder/k8s/coder.yaml`, `coder/k8s/deploy.sh`.
- `coder/sandbox-seed/` — baseline content for `cto-sandbox` + `provision-sandbox.sh`.
- `coder/tests/` — `test_config.py`, `test_model_factory.py`, `test_coding_agent_selftest.py`, `test_git_ops.py`, `test_service.py`, `test_api.py`.

**`platform_mcp/` additions:**
- `platform_mcp/coder_client.py` — HTTP client → coder `POST /code-task`.
- `platform_mcp/levers.py` — add `remediation_signal_present(...)`.
- `platform_mcp/config.py` — add `coder_url`, `coder_timeout`, `coder_sandbox_repo`.
- `platform_mcp/mcp_server.py` — add `_do_delegate(...)` + register `delegate_coding_task`; `build_mcp(..., coder=None)`; `main()` builds a `CoderClient`.
- `platform_mcp/tests/` — `test_coder_client.py`, `test_delegate.py` (+ extend nothing else).

**csuite / present / cto / demo:**
- `csuite/trace_view.py` — `beat_outcome`: add `delegate_coding_task` lever + `delegated`/`failed` kinds.
- `demos/08-cto/present/state.py` — add `delegated`, `failed` to `_STYLES`.
- `cto/agent.py` — prompt: add the delegation lever; soften "writes NO code".
- `demos/08-cto/drive.py` — beats 8 (remediation) + 9 (delivery).
- `demos/08-cto/questions.md` — the two new questions.
- `demos/08-cto/reseed-sandbox.sh` — host-side reseed.
- `demos/08-cto/run-demo.sh` — reseed step + baseline restore in the EXIT trap.

---

## Task 1: coder config + model factory (the swapped seam)

**Files:**
- Create: `coder/__init__.py`, `coder/config.py`, `coder/model_factory.py`
- Test: `coder/tests/__init__.py`, `coder/tests/test_config.py`, `coder/tests/test_model_factory.py`

**Interfaces:**
- Produces:
  - `Settings` (dataclass) fields: `ollama_api_key: str`, `ollama_base_url: str`, `models: dict[str,str]` (keys `reasoning`,`fast`), `model_fallback: str`, `sandbox_repo: str`, `sandbox_clone_url: str`, `workspace_root: str`, `api_port: int`, `request_timeout: float`, `test_timeout: int`, `gh_token: str`, `pr_base: str`; classmethod `Settings.from_env(env=None)`.
  - `model_factory.build_model(model, settings, *, temperature=0.2, max_tokens=None) -> ChatOpenAI`
  - `model_factory.resolve_role(settings, role, probe=None) -> str`
  - `model_factory.init_models(settings, probe=None) -> dict[str,str]`
  - `model_factory.llm(role="fast", *, reasoning=True, temperature=0.2, max_tokens=None) -> ChatOpenAI`
  - `model_factory.backend_healthcheck(settings) -> bool`

- [ ] **Step 1: Write the failing tests**

```python
# coder/tests/__init__.py  -> empty file
```
```python
# coder/tests/test_config.py
from coder.config import Settings

def test_defaults():
    s = Settings.from_env({})
    assert s.models == {"reasoning": "kimi-k2.6", "fast": "kimi-k2.6"}
    assert s.model_fallback == "kimi-k2.6"
    assert s.ollama_base_url == "https://ollama.com/v1"
    assert s.sandbox_repo == "bvcmartins/cto-sandbox"
    assert s.api_port == 8096

def test_env_overrides():
    s = Settings.from_env({
        "CODER_REASONING_MODEL": "kimi-k3", "CODER_FAST_MODEL": "kimi-k2.6",
        "CODER_MODEL_FALLBACK": "kimi-k2.6", "OLLAMA_API_KEY": "sk",
        "SANDBOX_REPO": "me/repo", "API_PORT": "9000"})
    assert s.models["reasoning"] == "kimi-k3"
    assert s.ollama_api_key == "sk"
    assert s.sandbox_repo == "me/repo"
    assert s.api_port == 9000
```
```python
# coder/tests/test_model_factory.py
from coder.config import Settings
from coder import model_factory as mf

def test_resolve_role_prefers_primary():
    s = Settings.from_env({"CODER_REASONING_MODEL": "kimi-k3",
                           "CODER_MODEL_FALLBACK": "kimi-k2.6"})
    seen = []
    def probe(model, settings):
        seen.append(model); return True
    assert mf.resolve_role(s, "reasoning", probe) == "kimi-k3"
    assert seen == ["kimi-k3"]

def test_resolve_role_falls_back_when_primary_down():
    s = Settings.from_env({"CODER_REASONING_MODEL": "kimi-k3",
                           "CODER_MODEL_FALLBACK": "kimi-k2.6"})
    def probe(model, settings):
        return model == "kimi-k2.6"
    assert mf.resolve_role(s, "reasoning", probe) == "kimi-k2.6"

def test_resolve_role_raises_when_none_answer():
    s = Settings.from_env({})
    import pytest
    with pytest.raises(RuntimeError):
        mf.resolve_role(s, "fast", lambda m, st: False)

def test_init_models_resolves_both_roles():
    s = Settings.from_env({})
    resolved = mf.init_models(s, probe=lambda m, st: True)
    assert set(resolved) == {"reasoning", "fast"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest coder/tests/test_config.py coder/tests/test_model_factory.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'coder'`.

- [ ] **Step 3: Create `coder/__init__.py` and `coder/config.py`**

```python
# coder/__init__.py  -> empty file
```
```python
# coder/config.py
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass
class Settings:
    ollama_api_key: str
    ollama_base_url: str
    models: dict          # {"reasoning": <id>, "fast": <id>}
    model_fallback: str
    sandbox_repo: str      # "owner/name"
    sandbox_clone_url: str # https clone URL (token injected at runtime)
    workspace_root: str
    api_port: int
    request_timeout: float
    test_timeout: int
    gh_token: str
    pr_base: str

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        e = os.environ if env is None else env

        def g(k, d=""):
            return e.get(k, d)

        default_model = g("CODER_MODEL", "kimi-k2.6")
        repo = g("SANDBOX_REPO", "bvcmartins/cto-sandbox")
        gh_token = g("GH_TOKEN") or _read_file(g("GH_TOKEN_PATH", ""))
        return cls(
            ollama_api_key=g("OLLAMA_API_KEY"),
            ollama_base_url=g("OLLAMA_BASE_URL", "https://ollama.com/v1"),
            models={
                "reasoning": g("CODER_REASONING_MODEL", default_model),
                "fast": g("CODER_FAST_MODEL", default_model),
            },
            model_fallback=g("CODER_MODEL_FALLBACK", default_model),
            sandbox_repo=repo,
            sandbox_clone_url=g("SANDBOX_CLONE_URL", f"https://github.com/{repo}.git"),
            workspace_root=g("CODER_WORKSPACE_ROOT", "/tmp/coder-workspaces"),
            api_port=int(g("API_PORT", "8096")),
            request_timeout=float(g("REQUEST_TIMEOUT", "600")),
            test_timeout=int(g("TEST_TIMEOUT", "180")),
            gh_token=gh_token,
            pr_base=g("PR_BASE", "main"),
        )


def _read_file(path: str) -> str:
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""
```

- [ ] **Step 4: Create `coder/model_factory.py`**

```python
# coder/model_factory.py
"""The ONE backend-specific seam of the coder. Ports cto/model_factory.py's
kimi/ollama conventions (ChatOpenAI @ ollama.com/v1, primary->fallback probing)
and adds a role split (reasoning|fast) so the ported coding_agent's llm(role, ...)
calls read identically. `reasoning` is accepted for call-site compatibility with
the Gemini port but is a no-op here (kimi has no thinking-budget knob over the
OpenAI-compatible endpoint)."""
from __future__ import annotations
import logging
from functools import lru_cache
from typing import Callable, Optional

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from .config import Settings

log = logging.getLogger("coder.llm")

_RESOLVED: dict = {}
_SETTINGS: Optional[Settings] = None


def build_model(model: str, settings: Settings, *, temperature: float = 0.2,
                max_tokens: Optional[int] = None) -> ChatOpenAI:
    kw = dict(model=model, temperature=temperature,
              base_url=settings.ollama_base_url,
              api_key=settings.ollama_api_key or "ollama",
              timeout=settings.request_timeout)
    if max_tokens:
        kw["max_tokens"] = max_tokens
    return ChatOpenAI(**kw)


def _default_probe(model: str, settings: Settings) -> bool:
    try:
        m = build_model(model, settings, temperature=0.0, max_tokens=8)
        m.invoke([HumanMessage("reply with the single word: ok")])
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("probe failed for %s: %s", model, e)
        return False


def _candidates(settings: Settings, role: str) -> list[str]:
    primary = settings.models.get(role) or settings.models["fast"]
    out: list[str] = []
    for m in (primary, settings.model_fallback):
        if m and m not in out:
            out.append(m)
    return out


def resolve_role(settings: Settings, role: str,
                 probe: Optional[Callable[[str, Settings], bool]] = None) -> str:
    probe = probe or _default_probe
    cands = _candidates(settings, role)
    for i, model in enumerate(cands):
        if probe(model, settings):
            if i > 0:
                log.warning("primary %s model unavailable; fell back to %s", role, model)
            return model
    raise RuntimeError(
        f"no {role} model answered at {settings.ollama_base_url}: "
        f"tried {', '.join(cands)}")


def init_models(settings: Settings,
                probe: Optional[Callable[[str, Settings], bool]] = None) -> dict:
    global _RESOLVED, _SETTINGS
    _SETTINGS = settings
    _RESOLVED = {role: resolve_role(settings, role, probe)
                 for role in ("reasoning", "fast")}
    return _RESOLVED


@lru_cache(maxsize=32)
def _client(model: str, temperature: float, max_tokens: Optional[int]) -> ChatOpenAI:
    return build_model(model, _SETTINGS, temperature=temperature, max_tokens=max_tokens)


def llm(role: str = "fast", *, reasoning: Optional[bool] = True,
        temperature: float = 0.2, max_tokens: Optional[int] = None) -> ChatOpenAI:
    if not _RESOLVED or _SETTINGS is None:
        raise RuntimeError("call init_models(settings) before llm()")
    model = _RESOLVED.get(role) or _RESOLVED["fast"]
    return _client(model, temperature, max_tokens)


def backend_healthcheck(settings: Settings) -> bool:
    try:
        return any(_default_probe(m, settings) for m in _candidates(settings, "fast"))
    except Exception:  # noqa: BLE001
        return False
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest coder/tests/test_config.py coder/tests/test_model_factory.py -q`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add coder/__init__.py coder/config.py coder/model_factory.py coder/tests/__init__.py coder/tests/test_config.py coder/tests/test_model_factory.py
git commit -m "feat(coder): config + kimi/ollama model factory (role-aware, primary->fallback)"
```

---

## Task 2: port `coding_agent_gemini.py` → `coder/coding_agent.py`

Copy the reference module verbatim, then make three surgical edits: (a) drop its Gemini/Vertex backend block and re-point the model factory at `coder.model_factory`; (b) drop the now-unused Gemini `content_text`/`thinking_of` Gemini-specifics only where they reference Gemini SDK types (keep the generic flatteners); (c) add a `set_workspace()` helper so the service can re-point `WORKSPACE` at a cloned checkout. The gate is the ported offline `_selftest()`, run as a pytest — it exercises every backend-free path with **no** model calls.

**Files:**
- Create: `coder/coding_agent.py`
- Reference (read-only): `/home/bmartins/dev/agentic_patterns/src/code_assistant/coding_agent_gemini.py`
- Test: `coder/tests/test_coding_agent_selftest.py`

**Interfaces:**
- Consumes: `coder.model_factory.llm`, `.init_models`, `.backend_healthcheck` (Task 1).
- Produces (names later tasks rely on): `CodingAgent` (class, `mode="direct"|"agentic"`, `.generate`, `.fix`, `.add_instruction`), `CodeResult` (dataclass), `build_agent_graph(tools, system, role="fast", reasoning=True, checkpointer=None)`, `TOOLS_BASE` (list of `@tool`s), `content_text(msg) -> str`, `strip_code_fences(text) -> str`, `lint_python`, `write_code_to_disk`, `compile_test_suite`, `spec_verify`, `CODER_SYSTEM_PROMPT`, `set_workspace(path) -> None`, module globals `WORKSPACE`/`AGENT_CODE_DIR`, and `_selftest() -> bool`.

- [ ] **Step 1: Copy the reference file into place**

```bash
cp /home/bmartins/dev/agentic_patterns/src/code_assistant/coding_agent_gemini.py \
   /home/bmartins/dev/nano-bank/coder/coding_agent.py
```

- [ ] **Step 2: Replace the Phase 0 backend block (the one seam)**

In `coder/coding_agent.py`, delete the whole `# --- Backend / Gemini config ---` through the end of `backend_healthcheck()` (in the reference: the `LLM_BACKEND`/`GCP_*`/`DEFAULT_MODEL`/`MODELS` constants, `_build_model`, `_client`, `llm`, and `backend_healthcheck` — roughly lines 121–228), **keeping** the `# --- Sandbox workspace ---` and `# --- Limits ---` blocks. Replace the deleted block with:

```python
# --- Backend: kimi/ollama via the coder model factory (THE one seam) ---------
from .config import Settings                       # noqa: E402
from . import model_factory as _mf                 # noqa: E402

SETTINGS = Settings.from_env()

# Re-export the factory's role-aware llm/healthcheck so the ported graphs below
# read exactly as in the Gemini original (llm("reasoning", reasoning=True), etc.).
llm = _mf.llm


def init_backend(settings: Optional[Settings] = None, probe=None) -> dict:
    """Resolve the kimi models once (network). Call before generate/fix."""
    return _mf.init_models(settings or SETTINGS, probe)


def backend_healthcheck() -> bool:
    return _mf.backend_healthcheck(SETTINGS)
```

Then, because `content_text` in the reference drops "Gemini 'thought' blocks" but is otherwise a generic content flattener, **keep `content_text`, `thinking_of`, `split_think` unchanged** — they are dict/list-shape based and work for any provider (ollama returns plain strings, so they pass through the `isinstance(c, str)` fast path).

- [ ] **Step 3: Make `WORKSPACE` re-pointable — add `set_workspace()`**

Immediately after the `# --- Sandbox workspace ---` block (where `WORKSPACE`, `AGENT_CODE_DIR`, `DEFAULT_POLICY_PATH` are defined), add:

```python
def set_workspace(path) -> None:
    """Re-point the module's WORKSPACE at an existing checkout (the coder service
    clones the sandbox, then calls this so the tools/gates operate on the repo).
    Rebinds the globals the tool/gate functions close over by name."""
    global WORKSPACE, AGENT_CODE_DIR, DEFAULT_POLICY_PATH
    WORKSPACE = Path(path).resolve()
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    AGENT_CODE_DIR = WORKSPACE / "agent_code"
    AGENT_CODE_DIR.mkdir(exist_ok=True)
    DEFAULT_POLICY_PATH = WORKSPACE / "learned_policy.json"
```

> Note: `_safe_path`, `_run_tests`, `write_code_to_disk` read `WORKSPACE`/`AGENT_CODE_DIR` at call time (module-global lookups), so rebinding here is sufficient — no other edits.

- [ ] **Step 4: Fix the module header docstring + the CLI health path**

Replace the top-of-file docstring's Gemini "RUNNING IT" section with a one-paragraph note that this is the kimi/ollama port (keep it short — it's documentation, not code). In `main()`, change the `--health` branch to initialise the backend first:

```python
    if args.health:
        try:
            init_backend()
        except Exception as e:  # noqa: BLE001
            print(f"init failed: {e}"); sys.exit(1)
        sys.exit(0 if backend_healthcheck() else 1)
```

Leave `--selftest`, `--task`, `--demo-loop` as-is except that `--task`/`--demo-loop` must call `init_backend()` before constructing/using `CodingAgent` (add `init_backend()` as the first line of each of those two branches).

- [ ] **Step 5: Write the selftest gate test**

```python
# coder/tests/test_coding_agent_selftest.py
def test_offline_selftest_all_green(tmp_path, monkeypatch):
    # Point the workspace at a temp dir so the selftest's disk writes are isolated.
    from coder import coding_agent as ca
    ca.set_workspace(tmp_path)
    assert ca._selftest() is True

def test_public_surface_present():
    from coder import coding_agent as ca
    for name in ("CodingAgent", "CodeResult", "build_agent_graph", "TOOLS_BASE",
                 "lint_python", "write_code_to_disk", "compile_test_suite",
                 "spec_verify", "content_text", "strip_code_fences",
                 "set_workspace", "CODER_SYSTEM_PROMPT"):
        assert hasattr(ca, name), name
```

- [ ] **Step 6: Run the test**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest coder/tests/test_coding_agent_selftest.py -q`
Expected: PASS. The selftest prints its own `PASS`/`FAIL` lines and returns True (lint gate, fence/think stripping, write-code gate, `spec_verify` green+red, self-improvement round-trip, graph compilation — all offline).

If import fails on `langgraph`/`langchain` not installed in the test env, install the coder deps first: `pip install -r coder/requirements.txt` (Task 6 creates it; for now `pip install langgraph langchain-core langchain-openai pydantic rich pytest`).

- [ ] **Step 7: Commit**

```bash
git add coder/coding_agent.py coder/tests/test_coding_agent_selftest.py
git commit -m "feat(coder): port coding_agent_gemini.py, model-factory seam swapped to kimi/ollama"
```

---

## Task 3: coder git/PR pure helpers

**Files:**
- Create: `coder/git_ops.py`
- Test: `coder/tests/test_git_ops.py`

**Interfaces:**
- Produces:
  - `branch_slug(task: str, ts: str) -> str` — returns `cto/<slug>-<ts>` (slug: lowercased, non-alnum→`-`, collapsed, trimmed, ≤40 chars).
  - `pr_create_args(*, head: str, base: str, title: str, body: str) -> list[str]` — argv **after** `gh` (starts with `"pr"`, `"create"`).
  - `code_task_result(outcome: str, *, pr_url=None, branch=None, tests=None, summary="", reason="") -> dict` — the JSON body the service returns and the lever audits.

- [ ] **Step 1: Write the failing tests**

```python
# coder/tests/test_git_ops.py
from coder.git_ops import branch_slug, pr_create_args, code_task_result

def test_branch_slug_shape():
    b = branch_slug("Fix the rounding bug in split_amount()", "20260813T120000Z")
    assert b.startswith("cto/")
    assert b.endswith("-20260813T120000Z")
    assert " " not in b and "(" not in b
    assert b == "cto/fix-the-rounding-bug-in-split-amount-20260813T120000Z"

def test_branch_slug_truncates_long_task():
    b = branch_slug("x" * 200, "T")
    slug = b[len("cto/"):-len("-T")]
    assert len(slug) <= 40

def test_pr_create_args_order():
    args = pr_create_args(head="cto/x-T", base="main", title="Fix X", body="because")
    assert args[:2] == ["pr", "create"]
    assert "--head" in args and args[args.index("--head") + 1] == "cto/x-T"
    assert "--base" in args and args[args.index("--base") + 1] == "main"
    assert "--title" in args and "--body" in args

def test_code_task_result_executed():
    r = code_task_result("executed", pr_url="https://x/pr/1", branch="cto/x-T",
                         tests="3 passed", summary="fixed rounding")
    assert r == {"outcome": "executed", "pr_url": "https://x/pr/1",
                 "branch": "cto/x-T", "tests": "3 passed", "summary": "fixed rounding"}

def test_code_task_result_failed_no_pr():
    r = code_task_result("failed", tests="1 failed", reason="tests still red")
    assert r["outcome"] == "failed"
    assert r["pr_url"] is None
    assert r["reason"] == "tests still red"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest coder/tests/test_git_ops.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'coder.git_ops'`.

- [ ] **Step 3: Implement `coder/git_ops.py`**

```python
# coder/git_ops.py
"""Pure, IO-free helpers for the coder service: branch naming, the gh pr-create
argv, and the result body. Kept separate so they unit-test with no git/network."""
from __future__ import annotations
import re

_SLUG_MAX = 40


def branch_slug(task: str, ts: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (task or "").lower()).strip("-")
    s = re.sub(r"-{2,}", "-", s)[:_SLUG_MAX].strip("-") or "task"
    return f"cto/{s}-{ts}"


def pr_create_args(*, head: str, base: str, title: str, body: str) -> list[str]:
    return ["pr", "create", "--head", head, "--base", base,
            "--title", title, "--body", body]


def code_task_result(outcome: str, *, pr_url=None, branch=None, tests=None,
                     summary: str = "", reason: str = "") -> dict:
    out = {"outcome": outcome, "pr_url": pr_url, "branch": branch,
           "tests": tests, "summary": summary}
    if reason:
        out["reason"] = reason
    return out
```

- [ ] **Step 4: Run to verify pass**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest coder/tests/test_git_ops.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add coder/git_ops.py coder/tests/test_git_ops.py
git commit -m "feat(coder): pure git/PR helpers (branch slug, gh args, result body)"
```

---

## Task 4: coder service orchestration (clone → agentic repo loop → gate → PR)

The repo-level adaptation the spec calls out. `run_code_task` clones the sandbox, points the ported coder's `WORKSPACE` at the checkout, drives it in **agentic** mode against the repo's own pytest (feeding the verbatim failure back), then applies the self-verify gate: repo suite green → branch/commit/push/`gh pr create`; red → `failed`, no PR. All IO is behind injectable seams so the whole thing tests with a fake model + a real temp git repo and stubbed push/gh.

**Files:**
- Create: `coder/service.py`
- Test: `coder/tests/test_service.py`

**Interfaces:**
- Consumes: `coder.coding_agent` (`set_workspace`, `build_agent_graph`, `TOOLS_BASE`, `content_text`, `CODER_SYSTEM_PROMPT`, `init_backend`), `coder.git_ops`, `coder.config.Settings`.
- Produces:
  - `Seams` (dataclass) with callables: `clone(settings, dest) -> str` (returns checkout path), `run_agent(task, feedback, checkout, settings) -> None` (mutates the checkout), `run_repo_tests(checkout, settings) -> dict` (`{all_passed,passed,failed,stdout}`), `git_publish(checkout, branch, title, body, settings) -> str` (commit+push+`gh pr create`, returns pr_url), `now() -> str` (UTC `YYYYMMDDTHHMMSSZ`).
  - `default_seams() -> Seams`
  - `run_code_task(kind: str, task: str, *, settings: Settings, seams: Optional[Seams] = None) -> dict` — returns a `git_ops.code_task_result(...)` body.

- [ ] **Step 1: Write the failing tests** (fake model = a seam that writes the fix; real pytest on a temp git repo; stubbed publish)

```python
# coder/tests/test_service.py
import subprocess
from pathlib import Path
import pytest
from coder.config import Settings
from coder import service as svc


def _init_repo(root: Path) -> Path:
    """A tiny real git repo whose test fails until helper.py is fixed."""
    (root / "helper.py").write_text("def dbl(n):\n    return n + n + 1  # bug\n")
    (root / "test_helper.py").write_text(
        "from helper import dbl\n\ndef test_dbl():\n    assert dbl(2) == 4\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "baseline"], cwd=root, check=True)
    return root


def _seams(tmp_path, checkout, publish_calls, *, agent_fixes: bool):
    def clone(settings, dest):
        return str(checkout)
    def run_agent(task, feedback, co, settings):
        if agent_fixes:  # stand in for the model editing the repo file
            (Path(co) / "helper.py").write_text("def dbl(n):\n    return n + n\n")
    def run_repo_tests(co, settings):
        p = subprocess.run(["python", "-m", "pytest", "-q"], cwd=co,
                           capture_output=True, text=True)
        out = p.stdout + p.stderr
        import re
        passed = int((re.search(r"(\d+) passed", out) or [0, 0])[1] or 0) if "passed" in out else 0
        return {"all_passed": p.returncode == 0, "passed": passed,
                "failed": 0 if p.returncode == 0 else 1, "stdout": out}
    def git_publish(co, branch, title, body, settings):
        publish_calls.append(branch)
        return f"https://github.com/{settings.sandbox_repo}/pull/1"
    return svc.Seams(clone=clone, run_agent=run_agent, run_repo_tests=run_repo_tests,
                     git_publish=git_publish, now=lambda: "20260813T120000Z")


def test_green_opens_pr(tmp_path):
    checkout = _init_repo(tmp_path / "repo")
    calls = []
    s = Settings.from_env({})
    res = svc.run_code_task("delivery", "make dbl double", settings=s,
                            seams=_seams(tmp_path, checkout, calls, agent_fixes=True))
    assert res["outcome"] == "executed"
    assert res["pr_url"].endswith("/pull/1")
    assert res["branch"].startswith("cto/")
    assert len(calls) == 1                       # published exactly once

def test_red_makes_no_pr(tmp_path):
    checkout = _init_repo(tmp_path / "repo")
    calls = []
    s = Settings.from_env({})
    res = svc.run_code_task("delivery", "make dbl double", settings=s,
                            seams=_seams(tmp_path, checkout, calls, agent_fixes=False))
    assert res["outcome"] == "failed"
    assert res["pr_url"] is None
    assert calls == []                           # never published on red
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest coder/tests/test_service.py -q`
Expected: FAIL — `AttributeError: module 'coder.service' has no attribute 'Seams'`.

- [ ] **Step 3: Implement `coder/service.py`**

```python
# coder/service.py
"""The coder service orchestration: turn (kind, task) into a PR-gated PR against
the sandbox. Clone -> point the ported coder at the checkout -> agentic loop that
edits repo files and re-verifies against the repo's OWN pytest -> self-verify
gate (green -> branch+commit+push+gh pr create; red -> failed, no PR). All IO is
behind `Seams` so it tests offline with a fake model + a temp git repo."""
from __future__ import annotations
import logging
import os
import shutil
import subprocess
import sys
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from langchain_core.messages import HumanMessage

from .config import Settings
from . import coding_agent as ca
from . import git_ops

log = logging.getLogger("coder.service")

_MAX_ROUNDS = int(os.environ.get("CODER_MAX_ROUNDS", "3"))


@dataclass
class Seams:
    clone: Callable
    run_agent: Callable
    run_repo_tests: Callable
    git_publish: Callable
    now: Callable


# --- default (real) seams ----------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _clone(settings: Settings, dest: str) -> str:
    url = settings.sandbox_clone_url
    if settings.gh_token and url.startswith("https://github.com/"):
        url = url.replace("https://github.com/",
                          f"https://x-access-token:{settings.gh_token}@github.com/")
    subprocess.run(["git", "clone", "--depth", "1", url, dest],
                   check=True, capture_output=True, text=True)
    return dest


def _run_repo_tests(checkout: str, settings: Settings) -> dict:
    p = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=checkout,
                       capture_output=True, text=True, timeout=settings.test_timeout)
    out = (p.stdout + p.stderr)
    mp, mf = re.search(r"(\d+) passed", out), re.search(r"(\d+) failed", out)
    return {"all_passed": p.returncode == 0,
            "passed": int(mp.group(1)) if mp else 0,
            "failed": int(mf.group(1)) if mf else (0 if p.returncode == 0 else 1),
            "stdout": out[-4000:]}


def _run_agent(task: str, feedback: str, checkout: str, settings: Settings) -> None:
    """One agentic pass: let the coder read the failing tests and edit repo files
    in place using its tools. Uses the ported build_agent_graph over TOOLS_BASE."""
    ca.set_workspace(checkout)
    graph = ca.build_agent_graph(ca.TOOLS_BASE, system=ca.CODER_SYSTEM_PROMPT, role="fast")
    prompt = (
        f"TASK ({task}).\n\nYou are working inside an existing Python repo at the "
        "workspace root. The repo has a pytest suite. Read the relevant files and "
        "the failing test, then EDIT the repo's source files in place using "
        "write_file to make `python -m pytest -q` pass. Do not create a new "
        "solution file; fix the real files. Verify with run_tests/bash as you go.\n\n"
        f"CURRENT TEST OUTPUT (verbatim ground truth):\n{feedback[:2000]}")
    graph.invoke({"messages": [HumanMessage(prompt)]},
                 config=ca.run_config("code-task", recursion_limit=2 * ca.MAX_ITERATIONS))


def _git_publish(checkout: str, branch: str, title: str, body: str,
                 settings: Settings) -> str:
    env = dict(os.environ)
    if settings.gh_token:
        env["GH_TOKEN"] = settings.gh_token
    run = lambda args: subprocess.run(args, cwd=checkout, check=True,
                                      capture_output=True, text=True, env=env)
    run(["git", "checkout", "-b", branch])
    run(["git", "-c", "user.email=coder@nano.bank", "-c", "user.name=nano-bank coder",
         "commit", "-am", title])
    run(["git", "push", "-u", "origin", branch])
    r = run(["gh"] + git_ops.pr_create_args(head=branch, base=settings.pr_base,
                                            title=title, body=body))
    return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""


def default_seams() -> Seams:
    return Seams(clone=_clone, run_agent=_run_agent, run_repo_tests=_run_repo_tests,
                 git_publish=_git_publish, now=_now)


# --- orchestration -----------------------------------------------------------

def run_code_task(kind: str, task: str, *, settings: Settings,
                  seams: Optional[Seams] = None) -> dict:
    seams = seams or default_seams()
    ts = seams.now()
    work = tempfile.mkdtemp(prefix="coder-", dir=_ensure_root(settings))
    checkout = os.path.join(work, "repo")
    try:
        seams.clone(settings, checkout)
        tests = seams.run_repo_tests(checkout, settings)          # capture the contract
        rounds = 0
        while not tests["all_passed"] and rounds < _MAX_ROUNDS:
            rounds += 1
            seams.run_agent(task, tests["stdout"], checkout, settings)
            tests = seams.run_repo_tests(checkout, settings)
        summary = f"{kind}: {task[:120]}"
        if not tests["all_passed"]:
            return git_ops.code_task_result(
                "failed", tests=f"{tests['passed']}p/{tests['failed']}f",
                summary=summary, reason="repo tests still red after coder rounds")
        branch = git_ops.branch_slug(task, ts)
        body = (f"Delegated by the Agent CTO (kind: {kind}).\n\nTask: {task}\n\n"
                "Authored by the coder against the sandbox; repo tests are green. "
                "PR-gated — a human reviews and merges.")
        pr_url = seams.git_publish(checkout, branch, title=summary, body=body,
                                   settings=settings)
        return git_ops.code_task_result(
            "executed", pr_url=pr_url or None, branch=branch,
            tests=f"{tests['passed']}p/{tests['failed']}f", summary=summary)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _ensure_root(settings: Settings) -> str:
    Path(settings.workspace_root).mkdir(parents=True, exist_ok=True)
    return settings.workspace_root
```

> The real `_run_agent` calls the model; the tests inject a fake `run_agent`, so no network runs in CI. `init_backend()` is called by the API layer (Task 5) before the first task, not here, so the temp-repo tests stay offline.

- [ ] **Step 4: Run to verify pass**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest coder/tests/test_service.py -q`
Expected: PASS (both). `test_red_makes_no_pr` will run the agent seam `_MAX_ROUNDS` times (a no-op fake) then return `failed`.

- [ ] **Step 5: Commit**

```bash
git add coder/service.py coder/tests/test_service.py
git commit -m "feat(coder): service orchestration — clone, agentic repo loop, self-verify gate, gated PR"
```

---

## Task 5: coder FastAPI service + entrypoint

**Files:**
- Create: `coder/api.py`, `coder/api_main.py`
- Test: `coder/tests/test_api.py`

**Interfaces:**
- Consumes: `coder.service.run_code_task`, `coder.config.Settings`, `coder.model_factory.backend_healthcheck`.
- Produces: `create_app(settings, run_fn=None, probes=None) -> FastAPI` with `GET /livez`, `GET /health`, `POST /code-task {kind, task}`.

- [ ] **Step 1: Write the failing test**

```python
# coder/tests/test_api.py
from fastapi.testclient import TestClient
from coder.config import Settings
from coder.api import create_app

def _client(run_fn):
    return TestClient(create_app(Settings.from_env({}), run_fn=run_fn,
                                 probes={"ollama": lambda: True}))

def test_livez():
    c = _client(lambda kind, task, settings: {})
    assert c.get("/livez").json()["status"] == "ok"

def test_health_reports_probes():
    c = _client(lambda kind, task, settings: {})
    body = c.get("/health").json()
    assert body["service"] == "coder"
    assert body["checks"]["ollama"] is True

def test_code_task_delegates_to_run_fn():
    seen = {}
    def run_fn(kind, task, settings):
        seen["kind"], seen["task"] = kind, task
        return {"outcome": "executed", "pr_url": "https://x/pull/1"}
    c = _client(run_fn)
    r = c.post("/code-task", json={"kind": "delivery", "task": "do X"})
    assert r.json()["outcome"] == "executed"
    assert seen == {"kind": "delivery", "task": "do X"}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest coder/tests/test_api.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'coder.api'`.

- [ ] **Step 3: Implement `coder/api.py` and `coder/api_main.py`**

```python
# coder/api.py
from __future__ import annotations
from typing import Callable, Optional

from fastapi import FastAPI
from pydantic import BaseModel

from .config import Settings
from .service import run_code_task as default_run


class CodeTaskRequest(BaseModel):
    kind: str
    task: str


def _default_probes(settings: Settings) -> dict:
    def ollama() -> bool:
        from . import model_factory as mf
        return mf.backend_healthcheck(settings)
    return {"ollama": ollama}


def create_app(settings: Settings, run_fn: Optional[Callable] = None,
               probes: Optional[dict] = None) -> FastAPI:
    run_fn = run_fn or (lambda kind, task, settings: default_run(kind, task, settings=settings))
    probes = probes if probes is not None else _default_probes(settings)
    app = FastAPI(title="nano-bank coder")

    @app.get("/livez")
    def livez():
        return {"status": "ok", "service": "coder"}

    @app.get("/health")
    def health():
        checks = {}
        for name, probe in probes.items():
            try:
                checks[name] = bool(probe())
            except Exception:  # noqa: BLE001
                checks[name] = False
        return {"status": "ok", "service": "coder", "checks": checks}

    @app.post("/code-task")
    def code_task(req: CodeTaskRequest):
        return run_fn(req.kind, req.task, settings)

    return app
```
```python
# coder/api_main.py
from __future__ import annotations

import uvicorn

from .config import Settings
from .api import create_app
from . import model_factory as mf


def main():
    settings = Settings.from_env()
    mf.init_models(settings)          # resolve kimi models once at boot
    app = create_app(settings)
    uvicorn.run(app, host="0.0.0.0", port=settings.api_port)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify pass**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest coder/tests/test_api.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add coder/api.py coder/api_main.py coder/tests/test_api.py
git commit -m "feat(coder): FastAPI service (/code-task, /livez, /health) + entrypoint"
```

---

## Task 6: coder image, requirements, k8s manifest, README

Deliverable is a deployable service. Fold the packaging files into one task; the gate is a local image build + a `kubectl apply --dry-run` of the manifest.

**Files:**
- Create: `coder/requirements.txt`, `coder/Dockerfile`, `coder/k8s/coder.yaml`, `coder/k8s/deploy.sh`, `coder/README.md`

- [ ] **Step 1: `coder/requirements.txt`**

```
langgraph>=1,<2
langchain-core>=1,<2
langchain-openai>=1,<2
pydantic>=2
rich>=13
fastapi>=0.115
uvicorn>=0.30
httpx>=0.27,<1
pytest>=8.0
```

- [ ] **Step 2: `coder/Dockerfile`** (bundles `git` + `gh` + pytest)

```dockerfile
# Build from the REPO ROOT:  docker build -f coder/Dockerfile -t nano-coder:dev .
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl ca-certificates gnupg \
 && install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
 && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update && apt-get install -y --no-install-recommends gh \
 && rm -rf /var/lib/apt/lists/*
COPY coder/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY coder /app/coder
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "coder.api_main"]
```

- [ ] **Step 3: `coder/k8s/coder.yaml`** (Deployment + Service `:8096`; ollama secret reused; gh token secret mounted)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: coder
  namespace: nano-bank
spec:
  replicas: 1
  selector: { matchLabels: { app: coder } }
  template:
    metadata: { labels: { app: coder } }
    spec:
      containers:
        - name: coder
          image: nano-coder:dev
          imagePullPolicy: IfNotPresent
          ports: [ { containerPort: 8096 } ]
          env:
            - name: OLLAMA_API_KEY
              valueFrom: { secretKeyRef: { name: cto-ollama, key: api-key } }
            - name: OLLAMA_BASE_URL
              value: "https://ollama.com/v1"
            - name: SANDBOX_REPO
              value: "bvcmartins/cto-sandbox"
            - name: GH_TOKEN_PATH
              value: "/etc/coder/gh-token"
            - name: CODER_WORKSPACE_ROOT
              value: "/tmp/coder-workspaces"
          volumeMounts:
            - { name: gh-token, mountPath: /etc/coder, readOnly: true }
          livenessProbe:
            httpGet: { path: /livez, port: 8096 }
            initialDelaySeconds: 5
            periodSeconds: 10
      volumes:
        - name: gh-token
          secret:
            secretName: coder-gh-token
            items: [ { key: token, path: gh-token } ]
---
apiVersion: v1
kind: Service
metadata:
  name: coder
  namespace: nano-bank
spec:
  selector: { app: coder }
  ports: [ { port: 8096, targetPort: 8096 } ]
```

> The `cto-ollama` secret already exists (the CTO uses it). Verify its exact name/key against `cto/k8s/cto.yaml` during implementation and match it; adjust the `secretKeyRef` if the CTO uses a different key name.

- [ ] **Step 4: `coder/k8s/deploy.sh`** (build → `kind load` → apply; snap env at top)

```bash
#!/usr/bin/env bash
set -euo pipefail
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/1000}"
export XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
cd "$(dirname "$0")/../.."          # repo root
CTX=kind-nano-bank
echo "🔨 building nano-coder:dev ..."
docker build -f coder/Dockerfile -t nano-coder:dev .
echo "📦 loading image into kind ..."
kind load docker-image nano-coder:dev --name nano-bank
echo "🔑 ensuring coder-gh-token secret exists ..."
kubectl --context "$CTX" -n nano-bank get secret coder-gh-token >/dev/null 2>&1 || {
  echo "   MISSING: create it first — see coder/README.md (provisioning)"; exit 1; }
echo "🚀 applying coder manifest ..."
kubectl --context "$CTX" apply -f coder/k8s/coder.yaml
kubectl --context "$CTX" -n nano-bank rollout status deploy/coder --timeout=120s
```

- [ ] **Step 5: `coder/README.md`** — document: what the coder is, the `/code-task` contract, the ONE seam vs the port, and the one-time provisioning (create `cto-sandbox`, mint a repo-scoped gh token, `kubectl create secret generic coder-gh-token --from-literal=token=…`, confirm cluster egress to github.com + ollama.com). Reference the design spec.

- [ ] **Step 6: Verify build + manifest**

```bash
export XDG_RUNTIME_DIR=/run/user/1000 XDG_DATA_HOME=/home/bmartins/.local/share
docker build -f coder/Dockerfile -t nano-coder:dev .
kubectl --context kind-nano-bank apply -f coder/k8s/coder.yaml --dry-run=client
```
Expected: image builds; dry-run prints `deployment.apps/coder configured (dry run)` + `service/coder configured (dry run)`.

- [ ] **Step 7: Commit**

```bash
git add coder/requirements.txt coder/Dockerfile coder/k8s/coder.yaml coder/k8s/deploy.sh coder/README.md
git commit -m "build(coder): image (git+gh), k8s manifest (:8096), deploy script, README"
```

---

## Task 7: platform_mcp coder client

**Files:**
- Create: `platform_mcp/coder_client.py`
- Modify: `platform_mcp/config.py` (add `coder_url`, `coder_timeout`, `coder_sandbox_repo`)
- Test: `platform_mcp/tests/test_coder_client.py`, extend `platform_mcp/tests/test_config.py`

**Interfaces:**
- Produces: `CoderClient(settings, transport=None)` with `.code_task(kind: str, task: str) -> dict` (POSTs `/code-task`, returns the coder's JSON body). New `Settings` fields `coder_url: str` (default `http://coder:8096`), `coder_timeout: float` (default `900`), `coder_sandbox_repo: str` (default `bvcmartins/cto-sandbox`).

- [ ] **Step 1: Write the failing test**

```python
# platform_mcp/tests/test_coder_client.py
import httpx
from platform_mcp.config import Settings
from platform_mcp.coder_client import CoderClient

def _settings():
    return Settings.from_env({"SERVICE_CLIENT_SECRET": "x"})

def test_code_task_posts_and_returns_body():
    seen = {}
    def handler(request):
        seen["url"] = str(request.url)
        seen["json"] = httpx._content.json_loads(request.content)
        return httpx.Response(200, json={"outcome": "executed", "pr_url": "https://x/pull/1"})
    tr = httpx.MockTransport(handler)
    out = CoderClient(_settings(), transport=tr).code_task("delivery", "do X")
    assert out["outcome"] == "executed"
    assert seen["url"].endswith("/code-task")
    assert seen["json"] == {"kind": "delivery", "task": "do X"}
```

```python
# add to platform_mcp/tests/test_config.py
def test_coder_defaults():
    from platform_mcp.config import Settings
    s = Settings.from_env({})
    assert s.coder_url == "http://coder:8096"
    assert s.coder_sandbox_repo == "bvcmartins/cto-sandbox"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest platform_mcp/tests/test_coder_client.py platform_mcp/tests/test_config.py -q`
Expected: FAIL — no `coder_client` module / missing `coder_url`.

- [ ] **Step 3: Add the config fields**

In `platform_mcp/config.py`, add to the `Settings` dataclass fields `coder_url: str`, `coder_timeout: float`, `coder_sandbox_repo: str`, and in `from_env(...)`:

```python
            coder_url=e.get("CODER_URL", "http://coder:8096"),
            coder_timeout=float(e.get("CODER_TIMEOUT", "900")),
            coder_sandbox_repo=e.get("CODER_SANDBOX_REPO", "bvcmartins/cto-sandbox"),
```

- [ ] **Step 4: Implement `platform_mcp/coder_client.py`**

```python
# platform_mcp/coder_client.py
"""HTTP client the delegation lever uses to reach the in-cluster coder service.
Mirrors audit.LedgerAudit's httpx shape; no auth (in-cluster service-to-service)."""
from __future__ import annotations
from typing import Optional

import httpx

from .config import Settings


class CoderClient:
    def __init__(self, settings: Settings, transport: Optional[httpx.BaseTransport] = None):
        self._s = settings
        self._http = httpx.Client(base_url=settings.coder_url,
                                  timeout=settings.coder_timeout, transport=transport)

    def code_task(self, kind: str, task: str) -> dict:
        r = self._http.post("/code-task", json={"kind": kind, "task": task})
        r.raise_for_status()
        return r.json()
```

- [ ] **Step 5: Run to verify pass**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest platform_mcp/tests/test_coder_client.py platform_mcp/tests/test_config.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add platform_mcp/coder_client.py platform_mcp/config.py platform_mcp/tests/test_coder_client.py platform_mcp/tests/test_config.py
git commit -m "feat(platform_mcp): coder HTTP client + coder settings"
```

---

## Task 8: remediation precondition (pure)

**Files:**
- Modify: `platform_mcp/levers.py` (add `remediation_signal_present`)
- Test: extend `platform_mcp/tests/test_levers.py`

**Interfaces:**
- Consumes: existing `levers.restart_warranted`, `levers._is_stalled`, `levers.is_allowed`.
- Produces: `remediation_signal_present(deployments, pods, allow_list, threshold=5) -> bool` — True iff some **allow-listed** deployment is degraded (`ready<desired`), crashlooping, or stalled (`ProgressDeadlineExceeded`) right now.

- [ ] **Step 1: Write the failing tests**

```python
# add to platform_mcp/tests/test_levers.py
from platform_mcp.levers import remediation_signal_present

_ALLOW = [("nano-bank", "cfo")]

def test_remediation_signal_true_when_degraded():
    deps = [{"cluster": "nano-bank", "name": "cfo", "desired": 2, "ready": 1, "conditions": []}]
    assert remediation_signal_present(deps, [], _ALLOW) is True

def test_remediation_signal_false_when_all_healthy():
    deps = [{"cluster": "nano-bank", "name": "cfo", "desired": 2, "ready": 2, "conditions": []}]
    assert remediation_signal_present(deps, [], _ALLOW) is False

def test_remediation_signal_ignores_non_allowlisted():
    deps = [{"cluster": "nano-bank", "name": "postgres", "desired": 2, "ready": 0, "conditions": []}]
    assert remediation_signal_present(deps, _ALLOW, ) if False else \
        remediation_signal_present(deps, [], _ALLOW) is False

def test_remediation_signal_true_when_stalled():
    deps = [{"cluster": "nano-bank", "name": "cfo", "desired": 1, "ready": 1,
             "conditions": [{"type": "Progressing", "reason": "ProgressDeadlineExceeded"}]}]
    assert remediation_signal_present(deps, [], _ALLOW) is True
```

> Fix the third test's call to just `remediation_signal_present(deps, [], _ALLOW)` (drop the dead ternary) when typing it out — kept explicit here to show the allow-list gate: a degraded `postgres` (not allow-listed) yields False.

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest platform_mcp/tests/test_levers.py -q`
Expected: FAIL — `ImportError: cannot import name 'remediation_signal_present'`.

- [ ] **Step 3: Implement in `platform_mcp/levers.py`**

```python
def remediation_signal_present(deployments: list, pods: list, allow_list,
                               threshold: int = 5) -> bool:
    """True iff some allow-listed deployment is unhealthy right now (degraded,
    crashlooping, or stalled) — the observed failing platform signal that a
    kind='remediation' delegation requires. Same reads Phase B self-verifies on."""
    allowed = set(allow_list)
    for d in deployments:
        if (d.get("cluster"), d.get("name")) not in allowed:
            continue
        if _is_stalled(d):
            return True
        if restart_warranted(d, pods, threshold):
            return True
    return False
```

- [ ] **Step 4: Run to verify pass**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest platform_mcp/tests/test_levers.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add platform_mcp/levers.py platform_mcp/tests/test_levers.py
git commit -m "feat(platform_mcp): remediation precondition — observed failing signal on an allow-listed app"
```

---

## Task 9: the `delegate_coding_task` lever (verify → act → audit)

**Files:**
- Modify: `platform_mcp/mcp_server.py` (add `_do_delegate`; `build_mcp(..., coder=None)`; register the tool; `main()` builds a `CoderClient`)
- Test: `platform_mcp/tests/test_delegate.py`

**Interfaces:**
- Consumes: `levers.remediation_signal_present` (Task 8), `CoderClient.code_task` (Task 7), the existing `audit.post_action`, `k8s.deployments()`/`k8s.pods()`.
- Produces: module fn `_do_delegate(k8s, coder, audit, settings, kind, task) -> dict`; MCP tool `delegate_coding_task(kind: str, task: str) -> dict`. Returns `{"outcome": "executed"|"refused"|"failed", ...}`; every path audits one row `action="delegate_coding_task"`, `params={"kind","task"}`, `effect=<the returned dict>`.

- [ ] **Step 1: Write the failing tests** (fake coder + fake audit + fake k8s)

```python
# platform_mcp/tests/test_delegate.py
from platform_mcp.config import Settings
from platform_mcp import mcp_server as srv


class FakeAudit:
    def __init__(self): self.rows = []
    def post_action(self, action, params, effect):
        self.rows.append((action, params, effect)); return {"id": len(self.rows)}


class FakeCoder:
    def __init__(self, ret=None, boom=False): self.ret, self.boom, self.calls = ret, boom, []
    def code_task(self, kind, task):
        self.calls.append((kind, task))
        if self.boom: raise RuntimeError("connect refused")
        return self.ret


class FakeK8s:
    def __init__(self, deps): self._d = deps
    def deployments(self): return self._d
    def pods(self): return []


def _settings():
    return Settings.from_env({"SERVICE_CLIENT_SECRET": "x"})

_DEGRADED = [{"cluster": "nano-bank", "name": "cfo", "desired": 2, "ready": 1, "conditions": []}]
_HEALTHY = [{"cluster": "nano-bank", "name": "cfo", "desired": 2, "ready": 2, "conditions": []}]


def test_delivery_executes_and_audits():
    audit = FakeAudit()
    coder = FakeCoder(ret={"outcome": "executed", "pr_url": "https://x/pull/1",
                           "branch": "cto/x-T", "tests": "3p/0f", "summary": "s"})
    out = srv._do_delegate(FakeK8s(_HEALTHY), coder, audit, _settings(), "delivery", "do X")
    assert out["outcome"] == "executed"
    assert coder.calls == [("delivery", "do X")]
    assert audit.rows[0][0] == "delegate_coding_task"
    assert audit.rows[0][2]["pr_url"] == "https://x/pull/1"

def test_remediation_refused_without_signal():
    audit = FakeAudit(); coder = FakeCoder()
    out = srv._do_delegate(FakeK8s(_HEALTHY), coder, audit, _settings(), "remediation", "fix cfo")
    assert out["outcome"] == "refused"
    assert coder.calls == []                       # never called the coder
    assert audit.rows[0][2]["outcome"] == "refused"

def test_remediation_executes_with_signal():
    audit = FakeAudit()
    coder = FakeCoder(ret={"outcome": "executed", "pr_url": "https://x/pull/2"})
    out = srv._do_delegate(FakeK8s(_DEGRADED), coder, audit, _settings(), "remediation", "fix cfo")
    assert out["outcome"] == "executed"
    assert coder.calls == [("remediation", "fix cfo")]

def test_unknown_kind_refused():
    audit = FakeAudit(); coder = FakeCoder()
    out = srv._do_delegate(FakeK8s(_HEALTHY), coder, audit, _settings(), "yolo", "x")
    assert out["outcome"] == "refused" and coder.calls == []

def test_empty_task_refused():
    audit = FakeAudit(); coder = FakeCoder()
    out = srv._do_delegate(FakeK8s(_HEALTHY), coder, audit, _settings(), "delivery", "   ")
    assert out["outcome"] == "refused" and coder.calls == []

def test_coder_unreachable_is_failed_not_crash():
    audit = FakeAudit(); coder = FakeCoder(boom=True)
    out = srv._do_delegate(FakeK8s(_HEALTHY), coder, audit, _settings(), "delivery", "do X")
    assert out["outcome"] == "failed"
    assert audit.rows[0][2]["outcome"] == "failed"

def test_delegate_tool_registered_when_coder_present():
    class _K: 
        def deployments(self): return []
        def pods(self): return []
    mcp = srv.build_mcp(_K(), health=type("H", (), {"probe": lambda self: []})(),
                        coder=FakeCoder(), audit=FakeAudit(), settings=_settings())
    import anyio
    names = {t.name for t in anyio.run(mcp.list_tools)}
    assert "delegate_coding_task" in names
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest platform_mcp/tests/test_delegate.py -q`
Expected: FAIL — `_do_delegate` / `coder` kwarg not present.

- [ ] **Step 3: Add `_do_delegate` to `platform_mcp/mcp_server.py`** (beside the Phase B `_do_restart`/`_do_rollback`)

```python
_VALID_KINDS = ("remediation", "delivery")


def _failed(reason):
    return {"outcome": "failed", "reason": reason}


def _do_delegate(k8s, coder, audit, settings, kind, task):
    """Delegate a coding task to the coder (opens a PR-gated PR against the sandbox).
    Structurally allow-listed: the coder service is pinned to the sandbox repo, so
    there is no repo to choose. remediation requires an observed failing signal."""
    params = {"kind": kind, "task": task}
    if kind not in _VALID_KINDS:
        outcome = _refused(f"unknown task kind {kind!r} (expected remediation|delivery)")
        audit.post_action("delegate_coding_task", params, outcome)
        return outcome
    if not (task or "").strip():
        outcome = _refused("empty task")
        audit.post_action("delegate_coding_task", params, outcome)
        return outcome
    if kind == "remediation" and not levers.remediation_signal_present(
            k8s.deployments(), k8s.pods(), settings.allow_list):
        outcome = _refused("no failing/degraded platform signal observed; "
                           "remediation is unwarranted")
        audit.post_action("delegate_coding_task", params, outcome)
        return outcome
    try:
        outcome = coder.code_task(kind, task)      # HTTP to the coder service
    except Exception as e:  # noqa: BLE001
        outcome = _failed(f"coder unreachable: {e}")
        audit.post_action("delegate_coding_task", params, outcome)
        return outcome
    audit.post_action("delegate_coding_task", params, outcome)
    return outcome
```

- [ ] **Step 4: Thread `coder` through `build_mcp` and register the tool**

Change the signature to `def build_mcp(k8s, health, writer=None, audit=None, settings=None, coder=None) -> FastMCP:` and add, inside `build_mcp` (independent of the writer block — delegation needs only `coder` + `audit` + `settings`, plus the `k8s` reads it already has):

```python
    if coder is not None and audit is not None and settings is not None:
        @mcp.tool()
        def delegate_coding_task(kind: str, task: str) -> dict:
            """Delegate a scoped coding task to the engineering coder, which opens a
            PR-gated pull request against the sandbox service repo. kind='remediation'
            (a durable root-cause code fix — REFUSED unless a real failing/degraded
            platform signal is observed) or kind='delivery' (a handed-down backlog
            task). You do NOT write code yourself; you delegate it. A human reviews and
            MERGES the PR — never you. Autonomous + audited; report the outcome and the
            PR link verbatim."""
            return _stringify(_do_delegate(k8s, coder, audit, settings, kind, task))
```

In `main()`, build and pass a `CoderClient`:

```python
    from .coder_client import CoderClient
    coder = CoderClient(settings)
    mcp = build_mcp(k8s, health, writer=writer, audit=audit, settings=settings, coder=coder)
```

- [ ] **Step 5: Run to verify pass**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest platform_mcp/tests/test_delegate.py platform_mcp/tests/ -q`
Expected: PASS (new file + no regression across the platform_mcp suite).

- [ ] **Step 6: Commit**

```bash
git add platform_mcp/mcp_server.py platform_mcp/tests/test_delegate.py
git commit -m "feat(platform_mcp): delegate_coding_task lever — verify (kind+signal) -> act (coder) -> audit"
```

---

## Task 10: CTO prompt — add the delegation lever

**Files:**
- Modify: `cto/agent.py` (the `CTO_PROMPT` string and the module docstring)
- Test: extend `cto/tests/` with a prompt-content assertion (find the existing prompt test file; if none, add `cto/tests/test_prompt.py`)

**Interfaces:** none new — the CTO already receives every platform_mcp tool over MCP, so `delegate_coding_task` becomes available automatically once registered (Task 9). This task only teaches the model when/how to use it.

- [ ] **Step 1: Write the failing test**

```python
# cto/tests/test_prompt.py
from cto.agent import CTO_PROMPT

def test_prompt_mentions_delegation_lever():
    p = CTO_PROMPT.lower()
    assert "delegate_coding_task" in p
    assert "pull request" in p or "pr" in p
    assert "merge" in p            # the human-merge gate is stated
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest cto/tests/test_prompt.py -q`
Expected: FAIL — assertion (the current prompt has no delegation language).

- [ ] **Step 3: Edit `CTO_PROMPT`**

In `cto/agent.py`, change the clause `"You still write NO code, and outside these two levers you take no other action…"` to reflect the third lever. Insert, right after the rollback/restart lever description and before the "outside these levers you OBSERVE" clause:

```
        "You have a THIRD lever, `delegate_coding_task(kind, task)`: you do NOT "
        "write code by hand, but you DELEGATE a scoped coding task to the "
        "engineering coder, which opens a PR-gated pull request against the "
        "sandbox service repo. Use kind='remediation' for a durable root-cause "
        "code fix AFTER you've stopped the bleeding with a restart/rollback (the "
        "bank refuses it unless a real failing/degraded signal is present), and "
        "kind='delivery' for a handed-down backlog task. A human reviews and "
        "MERGES the PR — you NEVER merge. Quote the tool's outcome "
        "(executed/refused/failed) and the PR link EXACTLY. "
```

And soften the absolute "writes NO code" in the module docstring + the trailing clause to: outside the restart/rollback/delegate levers it takes no other infra action; it authors no code *by hand* — code changes go through the coder as gated PRs.

- [ ] **Step 4: Run to verify pass**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest cto/tests/test_prompt.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cto/agent.py cto/tests/test_prompt.py
git commit -m "feat(cto): prompt gains the delegate_coding_task lever (delegate, never merge)"
```

---

## Task 11: presentation console — the `delegated` outcome chip

**Files:**
- Modify: `csuite/trace_view.py` (`beat_outcome`: add the lever + kinds)
- Modify: `demos/08-cto/present/state.py` (`_STYLES`: add `delegated`, `failed`)
- Test: extend `csuite/tests/` (find the `beat_outcome` test file) and `demos/08-cto/present/tests/` (find the `outcome_style` test file)

**Interfaces:**
- Consumes: existing `beat_outcome(trace, outcome_hint=None) -> {"kind","detail"}` and `outcome_style(kind) -> (label, color)`.
- Produces: `beat_outcome` recognises a `delegate_coding_task` tool call → kind `delegated` (executed, detail=PR url), `refused`, or `failed`. `outcome_style("delegated")` → `("DELEGATED", "#0969da")`; `outcome_style("failed")` → `("FAILED", "#cf222e")`.

- [ ] **Step 1: Write the failing tests**

```python
# add to the existing csuite beat_outcome test module (e.g. csuite/tests/test_trace_view.py)
from csuite.trace_view import beat_outcome

def _delegate_ev(output):
    return [{"kind": "tool", "name": "delegate_coding_task", "output": output}]

def test_beat_outcome_delegated_executed_with_pr():
    ev = _delegate_ev({"outcome": "executed", "pr_url": "https://github.com/o/r/pull/7"})
    got = beat_outcome(ev)
    assert got["kind"] == "delegated"
    assert "pull/7" in got["detail"]

def test_beat_outcome_delegate_failed():
    ev = _delegate_ev({"outcome": "failed", "reason": "coder unreachable"})
    assert beat_outcome(ev)["kind"] == "failed"

def test_beat_outcome_delegate_refused():
    ev = _delegate_ev({"outcome": "refused", "reason": "no signal"})
    assert beat_outcome(ev)["kind"] == "refused"
```

```python
# add to the existing present state test module (e.g. demos/08-cto/present/tests/test_state.py)
from state import outcome_style      # match the existing import style in that test file

def test_outcome_style_delegated():
    assert outcome_style("delegated") == ("DELEGATED", "#0969da")

def test_outcome_style_failed()::
    assert outcome_style("failed") == ("FAILED", "#cf222e")
```

> Fix the stray double-colon typo (`::`) when typing it out. Match the existing test file's import convention for `outcome_style` (the present tests add the `present/` dir to `sys.path`).

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest csuite/tests/ demos/08-cto/present/tests/ -q`
Expected: FAIL on the new cases.

- [ ] **Step 3: Extend `beat_outcome` in `csuite/trace_view.py`**

Add `"delegate_coding_task"` to `_LEVER_TOOLS`, and special-case it before the existing restart/rollback detail parsing:

```python
_LEVER_TOOLS = {"execute_rollback", "execute_rollout_restart", "delegate_coding_task"}
```

Inside `beat_outcome`, after resolving `last` and `text`, before the current `kind = "refused" if ... else "executed"` line:

```python
    if last.get("name") == "delegate_coding_task":
        low = text.lower()
        if "executed" in low:
            m = re.search(r"pr_url['\"]?\s*[:=]\s*['\"]?(https?://[^'\"\s,}]+)", text)
            return {"kind": "delegated", "detail": (m.group(1) if m else "PR opened")}
        if "failed" in low:
            m = re.search(r"reason['\"]?\s*[:=]\s*['\"]([^'\"]+)", text)
            return {"kind": "failed", "detail": (m.group(1) if m else "")}
        m = re.search(r"reason['\"]?\s*[:=]\s*['\"]([^'\"]+)", text)
        return {"kind": "refused", "detail": (m.group(1) if m else "")}
```

- [ ] **Step 4: Add the styles in `demos/08-cto/present/state.py`**

```python
_STYLES = {
    "executed":  ("EXECUTED", "#1a7f37"),
    "refused":   ("REFUSED", "#b35900"),
    "deferred":  ("DEFERRED", "#6639ba"),
    "delegated": ("DELEGATED", "#0969da"),
    "failed":    ("FAILED", "#cf222e"),
    "read_only": ("READ-ONLY", "#57606a"),
}
```

- [ ] **Step 5: Run to verify pass**

Run: `cd /home/bmartins/dev/nano-bank && python -m pytest csuite/tests/ demos/08-cto/present/tests/ -q`
Expected: PASS (new + existing).

- [ ] **Step 6: Commit**

```bash
git add csuite/trace_view.py demos/08-cto/present/state.py csuite/tests demos/08-cto/present/tests
git commit -m "feat(present): delegated/failed outcome chips for delegate_coding_task"
```

---

## Task 12: sandbox seed + reseed + demo beats 8/9

Provisioning content for `cto-sandbox` (so the repo is reproducible), the host-side reseed script, the two new demo beats, the question sheet rows, and the `run-demo.sh` wiring. The sandbox baseline is **green** (the two known gaps are an `xfail(strict=True)` and a `skip`), so the repo's suite passes at baseline; each task makes its gap real and removes the marker, and the self-verify gate turns green only when the fix is complete.

**Files:**
- Create: `coder/sandbox-seed/README.md`, `coder/sandbox-seed/helper_service/__init__.py`, `coder/sandbox-seed/helper_service/rounding.py`, `coder/sandbox-seed/helper_service/fees.py`, `coder/sandbox-seed/tests/test_rounding.py`, `coder/sandbox-seed/tests/test_fees.py`, `coder/sandbox-seed/provision-sandbox.sh`
- Create: `demos/08-cto/reseed-sandbox.sh`
- Modify: `demos/08-cto/drive.py` (append beats 8, 9), `demos/08-cto/questions.md`, `demos/08-cto/run-demo.sh`

- [ ] **Step 1: Seed source + tests** (baseline green; each gap fixable)

```python
# coder/sandbox-seed/helper_service/__init__.py  -> empty
```
```python
# coder/sandbox-seed/helper_service/rounding.py
def split_amount(total_cents: int, n: int) -> list[int]:
    """Split total_cents into n parts. BUG: drops the remainder, so the parts
    don't sum back to total_cents. The remediation task fixes this."""
    each = total_cents // n
    return [each] * n
```
```python
# coder/sandbox-seed/helper_service/fees.py
def etransfer_fee(amount_cents: int) -> int:
    """Flat $1.50 e-transfer fee, in cents. STUB — the delivery task implements it."""
    raise NotImplementedError
```
```python
# coder/sandbox-seed/tests/test_rounding.py
import pytest
from helper_service.rounding import split_amount

@pytest.mark.xfail(strict=True, reason="known remainder-loss bug; remediation task fixes it")
def test_split_sums_back():
    parts = split_amount(100, 3)
    assert sum(parts) == 100
    assert len(parts) == 3
```
```python
# coder/sandbox-seed/tests/test_fees.py
import pytest
from helper_service.fees import etransfer_fee

@pytest.mark.skip(reason="etransfer_fee not implemented; delivery task implements it")
def test_flat_fee():
    assert etransfer_fee(5000) == 150
```
```
# coder/sandbox-seed/README.md
The `cto-sandbox` baseline: a tiny Python helper "service" the Agent CTO delegates
against. Two intentional, real gaps:
  * split_amount() drops the remainder (remediation target) — guarded by an
    xfail(strict=True) test so baseline is green.
  * etransfer_fee() is a stub (delivery target) — guarded by a skipped test.
A delegated PR fixes the code AND removes the marker; the coder's self-verify gate
(the repo's own pytest) only goes green when the fix is complete.
```

- [ ] **Step 2: `provision-sandbox.sh`** (one-time; documented, run by the user with a gh token)

```bash
#!/usr/bin/env bash
# One-time: create bvcmartins/cto-sandbox from coder/sandbox-seed and tag baseline.
# Needs: gh authenticated with repo scope. Run from the repo root.
set -euo pipefail
REPO="${SANDBOX_REPO:-bvcmartins/cto-sandbox}"
SEED="coder/sandbox-seed"
TMP="$(mktemp -d)"
cp -r "$SEED"/. "$TMP"/
cd "$TMP"
git init -q && git add -A
git -c user.email=coder@nano.bank -c user.name="nano-bank coder" commit -qm "baseline: helper_service with two intentional gaps"
git tag baseline
gh repo create "$REPO" --private --source=. --push
git push origin baseline
echo "provisioned $REPO (baseline tag pushed)"
```

- [ ] **Step 3: `demos/08-cto/reseed-sandbox.sh`** (before the new beats; restores baseline after)

```bash
#!/usr/bin/env bash
# Reseed the sandbox for a clean demo run: close open cto/* PRs, delete stale
# cto/* branches, and reset main to the baseline tag. Needs gh + a clone.
set -euo pipefail
REPO="${SANDBOX_REPO:-bvcmartins/cto-sandbox}"
echo "🧽 reseeding $REPO ..."
for n in $(gh pr list -R "$REPO" --state open --json number --jq '.[].number' 2>/dev/null || true); do
  gh pr close -R "$REPO" "$n" --delete-branch 2>/dev/null || true
done
for b in $(gh api "repos/$REPO/branches" --jq '.[].name' 2>/dev/null | grep '^cto/' || true); do
  gh api -X DELETE "repos/$REPO/git/refs/heads/$b" 2>/dev/null || true
done
echo "   sandbox reset to baseline ✓"
```

- [ ] **Step 4: Append beats 8 and 9 to `demos/08-cto/drive.py` BEATS**

```python
    {
        "title": "Durable remediation — the CTO delegates the root-cause fix as a gated PR",
        "shows": "rollback stopped the bleeding; now the CTO delegates the DURABLE "
                 "code fix. It calls delegate_coding_task(kind='remediation') — the "
                 "coder authors the fix in the sandbox, its own pytest goes green, and "
                 "a real PR-gated PR is opened (a human merges). The delegation is "
                 "audited in the same tamper-evident ledger.",
        "message": "You rolled cfo back — good. Now open the durable fix: delegate the "
                   "root-cause code change to the coder as a gated pull request. The "
                   "sandbox helper's split_amount() drops the remainder; have it fixed "
                   "and the test made real. Don't merge it — a human will. Tell me the "
                   "outcome and the PR link.",
        "thread": "new",
        "outcome_hint": "delegated",
    },
    {
        "title": "Delivery — the CTO delegates a backlog task as a gated PR",
        "shows": "the same lever for planned work: handed a backlog item, the CTO "
                 "delegates it (kind='delivery'). The coder implements it against the "
                 "sandbox suite and opens a gated PR — audited, human-merged.",
        "message": "Backlog task: implement the flat $1.50 e-transfer fee helper "
                   "(etransfer_fee) in the sandbox service and make its skipped test "
                   "pass. Delegate it as a gated PR — don't merge. Report the outcome "
                   "and the PR link.",
        "thread": "new",
        "outcome_hint": "delegated",
    },
```

- [ ] **Step 5: Add the two rows to `demos/08-cto/questions.md`** (mirror the existing table/format in that file — one row per beat with the question text and what it shows).

- [ ] **Step 6: Wire reseed + baseline restore into `demos/08-cto/run-demo.sh`**

- After the bring-up / before driving the arc, add a reseed call (guarded so a missing `gh`/sandbox doesn't abort a levers-only run):

```bash
if [ "$DO_BREAK" = "1" ]; then
  demos/08-cto/reseed-sandbox.sh || echo "⚠ sandbox reseed skipped (gh/sandbox not provisioned)"
fi
```
- Extend the closing section (after the ledger inspect) with a note that the two delegate rows now appear, and add a `reseed-sandbox.sh` call to the `--down` teardown branch so a torn-down demo also closes any PRs it opened.

- [ ] **Step 7: Verify the seed suite is green at baseline and the fixes turn it green**

```bash
cd /home/bmartins/dev/nano-bank/coder/sandbox-seed && python -m pytest -q
```
Expected: PASS (1 xfailed, 1 skipped, 0 failed) — baseline is green. (No commit gate for the demo scripts beyond this; they're exercised live in Task 13.)

- [ ] **Step 8: Commit**

```bash
git add coder/sandbox-seed demos/08-cto/reseed-sandbox.sh demos/08-cto/drive.py demos/08-cto/questions.md demos/08-cto/run-demo.sh
chmod +x coder/sandbox-seed/provision-sandbox.sh demos/08-cto/reseed-sandbox.sh
git commit -m "demo(cto): sandbox seed + reseed + beats 8/9 (delegated remediation + delivery)"
```

---

## Task 13: live smoke (GATED — needs provisioning + user go-ahead)

Not run unattended. Requires: `cto-sandbox` provisioned (Task 12 `provision-sandbox.sh`), the `coder-gh-token` secret in-cluster, and cluster egress to github.com + ollama.com. Per the user's "do not open a PR" instruction, run this **only** when they explicitly ask — it opens a real (sandbox) PR.

- [ ] **Step 1: Deploy the coder + redeploy platform_mcp (picks up the delegate tool)**

```bash
export XDG_RUNTIME_DIR=/run/user/1000 XDG_DATA_HOME=/home/bmartins/.local/share
./coder/k8s/deploy.sh
kubectl --context kind-nano-bank -n nano-bank rollout restart deploy/platform-mcp
kubectl --context kind-nano-bank -n nano-bank rollout status  deploy/platform-mcp --timeout=120s
```

- [ ] **Step 2: One real delegation via the CTO** (port-forward cto:8095 as run-demo does)

```bash
curl -fsS -XPOST localhost:8095/ask -H 'content-type: application/json' -d '{
  "message": "Delivery task: implement etransfer_fee in the sandbox and make its skipped test pass. Delegate it as a gated PR — do not merge. Report the PR link."}' | python -m json.tool
```
Expected: the answer quotes `outcome=executed` and a real `https://github.com/bvcmartins/cto-sandbox/pull/N` link; `gh pr list -R bvcmartins/cto-sandbox` shows it open (unmerged).

- [ ] **Step 3: Confirm the audit row**

```bash
CTX=kind-nano-bank NS=nano-bank demos/08-cto/inspect-ledger.sh
```
Expected: a `delegate_coding_task` row with `outcome=executed` and the PR url in `effect`, hash-chained.

- [ ] **Step 4: Reseed**

```bash
demos/08-cto/reseed-sandbox.sh
```
Expected: the smoke PR is closed and its branch deleted; sandbox back at baseline.

---

## Self-Review

**Spec coverage:**
- Goal (delegate → gated PR, two narratives) → Tasks 9 (lever), 4/5 (coder), 12 (beats 8 remediation + 9 delivery). ✓
- Non-goals: never merge → Global Constraints + Task 5/12 body copy + Task 10 prompt; sandbox-only → structural (no repo arg) noted in Tasks 9 + Global Constraints. ✓
- Decisions table: port of coding_agent_gemini.py → Task 2; kimi seam → Task 1; real PR → Task 4/5; dedicated sandbox reseeded → Task 12; in-cluster service → Task 6; lever in platform_mcp → Task 9; Python/pytest sandbox → Task 12. ✓
- §1 sandbox repo + reseed → Task 12. §2 port (verbatim structure + swapped seam + repo adaptation in service) → Tasks 2 + 4. §2b coder service (clone→gen→gate→branch→PR, /livez+/health, k8s, gh secret, egress) → Tasks 4/5/6. §3 lever (allow-list, precondition, audited, HTTP, failed-not-crash) → Tasks 7/8/9. §4 PR-gated semantics → Tasks 4/5/9 + copy. §5 demo beats + `delegated` chip → Tasks 11/12. §6 guardrails+testing (offline selftest kept, pure helpers, fake-model service test, fake-coder lever test, live smoke) → Tasks 2/3/4/9/13. Ports (:8096) → Tasks 1/6/7. Provisioning → Tasks 6/12/13. ✓

**Placeholder scan:** No "TBD"/"implement later". Two intentional in-test typos (`::`, the dead ternary) are called out with a fix instruction beside them so the engineer corrects them while typing. Task 5/10/11 say "find the existing test file" for suites whose exact filename the implementer confirms in-repo — the assertion code is given in full.

**Type consistency:** `code_task_result(...)` shape (`outcome/pr_url/branch/tests/summary[/reason]`) is produced in Task 3 and consumed identically by Task 4 (service), Task 7 (client passthrough), Task 9 (lever audit effect), Task 11 (chip parse of `outcome`/`pr_url`/`reason`). `delegate_coding_task(kind, task)` signature identical across Tasks 7/9/10/12/13. `Seams` field names identical across Task 4 impl + test. `remediation_signal_present(deployments, pods, allow_list, threshold=5)` identical in Tasks 8 + 9.
