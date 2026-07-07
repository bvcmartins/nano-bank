from decimal import Decimal
import pytest
from agent.actions import ActionStore, ActDenied, ActError


class FakeDB:
    def __init__(self, owned): self.owned = set(owned)
    def owns_account(self, customer_id, account_id): return account_id in self.owned


class FakeBank:
    def __init__(self): self.calls = []
    def transfer(self, token, from_account, to_account, amount, memo=None, idempotency_key=None):
        self.calls.append(("transfer", idempotency_key, str(amount)))
        return {"transaction_id": "t-" + idempotency_key}


class FakeAudit:
    def __init__(self): self.events = []
    def record(self, e): self.events.append(e); return "a"


def _store(**kw):
    clock = {"t": 1000.0}
    db = kw.get("db", FakeDB(["acc-from", "acc-to"]))
    bank = kw.get("bank", FakeBank())
    audit = kw.get("audit", FakeAudit())
    s = ActionStore(db, bank, audit, max_per_tx=Decimal("1000"), ttl_s=300,
                    now=lambda: clock["t"])
    return s, db, bank, audit, clock


def test_propose_over_cap_denied_and_audited():
    s, _db, _bank, audit, _clock = _store()
    with pytest.raises(ActDenied):
        s.propose("C", "tok", "transfer", amount="5000", from_account="acc-from",
                  to_account="acc-to")
    assert audit.events[-1]["outcome"] == "denied"


def test_propose_foreign_source_denied():
    s, *_ = _store()
    with pytest.raises(ActDenied):
        s.propose("C", "tok", "transfer", amount="10", from_account="acc-STRANGER",
                  to_account="acc-to")


def test_propose_does_not_move_money():
    s, _db, bank, _audit, _clock = _store()
    out = s.propose("C", "tok", "transfer", amount="50", from_account="acc-from",
                    to_account="acc-to")
    assert "id" in out and bank.calls == []


def test_execute_moves_money_once_idempotent():
    s, _db, bank, _audit, _clock = _store()
    pid = s.propose("C", "tok", "transfer", amount="50", from_account="acc-from",
                    to_account="acc-to")["id"]
    r1 = s.execute(pid, "C", "tok")
    r2 = s.execute(pid, "C", "tok")           # duplicate confirm
    assert r1 == r2
    assert len(bank.calls) == 1               # only one bank call
    assert bank.calls[0][1] == pid            # idempotency key == action id


def test_execute_expired_refused():
    s, _db, bank, _audit, clock = _store()
    pid = s.propose("C", "tok", "transfer", amount="50", from_account="acc-from",
                    to_account="acc-to")["id"]
    clock["t"] += 301
    with pytest.raises(ActError):
        s.execute(pid, "C", "tok")
    assert bank.calls == []


def test_execute_foreign_customer_refused():
    s, _db, bank, _audit, _clock = _store()
    pid = s.propose("C", "tok", "transfer", amount="50", from_account="acc-from",
                    to_account="acc-to")["id"]
    with pytest.raises(ActError):
        s.execute(pid, "OTHER", "tok")
