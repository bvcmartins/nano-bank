from decimal import Decimal as D
from operations import metrics


def test_float_summary_totals_by_system():
    payload = {
        "accounts": [
            {"system": "interac", "role": "clearing", "account_type": "chequing", "balance": "100.00"},
            {"system": "interac", "role": "settlement", "account_type": "savings", "balance": "50.00"},
            {"system": "lynx", "role": "clearing", "account_type": "chequing", "balance": "25.50"},
        ],
        "total_float": "175.50",
        "basis": "gross sum; not a net position",
    }
    out = metrics.float_summary(payload)
    assert out["total_float"] == D("175.50")
    assert out["by_system"]["interac"] == D("150.00")
    assert out["by_system"]["lynx"] == D("25.50")
    assert out["basis"] == "gross sum; not a net position"


def test_transactions_summary_rolls_up_by_type():
    payload = {
        "window": "7d",
        "since": "2026-07-24T00:00:00Z",
        "groups": [
            {"transaction_type": "deposit", "status": "completed", "count": 3, "total": "300.00"},
            {"transaction_type": "deposit", "status": "failed", "count": 1, "total": "10.00"},
            {"transaction_type": "withdrawal", "status": "completed", "count": 2, "total": "40.00"},
        ],
    }
    out = metrics.transactions_summary(payload)
    assert out["window"] == "7d"
    assert out["total_count"] == 6
    assert out["total_amount"] == D("350.00")
    assert out["by_type"]["deposit"]["count"] == 4
    assert out["by_type"]["deposit"]["amount"] == D("310.00")


def test_rails_summary_per_rail_totals():
    payload = {
        "window": "30d",
        "since": "2026-07-01T00:00:00Z",
        "rails": {
            "interac": [
                {"status": "settled", "count": 5, "total": "500.00"},
                {"status": "pending", "count": 2, "total": "200.00"},
            ],
            "aft": [],
            "lynx": [{"status": "settled", "count": 1, "total": "9000.00"}],
        },
    }
    out = metrics.rails_summary(payload)
    assert out["by_rail"]["interac"]["total_count"] == 7
    assert out["by_rail"]["interac"]["total_amount"] == D("700.00")
    assert out["by_rail"]["interac"]["by_status"]["pending"]["count"] == 2
    assert out["by_rail"]["aft"]["total_count"] == 0
    assert out["by_rail"]["lynx"]["total_amount"] == D("9000.00")


def test_exceptions_summary_sums_counts():
    payload = {
        "window": "30d",
        "since": "2026-07-01T00:00:00Z",
        "exceptions": {
            "failed_transactions": 2, "reversals": 1, "returned_aft_entries": 0,
            "rejected_aft_entries": 3, "wire_recalls": 1,
        },
    }
    out = metrics.exceptions_summary(payload)
    assert out["total"] == 7
    assert out["by_kind"]["rejected_aft_entries"] == 3


def test_compute_derived_figures():
    # the average-card-purchase case that used to make the COO refuse
    assert metrics.compute("ratio", [9802.52, 34])["result"] == D("288.3094")
    assert metrics.compute("mean", [10, 20, 30])["result"] == D("20.00")
    assert metrics.compute("sum", ["100.00", "50.00"])["result"] == D("150.00")
    assert metrics.compute("percent", [1122981.14, 1179606.42])["result"] == D("95.20")
    assert metrics.compute("difference", [100, 40])["result"] == D("60.00")
    assert metrics.compute("product", [3, 4])["result"] == D("12.00")


def test_compute_guards_bad_input():
    assert "error" in metrics.compute("ratio", [5])            # missing denominator
    assert "error" in metrics.compute("percent", [5, 0])       # zero denominator
    assert "error" in metrics.compute("bogus", [1, 2])         # unknown op


def test_cards_summary_holds_and_captured():
    payload = {
        "window": "30d",
        "since": "2026-07-01T00:00:00Z",
        "cardholders": {"active": 8, "single_purchase": 3},
        "authorization_holds": {"open_count": 4, "open_amount": "220.00",
                                "as_of": "2026-08-05T12:00:00Z",
                                "basis": "open now; not windowed"},
        "card_transactions": [
            {"transaction_type": "card_purchase", "status": "completed", "count": 3, "total": "150.00"},
            {"transaction_type": "card_settlement", "status": "completed", "count": 2, "total": "100.00"},
        ],
    }
    out = metrics.cards_summary(payload)
    assert out["open_holds"]["count"] == 4
    assert out["open_holds"]["amount"] == D("220.00")
    assert out["open_holds"]["as_of"] == "2026-08-05T12:00:00Z"
    assert out["open_holds"]["basis"] == "open now; not windowed"
    assert out["captured"]["count"] == 5
    assert out["captured"]["amount"] == D("250.00")
    assert out["cardholders"] == {"active": 8, "single_purchase": 3}


def test_declines_summary_rolls_up_categories_and_channels():
    raw = {
        "window": "30d", "total_count": 3, "total_amount": "1500.00",
        "by_category": {"nsf": {"count": 2, "amount": "1000.00"},
                        "other": {"count": 1, "amount": "500.00"}},
        "by_channel": {"card_authorize": {"count": 3, "amount": "1500.00"}},
    }
    out = metrics.declines_summary(raw)
    assert out["total_count"] == 3
    assert out["by_category"]["nsf"]["count"] == 2
    assert out["by_channel"]["card_authorize"]["count"] == 3
    # the fraud bucket must never appear
    assert "risk" not in out["by_category"]


def test_declines_summary_drops_a_risk_bucket_defensively():
    raw = {"by_category": {"risk": {"count": 5, "amount": "9.00"}}, "by_channel": {}}
    out = metrics.declines_summary(raw)
    assert "risk" not in out["by_category"]


def test_cards_summary_threads_rates_through():
    payload = {"window": "30d", "authorization_holds": {}, "card_transactions": [],
               "cardholders": {}, "rates": {"approved": 4, "declined": 1,
               "decline_rate": 0.2}}
    out = metrics.cards_summary(payload)
    assert out["rates"]["decline_rate"] == 0.2
