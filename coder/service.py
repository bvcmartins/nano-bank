"""The coder service orchestration: turn (kind, task) into a PR-gated PR against
the sandbox. Clone -> point the ported coder at the checkout -> agentic loop that
edits repo files and re-verifies against the repo's OWN pytest -> self-verify
gate (green -> branch+commit+push+gh pr create; red -> failed, no PR). All IO is
behind `Seams` so it tests offline with a fake model + a temp git repo."""
from __future__ import annotations
import collections
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from langchain_core.messages import HumanMessage
from langgraph.errors import GraphRecursionError

from .config import Settings
from . import coding_agent as ca
from . import git_ops

log = logging.getLogger("coder.service")

_MAX_ROUNDS = int(os.environ.get("CODER_MAX_ROUNDS", "3"))
# Wall-clock ceiling for one delegation. The agentic loop can outlive the client's
# HTTP timeout (CoderClient gives up at ~900s) and keep spending tokens with nobody
# waiting; this bounds it, checked at each round boundary.
_RUN_DEADLINE_S = float(os.environ.get("CODER_RUN_DEADLINE_S", "1200"))

# run_code_task drives the coder through module-level globals (ca.set_workspace
# rebinds WORKSPACE, which the tools read at call time), so two concurrent runs would
# interleave into each other's checkout. FastAPI serves the sync POST /code-task on a
# threadpool, so serialise runs here. (replicas:1 + Recreate bounds it to one pod but
# does NOT serialise requests into it.)
_RUN_LOCK = threading.Lock()

# Scratch artefacts the coder's own tools drop into the checkout (run_tests ->
# _spec_test.py, run_python -> _run.py, write_code -> agent_code/). They are the
# agent's footprint, not the repo change: they must never be staged into the PR, and
# on their own they must not satisfy the "coder produced a change" gate (a run that
# only explores would otherwise publish a PR of pure detritus).
_AGENT_SCRATCH = ("_spec_test.py", "_run.py", "agent_code/",
                  "learned_policy.json", "_selftest_policy.json")

# In-process store of recent runs' transcripts, keyed by review branch, so the
# presentation console can replay 'the coder in action' (fetched via GET /runs).
_RUN_STORE: "collections.OrderedDict[str, dict]" = collections.OrderedDict()
_RUN_STORE_MAX = 20


def _store_run(rec: dict) -> None:
    # Key by branch for a published run, else by timestamp so FAILED runs (no branch)
    # are still retained — those are often the ones most worth inspecting.
    key = rec.get("branch") or rec.get("ts")
    if not key:
        return
    _RUN_STORE[key] = rec
    while len(_RUN_STORE) > _RUN_STORE_MAX:
        _RUN_STORE.popitem(last=False)


def _store_failed(kind: str, task: str, ts: str, tests_str: str, summary: str,
                  reason: str, collector, checkout: str) -> None:
    """Retain a failed run's transcript (keyed by ts, no branch) so GET /runs can
    inspect it — a red or no-change run is often the one most worth reading."""
    _store_run({"kind": kind, "task": task, "branch": None, "ts": ts,
                "outcome": "failed", "tests": tests_str, "summary": summary,
                "reason": reason, "pr_url": None,
                "steps": list(collector.steps), "diff": _diff(checkout)})


def get_run(branch: str) -> Optional[dict]:
    return _RUN_STORE.get(branch)


def list_runs() -> list[str]:
    return list(_RUN_STORE.keys())


def latest_run() -> Optional[dict]:
    return next(reversed(_RUN_STORE.values())) if _RUN_STORE else None


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
    # Full (not shallow) clone: a shallow history can't push a branch back over
    # git:// receive-pack ("shallow update not allowed"). The sandbox is tiny.
    subprocess.run(["git", "clone", url, dest],
                   check=True, capture_output=True, text=True)
    return dest


def _run_repo_tests(checkout: str, settings: Settings) -> dict:
    # The repo suite runs MODEL-MODIFIED code — scrub credentials from its env so a
    # malicious test can't read the pod's secrets (see coding_agent.sandbox_env).
    p = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=checkout,
                       capture_output=True, text=True, timeout=settings.test_timeout,
                       env=ca.sandbox_env())
    out = (p.stdout + p.stderr)
    mp, mf = re.search(r"(\d+) passed", out), re.search(r"(\d+) failed", out)
    me = re.search(r"(\d+) error", out)
    errors = int(me.group(1)) if me else 0
    failed = int(mf.group(1)) if mf else 0
    # A collection/import error yields no "N failed"; don't let a broken suite read as
    # 0f (which looks benign in the console). Reflect the error count, min 1 on any
    # non-zero exit. The gate stays all_passed (returncode == 0), so this is display-only.
    if p.returncode != 0 and failed == 0:
        failed = errors or 1
    return {"all_passed": p.returncode == 0,
            "passed": int(mp.group(1)) if mp else 0,
            "failed": failed,
            "errors": errors,
            "stdout": out[-4000:]}


