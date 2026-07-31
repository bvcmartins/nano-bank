from decimal import Decimal as D
import pytest
from finance.config import RiskConfig


def test_default_risk_config_matches_spec():
    rc = RiskConfig.default()
    assert rc.target_ratio == D("0.10")
    assert rc.risk_weights["CardReceivable"] == D("0.75")
    assert rc.risk_weights["TreasuryPlacement"] == D("0.20")
    assert rc.risk_weights["OverdraftReceivable"] == D("1.00")
    assert rc.risk_weights["LoansReceivable"] == D("1.00")
    assert rc.risk_weights["CashReserves"] == D("0")
    assert rc.loss_rates["CardReceivable"] == D("0.03")
    assert rc.loss_rates["OverdraftReceivable"] == D("0.02")
    assert rc.loss_rates["LoansReceivable"] == D("0.015")


def test_from_env_overrides_target_and_a_weight():
    rc = RiskConfig.from_env({
        "RISK_TARGET_RATIO": "0.12",
        "RISK_WEIGHT_CardReceivable": "0.80",
        "RISK_LOSS_LoansReceivable": "0.02",
    })
    assert rc.target_ratio == D("0.12")
    assert rc.risk_weights["CardReceivable"] == D("0.80")
    assert rc.risk_weights["TreasuryPlacement"] == D("0.20")   # untouched default
    assert rc.loss_rates["LoansReceivable"] == D("0.02")


def test_from_env_can_override_default_asset_weight():
    rc = RiskConfig.from_env({"RISK_DEFAULT_ASSET_WEIGHT": "1.50"})
    assert rc.default_asset_weight == D("1.50")


@pytest.mark.parametrize("bad", ["0", "0.00", "-0.5"])
def test_from_env_rejects_nonpositive_default_asset_weight(bad):
    # A zero/negative default treats unmapped assets as risk-free and collapses
    # the capital model — it must fail loudly at load, not silently.
    with pytest.raises(ValueError):
        RiskConfig.from_env({"RISK_DEFAULT_ASSET_WEIGHT": bad})


def test_from_env_configures_a_role_with_no_builtin_default():
    """Receivable has no entry in the default weight/loss tables. The old loop
    iterated the defaults, so RISK_WEIGHT_Receivable was silently ignored and the
    role stayed pinned to the fallback forever — the remedy the 'assumed weight'
    framing implies was unreachable through the documented interface."""
    rc = RiskConfig.from_env({
        "RISK_WEIGHT_Receivable": "0.35",
        "RISK_LOSS_Receivable": "0.04",
    })
    assert rc.risk_weights["Receivable"] == D("0.35")
    assert rc.loss_rates["Receivable"] == D("0.04")
    # built-ins untouched
    assert rc.risk_weights["CardReceivable"] == D("0.75")


def test_from_env_can_override_default_loss_rate():
    rc = RiskConfig.from_env({"RISK_DEFAULT_LOSS_RATE": "0.05"})
    assert rc.default_loss_rate == D("0.05")


@pytest.mark.parametrize("env", [
    {"RISK_TARGET_RATIO": "0"},            # zeroes economic capital -> raroc None
    {"RISK_TARGET_RATIO": "-0.1"},
    {"RISK_WEIGHT_CardReceivable": "-0.1"},  # negative RWA
    {"RISK_LOSS_CardReceivable": "-0.01"},   # negative expected loss
    {"RISK_DEFAULT_LOSS_RATE": "0"},         # silently charges no loss
])
def test_from_env_rejects_invalid_capital_knobs(env):
    """The same fail-loud guard the default asset weight already had, extended to
    the neighbouring knobs that collapse the model just as quietly."""
    with pytest.raises(ValueError):
        RiskConfig.from_env(env)
