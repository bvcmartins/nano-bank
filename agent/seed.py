from __future__ import annotations


class CredStore:
    def __init__(self):
        self._d: dict = {}

    def put(self, customer_id, email, password):
        self._d[customer_id] = (email, password)

    def get(self, customer_id):
        return self._d.get(customer_id)

    def as_dict(self):
        return dict(self._d)


def seed_customer(bank, store: CredStore, *, first, last, email, password,
                  dob="1990-01-01") -> dict:
    out = bank.create_customer({
        "first_name": first, "last_name": last, "email": email,
        "phone_number": "+15550100000", "date_of_birth": dob, "password": password})
    cid = out["customer_id"]
    store.put(cid, email, password)
    return {"customer_id": cid, "email": email, "password": password}


def open_account(bank, token, customer_id, account_type="chequing") -> dict:
    return bank.create_account(token, {"customer_id": customer_id,
                                       "account_type": account_type})


def fund(bank, token, account_id, amount) -> dict:
    return bank.deposit(token, account_id, str(amount))


def seed_demo(bank) -> dict:
    store = CredStore()
    customers = []
    for i, (first, email) in enumerate([("Ada", "ada@x.ca"), ("Bo", "bo@x.ca")]):
        c = seed_customer(bank, store, first=first, last="Demo", email=email,
                          password="pw12345678")
        token = bank.login(email, "pw12345678")
        acc = open_account(bank, token, c["customer_id"])
        if i == 0:
            fund(bank, token, acc["account_id"], "1000")
        customers.append({**c, "account_id": acc["account_id"]})
    return {"customers": customers, "creds": store.as_dict()}