def _run_agent(task: str, feedback: str, checkout: str, settings: Settings,
               collector=None) -> None:
    """One agentic pass: let the coder read the failing tests and edit repo files
    in place using its tools. Uses the ported build_agent_graph over TOOLS_BASE.
    If `collector` is given (a TranscriptCollector) it records the session steps.

    A single pass that doesn't converge within the recursion budget must NOT crash
    the request: LangGraph raises GraphRecursionError when the ReAct loop runs long.
    The coder's edits are persisted to disk by write_file as it goes, so we swallow
    a non-terminating pass and let run_code_task's test-gate + change-check decide
    (green+changed -> publish; otherwise a clean `failed`, never a 500)."""
    ca.set_workspace(checkout)
    graph = ca.build_agent_graph(ca.TOOLS_BASE, system=ca.CODER_SYSTEM_PROMPT, role="fast")
    prompt = (
        f"TASK ({task}).\n\nYou are working inside an existing Python repo at the "
        "workspace root. The repo has a pytest suite. Read the relevant files and "
        "the failing test, then EDIT the repo's source files in place using "
        "write_file to make `python -m pytest -q` pass. Do not create a new "
        "solution file; fix the real files. Verify with run_tests/bash as you go. "
        "Work efficiently: make the edit, run the tests once to confirm green, then "
        "STOP — do not keep exploring after the suite passes.\n\n"
        f"CURRENT TEST OUTPUT (verbatim ground truth):\n{feedback[:2000]}")
    cfg = ca.run_config("code-task", recursion_limit=6 * ca.MAX_ITERATIONS)
    if collector is not None:
        cfg["callbacks"] = list(cfg.get("callbacks", [])) + [collector]
    try:
        graph.invoke({"messages": [HumanMessage(prompt)]}, config=cfg)
    except GraphRecursionError:
        log.warning("agentic pass hit the recursion limit without a stop condition; "
                    "leaving edits on disk for the test-gate to judge")


def _diff(checkout: str) -> str:
    """The coder's change as a unified diff, for the console. Call AFTER the branch
    is committed: origin/main..HEAD is the delegated change (baseline → branch tip).
    Falls back to the working-tree diff vs HEAD (a not-yet-committed change), never
    to `git show HEAD`, which would dump the whole baseline commit."""
    p = subprocess.run(["git", "diff", "origin/main..HEAD", "--", "."], cwd=checkout,
                       capture_output=True, text=True)
    out = p.stdout or ""
    if not out.strip():
        p = subprocess.run(["git", "diff", "HEAD", "--", "."], cwd=checkout,
                           capture_output=True, text=True)
        out = p.stdout or ""
    return out[:20000]


def _git_publish(checkout: str, branch: str, title: str, body: str,
                 settings: Settings) -> str:
    """Commit the coder's work on a review branch and publish it. In 'local' mode
    the branch is pushed back to the local bare sandbox repo (origin is a file://
    path) and the returned 'pr_url' is a local branch reference — no GitHub, no gh.
    In 'github' mode it additionally opens a gated PR with `gh pr create`."""
    env = dict(os.environ)
    if settings.gh_token:
        env["GH_TOKEN"] = settings.gh_token

    def run(args):
        return subprocess.run(args, cwd=checkout, check=True,
                              capture_output=True, text=True, env=env)

    run(["git", "checkout", "-b", branch])
    # Stage new + modified + deleted, but EXCLUDE the agent's scratch footprint so the
    # PR is the repo change, not _spec_test.py/_run.py/agent_code/. Robust even when the
    # sandbox repo carries no .gitignore for them (github mode). Pathspec magic prefix
    # ':(exclude)' drops each from an otherwise-everything add.
    excludes = [f":(exclude){s.rstrip('/')}" for s in _AGENT_SCRATCH]
    run(["git", "add", "-A", "--", ".", *excludes])
    run(["git", "-c", "user.email=coder@nano.bank", "-c", "user.name=nano-bank coder",
         "commit", "-m", title])
    run(["git", "push", "-u", "origin", branch])
    if settings.sandbox_mode == "local":
        # The review artifact is the pushed branch in the local bare repo. A human
        # reviews it with `git diff <base>..<branch>` and merges by hand.
        return f"{branch} @ {settings.sandbox_clone_url}"
    r = run(["gh"] + git_ops.pr_create_args(head=branch, base=settings.pr_base,
                                            title=title, body=body))
    return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""


