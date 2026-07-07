import json
import httpx
import pytest
from agent.bank import BankClient, BankError


def _client(handler):
    transport = httpx.MockTransport(handler)
    return BankClient("http://bank.test", http=httpx.Client(transport=transport))


def test_transfer_sends_token_amount_and_idempotency():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization")
        seen["idem"] = req.headers.get("idempotency-key")
        seen["body"] = json.loads(req.content)
        return httpx.Response(201, json={"transaction_id": "t1"})

    bank = _client(handler)
    out = bank.transfer("jwt-abc", "acc-from", "acc-to", "50.00",
                        memo="rent", idempotency_key="act-1")
    assert out["transaction_id"] == "t1"
    assert seen["url"].endswith("/api/v1/transactions/transfer")
    assert seen["auth"] == "Bearer jwt-abc"
    assert seen["idem"] == "act-1"
    assert seen["body"]["amount"] == "50.00"


def test_non_2xx_raises_bankerror():
    bank = _client(lambda req: httpx.Response(422, json={"error": {"message": "insufficient"}}))
    with pytest.raises(BankError) as ei:
        bank.deposit("jwt", "acc", "10")
    assert ei.value.status == 422
