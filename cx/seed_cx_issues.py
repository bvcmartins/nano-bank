# cx/seed_cx_issues.py — deterministic, as-if-personal-manager cx_issues seeder.
#
# Calibrated, not just populated: demos/10-ceo/debate.py's own scripted premise
# is that recurring e-Transfers "is the top customer feature request" and the
# CXO — the officer who effectively tables that premise — reasons live off
# this data. A uniform-random category draw and an all-open issue backlog
# both silently contradict that premise (feature_request ties or loses the
# plurality; every active customer looks like a live detractor), which is
# what produced a CXO NAY against its own motion. The fix is to make the
# seed *support* the story the demo tells, the same way
# cfo/demo/seed-demo-bank.sh is calibrated to specific ratios rather than
# just "some balances."
from __future__ import annotations
import random

_CATS = ["onboarding", "declines_friction", "fees", "rail_experience", "app_ux",
         "feature_request", "other"]
# feature_request weighted 3x the rest so it's the clear plurality category —
# the debate's motion literally is "it is the top customer feature request."
_CAT_WEIGHTS = [1, 1, 1, 1, 1, 3, 1]
_SEVS = ["low", "low", "medium", "medium", "high", "urgent"]  # weighted toward low/medium
# cx/metrics.py's issue_summary() computes top_theme from OPEN (non-resolved)
# issues only — so a category weighted heavily in *total* volume but resolved
# at the same rate as everything else washes back out to a tie, which is
# exactly what happened here (feature_request was 11/40 filed but only 1/10
# still open, so declines_friction read as the "top theme" instead). A
# feature request also isn't "resolved" the way a bug is — it stays open
# until the feature ships — so keeping it mostly open is realistic, not just
# convenient: resolve real friction (onboarding/declines/fees/etc.) at a high
# rate, but leave feature_request mostly outstanding so it's the plurality
# among *open* issues too, not just ever-filed ones.
_RESOLVE_PROB = {"feature_request": 0.25}  # everything else defaults to 0.8
_DEFAULT_RESOLVE_PROB = 0.8
_NOT_RESOLVED_STATUSES = ["acknowledged", "acknowledged", "open"]  # 2:1, both count as "open"
_SUMMARIES = {
    "onboarding": "KYC took too long to clear",
    "declines_friction": "card declined at checkout despite funds",
    "fees": "surprised by the monthly fee",
    "rail_experience": "e-Transfer expired before the payee claimed it",
    "app_ux": "couldn't find the autodeposit setting",
    "feature_request": "wants recurring e-Transfers",
    "other": "general dissatisfaction with support wait time"}


def build_issue_rows(customer_ids: list[str], n: int = 40, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        cat = rng.choices(_CATS, weights=_CAT_WEIGHTS, k=1)[0]
        sev = rng.choice(_SEVS)
        resolve_prob = _RESOLVE_PROB.get(cat, _DEFAULT_RESOLVE_PROB)
        status = "resolved" if rng.random() < resolve_prob else rng.choice(_NOT_RESOLVED_STATUSES)
        created_offset = rng.randint(0, 29)
        # A resolved issue must resolve strictly after it was filed, and
        # within the seed window (not in the future).
        resolved_offset = rng.randint(0, created_offset) if status == "resolved" else None
        out.append({"customer_id": rng.choice(customer_ids), "category": cat,
                    "severity": sev, "summary": _SUMMARIES[cat],
                    "detail": f"{_SUMMARIES[cat]} (case {i}).",
                    "status": status,
                    "created_at_offset_days": created_offset,
                    "resolved_at_offset_days": resolved_offset})
    return out


def seed(db_params: dict, n: int = 40, seed_val: int = 7) -> int:
    import psycopg2
    conn = psycopg2.connect(**db_params)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT customer_id::text FROM customers LIMIT 200")
            ids = [r[0] for r in cur.fetchall()]
            if not ids:
                raise RuntimeError("no customers to attach issues to — seed the bank first")
            # 'demo_seed' — never 'personal_manager', which is the tag the
            # production write path (agent/db.py::insert_cx_issue) stamps on
            # every genuine customer filing. Deleting by that tag would wipe
            # real complaints in any environment that has taken live filings.
            cur.execute("DELETE FROM cx_issues WHERE source = 'demo_seed'")
            rows = build_issue_rows(ids, n=n, seed=seed_val)
            for r in rows:
                resolved_offset = r["resolved_at_offset_days"]
                cur.execute(
                    "INSERT INTO cx_issues (customer_id, category, severity, summary, detail,"
                    " status, source, created_at, resolved_at) VALUES (%s,%s,%s,%s,%s,%s,"
                    "'demo_seed', now() - (%s || ' days')::interval,"
                    " CASE WHEN %s IS NULL THEN NULL ELSE now() - (%s || ' days')::interval END)",
                    (r["customer_id"], r["category"], r["severity"], r["summary"],
                     r["detail"], r["status"], r["created_at_offset_days"],
                     resolved_offset, resolved_offset))
            return len(rows)
    finally:
        conn.close()


if __name__ == "__main__":
    from .config import Settings
    print("seeded", seed(Settings.from_env().db), "cx_issues")
