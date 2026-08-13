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
