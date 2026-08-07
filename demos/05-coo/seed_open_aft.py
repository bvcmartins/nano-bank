#!/usr/bin/env python3
"""Leave ONE open outbound AFT batch with an entry, so the COO's lever beat has
something real to cut. DEMO/TEST-ONLY — like the rest of run-demo.sh's seeding,
it runs from the host against the port-forwarded bank, never from an app process.

Registers a throwaway funded customer and originates a single AFT credit; the
credit accrues into the shared open outbound batch and is NOT cut here — that is
exactly the action we want the COO to take autonomously.

    API_URL=http://localhost:8081 python demos/05-coo/seed_open_aft.py
"""
import json
import os
import random
import sys
import urllib.error
import urllib.request

API = os.environ.get("API_URL", "http://localhost:8081")


def call(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("content-type", "application/json")
    if token:
        req.add_header("authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:200]}


def main() -> int:
    n = random.randint(1000, 9_999_999)
    pw = "Passw0rd!demo"
    email = f"coo.lever.{n}@example.com"
    sin = "".join(str(random.randint(0, 9)) for _ in range(9))

    st, _ = call("POST", "/api/v1/customers", {
        "email": email, "phone_number": f"+1514555{random.randint(1000, 9999)}",
        "first_name": "Coo", "last_name": "Lever", "password": pw,
        "date_of_birth": "1990-01-01", "sin": sin})
    if st != 201:
        print(f"  ✗ register {st}"); return 1

    st, tok = call("POST", "/api/v1/auth/login", {"email": email, "password": pw})
    if st != 200:
        print(f"  ✗ login {st}"); return 1
    token = tok["access_token"]

    st, acct = call("POST", "/api/v1/accounts", {"account_type": "chequing"}, token=token)
    if st != 201:
        print(f"  ✗ open account {st}"); return 1
    account_id = acct["account_id"]

    st, _ = call("POST", "/api/v1/transactions/deposit", {
        "account_id": account_id, "amount": "5000.00",
        "description": "seed funds for the COO lever demo"}, token=token)
    if st != 201:
        print(f"  ✗ deposit {st}"); return 1

    st, credit = call("POST", "/api/v1/aft/credits", {
        "originator_account_id": account_id, "amount": "1234.56",
        "counterparty_institution": "004", "counterparty_transit": "12345",
        "counterparty_account": "987654321", "payee_name": "Acme Payroll"}, token=token)
    if st != 201:
        print(f"  ✗ aft credit {st}: {credit.get('error','')}"); return 1

    print(f"  ✓ open outbound AFT batch {credit['batch_id'][:8]} has an entry "
          f"(${credit['amount']}) awaiting a cutoff — the COO will cut it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
