from __future__ import annotations
from typing import Optional
import httpx


class BankError(Exception):
    def __init__(self, status: int, body):
        super().__init__(f"nano-bank {status}: {body}")
        self.status = status
        self.body = body


class BankClient:
    def __init__(self, base_url: str, http: Optional[httpx.Client] = None):
        self.base = base_url.rstrip("/")
        self.http = http or httpx.Client(timeout=30)

    def _post(self, path: str, json: dict, token: Optional[str] = None,
              idempotency_key: Optional[str] = None) -> dict:
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        r = self.http.post(self.base + path, json=json, headers=headers)
        if r.status_code // 100 != 2:
            raise BankError(r.status_code, _safe_json(r))
        return _safe_json(r)

    def login(self, email: str, password: str) -> str:
        out = self._post("/api/v1/auth/login", {"email": email, "password": password})
        return out.get("access_token") or out["token"]

    def deposit(self, token, account_id, amount, idempotency_key=None) -> dict:
        return self._post("/api/v1/transactions/deposit",
                          {"account_id": account_id, "amount": str(amount)},
                          token=token, idempotency_key=idempotency_key)

    def withdraw(self, token, account_id, amount, idempotency_key=None) -> dict:
        return self._post("/api/v1/transactions/withdrawal",
                          {"account_id": account_id, "amount": str(amount)},
                          token=token, idempotency_key=idempotency_key)

    def transfer(self, token, from_account, to_account, amount, memo=None,
                 idempotency_key=None) -> dict:
        body = {"from_account_id": from_account, "to_account_id": to_account,
                "amount": str(amount)}
        if memo:
            body["memo"] = memo
        return self._post("/api/v1/transactions/transfer", body,
                          token=token, idempotency_key=idempotency_key)

    def create_customer(self, payload: dict) -> dict:
        return self._post("/api/v1/customers", payload)

    def create_account(self, token, payload: dict) -> dict:
        return self._post("/api/v1/accounts", payload, token=token)


def _safe_json(r: httpx.Response):
    try:
        return r.json()
    except Exception:  # noqa: BLE001
        return {"raw": r.text}
