"""Read the tamper-evident agent_action_ledger for the presentation console.
Reads straight from Postgres in the kind cluster via `kubectl exec` (no host DB
driver), mirroring demos/08-cto/inspect-ledger.sh. The pure parsers are unit
tested; the subprocess wrappers are exercised by the live smoke."""
from __future__ import annotations
import subprocess

CTX = "kind-nano-bank"
NS = "nano-bank"
_FIELDS = ["seq", "ts", "actor", "action", "outcome", "deployment", "detail", "prev", "entry"]

_ROWS_SQL = """
SELECT seq, to_char(ts,'YYYY-MM-DD HH24:MI:SS'), actor, action,
       COALESCE(effect->>'outcome','—'),
       COALESCE(params->>'deployment',''),
       COALESCE(effect->'effect'->>'rolled_back_to',
                effect->'effect'->>'restarted_at',
                effect->>'reason',''),
       left(prev_hash,10), left(entry_hash,10)
FROM agent_action_ledger ORDER BY seq;
"""


def parse_rows(psql_text: str) -> list[dict]:
    rows = []
    for line in psql_text.splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        parts += [""] * (len(_FIELDS) - len(parts))
        rows.append(dict(zip(_FIELDS, parts)))
    return rows


def parse_verdict(psql_text: str) -> tuple[str, int | None]:
    s = psql_text.strip()
    return ("INTACT", None) if s == "" else ("TAMPERED", int(s))


def _pg_pod() -> str:
    return subprocess.run(
        ["kubectl", "--context", CTX, "-n", NS, "get", "pod", "-l", "app=postgres",
         "-o", "jsonpath={.items[0].metadata.name}"],
        capture_output=True, text=True, check=True).stdout.strip()


def _psql(sql: str, pod: str | None = None) -> str:
    pod = pod or _pg_pod()
    return subprocess.run(
        ["kubectl", "--context", CTX, "-n", NS, "exec", "-i", pod, "--",
         "psql", "-U", "nanobank_user", "-d", "nano_bank_db", "-At", "-F", "|", "-c", sql],
        capture_output=True, text=True).stdout


def read_rows() -> list[dict]:
    return parse_rows(_psql(_ROWS_SQL))


def chain_verdict() -> tuple[str, int | None]:
    return parse_verdict(_psql("SELECT verify_agent_ledger();"))


def tamper_demo() -> dict:
    """Prove immutability: attempt an UPDATE and a DELETE; both must be rejected
    by the append-only trigger. Returns {update_rejected, delete_rejected}."""
    pod = _pg_pod()
    upd = _run_expect_reject(
        "UPDATE agent_action_ledger SET effect='{\"outcome\":\"tampered\"}' WHERE seq=1;", pod)
    dele = _run_expect_reject("DELETE FROM agent_action_ledger WHERE seq=1;", pod)
    return {"update_rejected": upd, "delete_rejected": dele}


def _run_expect_reject(sql: str, pod: str) -> bool:
    out = subprocess.run(
        ["kubectl", "--context", CTX, "-n", NS, "exec", "-i", pod, "--",
         "psql", "-U", "nanobank_user", "-d", "nano_bank_db", "-c", sql],
        capture_output=True, text=True)
    blob = (out.stdout + out.stderr).lower()
    return "append-only" in blob or "error" in blob
