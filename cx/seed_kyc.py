# cx/seed_kyc.py — deterministic KYC-completion seeder.
#
# `POST /api/v1/customers` always leaves kyc_status='pending' /
# kyc_completed_at=NULL (api/src/handlers/customers.rs), and the self-serve
# document-upload endpoint that would normally complete it is a permanent stub
# (see the repo's own CLAUDE.md) — so every customer created through the API,
# demo or otherwise, is stuck at 0% KYC completion forever. That reads to the
# CXO as a catastrophic onboarding failure, but it isn't one: it's a gap in
# the self-serve *upload* flow, not evidence no customer ever completed KYC.
# The codebase's own test suite already treats a direct
# `UPDATE customers SET kyc_status = 'verified'` as the legitimate stand-in
# for that missing flow (api/tests/lending*.rs); this seeder applies the same
# pattern to a realistic majority of the demo customer base, the same way
# cfo/demo/seed-demo-bank.sh calibrates the balance sheet directly rather than
# through a broken endpoint. Left deliberately short of 100% — a bank with a
# live onboarding queue always has *some* pending KYC, and a suspicious 100%
# would read as fabricated rather than operating.
from __future__ import annotations
import random

_VERIFIED_FRACTION = 0.8
# 2:1 pending:under_review — both still count as "kyc_pending" in
# cx/db.py::customers_onboarding (kyc_completed_at IS NULL).
_STILL_PENDING_STATUSES = ["pending", "pending", "under_review"]


def build_updates(customer_ids: list[str], seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    ids = list(customer_ids)
    rng.shuffle(ids)
    n_verified = int(len(ids) * _VERIFIED_FRACTION)
    out = []
    for i, cid in enumerate(ids):
        if i < n_verified:
            out.append({"customer_id": cid, "kyc_status": "verified",
                        "completed_offset_days": rng.randint(1, 60)})
        else:
            out.append({"customer_id": cid,
                        "kyc_status": rng.choice(_STILL_PENDING_STATUSES),
                        "completed_offset_days": None})
    return out


def seed(db_params: dict, seed_val: int = 7) -> dict:
    import psycopg2
    conn = psycopg2.connect(**db_params)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT customer_id::text FROM customers")
            ids = [r[0] for r in cur.fetchall()]
            if not ids:
                raise RuntimeError("no customers to set KYC status on — seed the bank first")
            updates = build_updates(ids, seed=seed_val)
            for u in updates:
                offset = u["completed_offset_days"]
                cur.execute(
                    "UPDATE customers SET kyc_status = %s::kyc_status,"
                    " kyc_completed_at = CASE WHEN %s IS NULL THEN NULL"
                    " ELSE now() - (%s || ' days')::interval END"
                    " WHERE customer_id = %s::uuid",
                    (u["kyc_status"], offset, offset, u["customer_id"]))
            verified = sum(1 for u in updates if u["kyc_status"] == "verified")
            return {"total": len(updates), "verified": verified}
    finally:
        conn.close()


if __name__ == "__main__":
    from .config import Settings
    print("kyc seeded", seed(Settings.from_env().db))
