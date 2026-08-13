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

    def run_agent(task, feedback, co, settings):
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