def default_seams() -> Seams:
    return Seams(clone=_clone, run_agent=_run_agent, run_repo_tests=_run_repo_tests,
                 git_publish=_git_publish, now=_now)


# --- orchestration -----------------------------------------------------------

def run_code_task(kind: str, task: str, *, settings: Settings,
                  seams: Optional[Seams] = None) -> dict:
    # Serialise: one run at a time drives the module-global workspace (see _RUN_LOCK).
    with _RUN_LOCK:
        return _run_code_task_locked(kind, task, settings=settings, seams=seams)


def _run_code_task_locked(kind: str, task: str, *, settings: Settings,
                          seams: Optional[Seams] = None) -> dict:
    seams = seams or default_seams()
    ts = seams.now()
    collector = ca.TranscriptCollector()
    work = tempfile.mkdtemp(prefix="coder-", dir=_ensure_root(settings))
    checkout = os.path.join(work, "repo")
    started = time.monotonic()
    try:
        checkout = seams.clone(settings, checkout) or checkout
        tests = seams.run_repo_tests(checkout, settings)          # baseline (model's context)
        # Always run the coder at least once — the TASK drives the work, not the
        # test colour. A delivery task's contract may be a skipped/xfail test, so
        # the baseline can be green; the model must still make (and verify) a change.
        rounds = 0
        changed = False
        deadline_hit = False
        while rounds < _MAX_ROUNDS:
            rounds += 1
            seams.run_agent(task, tests["stdout"], checkout, settings, collector)
            tests = seams.run_repo_tests(checkout, settings)
            changed = _has_changes(checkout)
            if tests["all_passed"] and changed:
                break
            if time.monotonic() - started > _RUN_DEADLINE_S:
                deadline_hit = True
                log.warning("run exceeded the %.0fs deadline after round %d; stopping",
                            _RUN_DEADLINE_S, rounds)
                break
        summary = f"{kind}: {task[:120]}"
        tests_str = f"{tests['passed']}p/{tests['failed']}f"
        if not changed:
            reason = ("run deadline exceeded before any change" if deadline_hit
                      else "coder produced no change")
            _store_failed(kind, task, ts, tests_str, summary, reason, collector, checkout)
            return git_ops.code_task_result(
                "failed", tests=tests_str, summary=summary, reason=reason)
        if not tests["all_passed"]:
            reason = ("run deadline exceeded before tests passed" if deadline_hit
                      else "repo tests still red after coder rounds")
            _store_failed(kind, task, ts, tests_str, summary, reason, collector, checkout)
            return git_ops.code_task_result(
                "failed", tests=tests_str, summary=summary, reason=reason)
        branch = git_ops.branch_slug(task, ts)
        body = (f"Delegated by the Agent CTO (kind: {kind}).\n\nTask: {task}\n\n"
                "Authored by the coder against the sandbox; repo tests are green. "
                "PR-gated — a human reviews and merges.")
        pr_url = seams.git_publish(checkout, branch, title=summary, body=body,
                                   settings=settings)
        diff = _diff(checkout)                    # AFTER commit: origin/main..HEAD is the change
        _store_run({"kind": kind, "task": task, "branch": branch, "outcome": "executed",
                    "tests": tests_str, "summary": summary, "pr_url": pr_url or None,
                    "steps": list(collector.steps), "diff": diff, "ts": ts})
        return git_ops.code_task_result(
            "executed", pr_url=pr_url or None, branch=branch,
            tests=tests_str, summary=summary)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _has_changes(checkout: str) -> bool:
    """True iff the coder left a REAL repo change — the agent's own scratch files
    (_AGENT_SCRATCH) don't count. Without this filter a run that only explored (it
    called run_tests once, edited nothing) leaves _spec_test.py behind, which reads as
    'the coder produced a change' and publishes a PR of pure detritus — exactly the
    case the no-change -> failed gate exists to catch."""
    p = subprocess.run(["git", "status", "--porcelain"], cwd=checkout,
                       capture_output=True, text=True)
    for line in p.stdout.splitlines():
        # porcelain v1: 2 status chars + space, then the path (quoted if unusual).
        path = line[3:].strip().strip('"')
        if path and not path.startswith(_AGENT_SCRATCH):
            return True
    return False


def _ensure_root(settings: Settings) -> str:
    Path(settings.workspace_root).mkdir(parents=True, exist_ok=True)
    return settings.workspace_root
