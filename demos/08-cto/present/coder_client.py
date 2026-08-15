"""Fetch the coder's per-run transcript for the presentation console. Mirrors
ledger.py: no host port-forward — we `kubectl exec` into the coder pod and let it
curl its own :8096 (the coder image ships python, not curl). Used at capture time
to attach 'the coder in action' to the delegation beats so replay can show it."""
from __future__ import annotations
import json
import subprocess

CTX = "kind-nano-bank"
NS = "nano-bank"

_FETCH = (
    "import urllib.request,sys,json;"
    "u='http://localhost:8096/runs/'+urllib.parse.quote(sys.argv[1]);"
    "sys.stdout.write(urllib.request.urlopen(u,timeout=15).read().decode())"
)
_FETCH = "import urllib.parse;" + _FETCH


def fetch_run(branch: str, *, ctx: str = CTX, ns: str = NS) -> dict | None:
    """The stored run {kind,task,branch,steps,diff,tests,outcome} for a review
    branch, or None if the coder has no such run (or isn't reachable)."""
    if not branch:
        return None
    p = subprocess.run(
        ["kubectl", "--context", ctx, "-n", ns, "exec", "deploy/coder", "--",
         "python", "-c", _FETCH, branch],
        capture_output=True, text=True)
    out = (p.stdout or "").strip()
    if not out:
        return None
    try:
        rec = json.loads(out)
    except json.JSONDecodeError:
        return None
    return rec or None
