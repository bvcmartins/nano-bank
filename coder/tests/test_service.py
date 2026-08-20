import re
import subprocess
from pathlib import Path

from coder.config import Settings
from coder import service as svc


def _init_repo(root: Path) -> Path:
    """A tiny real git repo whose test fails until helper.py is fixed."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "helper.py").write_text("def dbl(n):\n    return n + n + 1  # bug\n")
    (root / "test_helper.py").write_text(
        "from helper import dbl\n\n\ndef test_dbl():\n    assert dbl(2) == 4\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "baseline"], cwd=root, check=True)
    return root


def _seams(checkout, publish_calls, *, agent_fixes: bool):
    def clone(settings, dest):
        return str(checkout)

    def run_agent(task, feedback, co, settings, collector=None):
        if agent_fixes:  # stand in for the model editing the repo file
            (Path(co) / "helper.py").write_text("def dbl(n):\n    return n + n\n")

    def run_repo_tests(co, settings):
        p = subprocess.run(["python", "-m", "pytest", "-q"], cwd=co,
                           capture_output=True, text=True)
        out = p.stdout + p.stderr
        mp = re.search(r"(\d+) passed", out)
        return {"all_passed": p.returncode == 0,
                "passed": int(mp.group(1)) if mp else 0,
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
                            seams=_seams(checkout, calls, agent_fixes=True))
    assert res["outcome"] == "executed"
    assert res["pr_url"].endswith("/pull/1")
    assert res["branch"].startswith("cto/")
    assert len(calls) == 1                       # published exactly once


def test_red_makes_no_pr(tmp_path):
    checkout = _init_repo(tmp_path / "repo")
    calls = []
    s = Settings.from_env({})
    res = svc.run_code_task("delivery", "make dbl double", settings=s,
                            seams=_seams(checkout, calls, agent_fixes=False))
    assert res["outcome"] == "failed"
    assert res["pr_url"] is None
    assert calls == []                           # never published on red


def test_git_publish_local_pushes_branch_no_github(tmp_path):
    """Real git, no network/gh: local mode publishes a review branch to the bare
    sandbox repo and returns a local ref (not an https URL)."""
    bare = tmp_path / "cto-sandbox.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True)
    # seed the bare repo with a main branch
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "helper.py").write_text("x = 1\n")
    for args in (["git", "init", "-q"], ["git", "add", "-A"],
                 ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"],
                 ["git", "branch", "-M", "main"],
                 ["git", "remote", "add", "origin", str(bare)],
                 ["git", "push", "-q", "-u", "origin", "main"]):
        subprocess.run(args, cwd=seed, check=True)
    # a fresh checkout the coder "worked in"
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "clone", "-q", str(bare), str(checkout)], check=True)
    (checkout / "helper.py").write_text("x = 2  # coder edit\n")

    s = Settings.from_env({"SANDBOX_MODE": "local", "SANDBOX_CLONE_URL": str(bare)})
    ref = svc._git_publish(str(checkout), "cto/fix-T", "fix: bump", "body", s)

    assert not ref.startswith("http")               # local ref, not a GitHub URL
    assert "cto/fix-T" in ref
    branches = subprocess.run(["git", "branch"], cwd=bare, capture_output=True, text=True).stdout
    assert "cto/fix-T" in branches                   # the branch really landed in the bare repo


def test_scratch_files_alone_do_not_count_as_a_change(tmp_path):
    """Finding 9: a run that only explored (dropped the coder's own scratch files,
    edited nothing) must hit the no-change gate — not publish a PR of agent detritus."""
    checkout = _init_repo(tmp_path / "repo")
    calls = []

    def clone(settings, dest):
        return str(checkout)

    def run_agent(task, feedback, co, settings, collector=None):
        (Path(co) / "_spec_test.py").write_text("def test_noop():\n    assert True\n")
        (Path(co) / "_run.py").write_text("print('scratch')\n")
        (Path(co) / "agent_code").mkdir(exist_ok=True)
        (Path(co) / "agent_code" / "draft.py").write_text("x = 1\n")

    def run_repo_tests(co, settings):
        return {"all_passed": True, "passed": 1, "failed": 0, "stdout": "1 passed"}

    def git_publish(co, branch, title, body, settings):
        calls.append(branch)
        return "should-not-happen"

    seams = svc.Seams(clone=clone, run_agent=run_agent, run_repo_tests=run_repo_tests,
                      git_publish=git_publish, now=lambda: "T")
    res = svc.run_code_task("delivery", "explore only", settings=Settings.from_env({}),
                            seams=seams)
    assert res["outcome"] == "failed"
    assert "no change" in res["reason"]
    assert calls == []


def test_git_publish_excludes_agent_scratch(tmp_path):
    """Finding 9: _git_publish stages the real change but NOT the agent's scratch
    footprint, even when the repo carries no .gitignore for it."""
    bare = tmp_path / "s.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True)
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "helper.py").write_text("x = 1\n")
    for args in (["git", "init", "-q"], ["git", "add", "-A"],
                 ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"],
                 ["git", "branch", "-M", "main"],
                 ["git", "remote", "add", "origin", str(bare)],
                 ["git", "push", "-q", "-u", "origin", "main"]):
        subprocess.run(args, cwd=seed, check=True)
    checkout = tmp_path / "co"
    subprocess.run(["git", "clone", "-q", str(bare), str(checkout)], check=True)
    (checkout / "helper.py").write_text("x = 2  # real edit\n")
    (checkout / "_spec_test.py").write_text("assert True\n")          # scratch
    (checkout / "_run.py").write_text("print(1)\n")                   # scratch
    (checkout / "agent_code").mkdir()
    (checkout / "agent_code" / "d.py").write_text("y = 1\n")         # scratch

    s = Settings.from_env({"SANDBOX_MODE": "local", "SANDBOX_CLONE_URL": str(bare)})
    svc._git_publish(str(checkout), "cto/x-T", "t", "b", s)
    files = subprocess.run(["git", "ls-tree", "-r", "--name-only", "cto/x-T"],
                           cwd=bare, capture_output=True, text=True).stdout
    assert "helper.py" in files                       # the real change landed
    assert "_spec_test.py" not in files               # scratch did not
    assert "_run.py" not in files
    assert "agent_code/d.py" not in files


def test_run_is_stored_with_transcript_and_diff(tmp_path):
    """An executed task records a run (keyed by branch) the console can fetch: it
    carries the transcript steps + the diff + task/kind."""
    checkout = _init_repo(tmp_path / "repo")
    s = Settings.from_env({})
    res = svc.run_code_task("delivery", "make dbl double", settings=s,
                            seams=_seams(checkout, [], agent_fixes=True))
    run = svc.get_run(res["branch"])
    assert run is not None
    assert run["kind"] == "delivery" and run["branch"] == res["branch"]
    assert "steps" in run and isinstance(run["steps"], list)
    assert "diff" in run and isinstance(run["diff"], str)
    assert res["branch"] in svc.list_runs()


def test_transcript_collector_records_reasoning_and_tools():
    tc = svc.ca.TranscriptCollector()
    tc.on_tool_start({"name": "write_file"}, "rounding.py: ...", run_id="r1")
    tc.on_tool_end("wrote 42 bytes", run_id="r1")
    kinds = [s["type"] for s in tc.steps]
    assert kinds == ["tool"]
    assert tc.steps[0]["name"] == "write_file"
    assert tc.steps[0]["output"].startswith("wrote 42")


def test_green_baseline_still_runs_model_and_publishes(tmp_path):
    """The delivery-task bug: baseline is GREEN (skipped/xfail contract), so the
    model must still run and its change must be published."""
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    (root / "helper.py").write_text("def dbl(n):\n    return n + n\n")
    (root / "test_helper.py").write_text(
        "from helper import dbl\n\n\ndef test_dbl():\n    assert dbl(2) == 4\n")  # passes at baseline
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "base"], cwd=root, check=True)
    calls = []
    ran = {"n": 0}

    def clone(settings, dest):
        return str(root)

    def run_agent(task, feedback, co, settings, collector=None):
        ran["n"] += 1                              # model adds a new helper (green stays green)
        (Path(co) / "helper.py").write_text(
            "def dbl(n):\n    return n + n\n\n\ndef fee():\n    return 150\n")

    def run_repo_tests(co, settings):
        p = subprocess.run(["python", "-m", "pytest", "-q"], cwd=co,
                           capture_output=True, text=True)
        return {"all_passed": p.returncode == 0, "passed": 1, "failed": 0,
                "stdout": p.stdout + p.stderr}

    def git_publish(co, branch, title, body, settings):
        calls.append(branch)
        return f"{branch} @ file:///sandbox"

    seams = svc.Seams(clone=clone, run_agent=run_agent, run_repo_tests=run_repo_tests,
                      git_publish=git_publish, now=lambda: "T")
    res = svc.run_code_task("delivery", "add fee", settings=Settings.from_env({}), seams=seams)
    assert res["outcome"] == "executed"
    assert ran["n"] >= 1                            # model ran even though baseline was green
    assert len(calls) == 1
