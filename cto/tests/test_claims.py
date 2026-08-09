from cto.claims import unsupported_claims


def test_books_mention_without_disclaimer_is_flagged():
    issues = unsupported_claims("Our net interest margin improved to 3.2%.", [])
    assert any("CFO" in i for i in issues)


def test_books_mention_with_disclaimer_is_allowed():
    issues = unsupported_claims(
        "I cannot speak to NIM — that is the CFO's domain.", [])
    assert issues == []


def test_money_ops_mention_without_disclaimer_is_flagged():
    issues = unsupported_claims("Settlement float across the rails is $2M.", [])
    assert any("COO" in i for i in issues)


def test_fraud_is_out_of_scope():
    issues = unsupported_claims("The fraud rate is elevated.", [])
    assert any("scope" in i.lower() for i in issues)


def test_pure_platform_answer_is_clean():
    issues = unsupported_claims(
        "2 of 7 deployments are degraded; coo is crashlooping (9 restarts).", [])
    assert issues == []
