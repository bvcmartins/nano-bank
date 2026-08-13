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
