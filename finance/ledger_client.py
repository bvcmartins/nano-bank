from __future__ import annotations
import httpx


def get_balances(base_url: str) -> list:
    r = httpx.get(f"{base_url}/api/v1/ledger/balances", timeout=10.0)
    r.raise_for_status()
    return r.json()
