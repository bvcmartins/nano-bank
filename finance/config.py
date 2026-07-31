from __future__ import annotations
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping, Optional


_DEFAULT_WEIGHTS = {
    "CashReserves": Decimal("0"),
    "Bank": Decimal("0.20"),            # interbank / central-bank claim
    "TreasuryPlacement": Decimal("0.20"),
    "CardReceivable": Decimal("0.75"),
    "OverdraftReceivable": Decimal("1.00"),
    "LoansReceivable": Decimal("1.00"),
}
# Any asset role without an explicit weight is risk-weighted at this rate.
# It must never be 0: an unmapped asset silently treated as risk-free collapses
# RWA, and with it economic capital, which makes RAROC explode.
_DEFAULT_ASSET_WEIGHT = Decimal("1.00")
_DEFAULT_LOSS = {
    "CardReceivable": Decimal("0.03"),
    "OverdraftReceivable": Decimal("0.02"),
    "LoansReceivable": Decimal("0.015"),
}
# A credit-exposed role (roles.CREDIT_EXPOSED_ROLES) with no configured loss rate
# is charged at this rate rather than silently contributing zero expected loss.
# Set to the highest built-in rate — an unconfigured credit asset is assumed at
# least as risky as the riskiest configured one — and, like the asset-weight
# default, it must never be 0.
_DEFAULT_LOSS_RATE = Decimal("0.03")


@dataclass(frozen=True)
class RiskConfig:
    """Basel-lite capital model for RAROC (spec #5 replaces this behind raroc())."""
    risk_weights: dict
    loss_rates: dict
    target_ratio: Decimal
    default_asset_weight: Decimal = _DEFAULT_ASSET_WEIGHT
    default_loss_rate: Decimal = _DEFAULT_LOSS_RATE

    @classmethod
    def default(cls) -> "RiskConfig":
        return cls(risk_weights=dict(_DEFAULT_WEIGHTS),
                   loss_rates=dict(_DEFAULT_LOSS),
                   target_ratio=Decimal("0.10"))

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "RiskConfig":
        e = os.environ if env is None else env
        weights = dict(_DEFAULT_WEIGHTS)
        loss = dict(_DEFAULT_LOSS)
        # Iterate the RISK_WEIGHT_*/RISK_LOSS_* env keys rather than the default
        # tables, so a role with no built-in default — Receivable,
        # AccruedInterestReceivable, InputTax — can actually be configured
        # instead of being pinned to the fallback weight forever.
        for k, v in e.items():
            if k.startswith("RISK_WEIGHT_"):
                weights[k[len("RISK_WEIGHT_"):]] = Decimal(v)
            elif k.startswith("RISK_LOSS_"):
                loss[k[len("RISK_LOSS_"):]] = Decimal(v)
        ratio = Decimal(e.get("RISK_TARGET_RATIO", "0.10"))
        default_w = Decimal(e.get("RISK_DEFAULT_ASSET_WEIGHT",
                                  str(_DEFAULT_ASSET_WEIGHT)))
        default_loss = Decimal(e.get("RISK_DEFAULT_LOSS_RATE",
                                     str(_DEFAULT_LOSS_RATE)))
        # Enforce the invariant the fallback weight exists to protect: a zero (or
        # negative) default treats every unmapped asset as risk-free, collapsing
        # RWA and economic capital and making RAROC explode. Fail loudly at load
        # rather than emit silently-wrong capital numbers downstream. The same
        # reasoning extends to the other capital knobs: a zero target ratio
        # collapses economic capital just as quietly (raroc then returns None),
        # and a negative weight or loss rate yields negative RWA / expected loss.
        if default_w <= 0:
            raise ValueError(
                "RISK_DEFAULT_ASSET_WEIGHT must be > 0 "
                f"(got {default_w}); a zero/negative default risk-weights "
                "unmapped assets as risk-free and collapses the capital model")
        if default_loss <= 0:
            raise ValueError(
                "RISK_DEFAULT_LOSS_RATE must be > 0 "
                f"(got {default_loss}); a zero/negative default charges no "
                "expected loss on an unconfigured credit asset")
        if ratio <= 0:
            raise ValueError(
                f"RISK_TARGET_RATIO must be > 0 (got {ratio}); a zero/negative "
                "target ratio zeroes economic capital and collapses RAROC")
        for role, w in weights.items():
            if w < 0:
                raise ValueError(
                    f"RISK_WEIGHT_{role} must be >= 0 (got {w}); a negative risk "
                    "weight produces negative risk-weighted assets")
        for role, r in loss.items():
            if r < 0:
                raise ValueError(
                    f"RISK_LOSS_{role} must be >= 0 (got {r}); a negative loss "
                    "rate produces negative expected loss")
        return cls(risk_weights=weights, loss_rates=loss, target_ratio=ratio,
                   default_asset_weight=default_w, default_loss_rate=default_loss)


@dataclass
class Settings:
    db: dict
    nano_bank_api: str
    mcp_port: int

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        e = os.environ if env is None else env

        def g(k, d=""):
            return e.get(k, d)

        return cls(
            db=dict(
                host=g("DB_HOST", "::1"),
                port=int(g("DB_PORT", "5432")),
                dbname=g("DB_NAME", "nano_bank_db"),
                user=g("DB_USER", "nanobank_user"),
                password=g("DB_PASSWORD", "secure_nano_password_2024!"),
            ),
            nano_bank_api=g("NANO_BANK_API", "http://localhost:8081"),
            mcp_port=int(g("MCP_PORT", "8088")),
        )
