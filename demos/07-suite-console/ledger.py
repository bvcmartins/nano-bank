"""Read the tamper-evident agent-action ledger for the console's live panel.

Reads straight from Postgres in the kind cluster via `kubectl exec` (the same
password-free path as demos/05-coo/inspect-ledger.sh) — no DB driver, no
port-forward, no credentials in the demo. Returns plain dicts for Streamlit."""
from __future__ import annotations
import os
import subprocess

CTX = os.environ.get("KCTX", "kind-nano-bank")
NS = os.environ.get("KNS", "nano-bank")

_ROWS_SQL = (
    "SELECT seq, to_char(ts,'HH24:MI:SS'), actor, action, "
    "COALESCE(effect->>'outcome','—'), "
    "COALESCE(effect->'effect'->>'batch_id', effect->>'reason', "
    "  CASE WHEN effect ? 'roles_captured' THEN 'roles='||(effect->>'roles_captured') END, ''), "
    "left(prev_hash,10), left(entry_hash,10) "
    "FROM agent_action_ledger ORDER BY seq;"
)
_VERIFY_SQL = "SELECT COALESCE(verify_agent_ledger()::text,'');"


def _psql(sql: str) -> tuple[bool, str]:
    try:
        out = subprocess.run(
            ["kubectl", "--context", CTX, "-n", NS, "exec", "-i", "deploy/postgres",
             "--", "psql", "-U", "nanobank_user", "-d", "nano_bank_db",
             "-At", "-F", "|", "-c", sql],
            capture_output=True, text=True, timeout=15)
        if out.returncode != 0:
            return False, out.stderr.strip()
        return True, out.stdout
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def fetch() -> dict:
    """{'ok', 'rows': [ {seq,ts,actor,action,outcome,detail,prev,hash} ], 'intact',
    'broken_seq', 'error'}."""
    ok, raw = _psql(_ROWS_SQL)
    if not ok:
        return {"ok": False, "rows": [], "intact": None, "broken_seq": None,
                "error": raw}
    rows = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) < 8:
            continue
        seq, ts, actor, action, outcome, detail, prev, h = parts[:8]
        rows.append({"seq": int(seq), "ts": ts, "actor": actor, "action": action,
                     "outcome": outcome, "detail": detail, "prev": prev, "hash": h})
    vok, vraw = _psql(_VERIFY_SQL)
    broken = vraw.strip() if vok else ""
    return {"ok": True, "rows": rows,
            "intact": (broken == "") if vok else None,
            "broken_seq": (int(broken) if broken.strip().isdigit() else None),
            "error": None}
