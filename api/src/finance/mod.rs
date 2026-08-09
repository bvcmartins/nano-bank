//! Interest / NIM engine (spec #2): pure money math + config. The batch and
//! inline posting logic lives in `crate::handlers::finance` and reuses these.
use rust_decimal::Decimal;
use rust_decimal_macros::dec;

/// ACT/365 simple daily interest on an end-of-day balance, rounded to the cent.
pub fn daily_interest(principal: Decimal, annual_rate: Decimal) -> Decimal {
    if principal <= Decimal::ZERO || annual_rate <= Decimal::ZERO {
        return Decimal::ZERO;
    }
    (principal * annual_rate / dec!(365)).round_dp(2)
}

/// Interchange income on a captured purchase at a bps rate, rounded to the cent.
pub fn interchange_amount(purchase: Decimal, bps: Decimal) -> Decimal {
    if purchase <= Decimal::ZERO || bps <= Decimal::ZERO {
        return Decimal::ZERO;
    }
    (purchase * bps / dec!(10000)).round_dp(2)
}

/// Resolved finance tunables, built from `Settings` for the posting logic.
#[derive(Debug, Clone)]
pub struct FinanceConfig {
    pub interchange_bps: Decimal,
    pub etransfer_fee: Decimal,
    pub transfer_fee: Decimal,
    pub maintenance_fee: Decimal,
    pub maintenance_waiver: Decimal,
}

/// Monthly maintenance fee due for a deposit account: the flat fee, or zero when
/// the balance is at/above the waiver threshold.
pub fn maintenance_due(balance: Decimal, cfg: &FinanceConfig) -> Decimal {
    if balance >= cfg.maintenance_waiver {
        Decimal::ZERO
    } else {
        cfg.maintenance_fee
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn daily_interest_act_365_rounds_to_cent() {
        // $10,000 at 3% for one day = 0.82 (0.821917… rounded).
        assert_eq!(daily_interest(dec!(10000), dec!(0.0300)), dec!(0.82));
    }

    #[test]
    fn daily_interest_zero_when_no_principal_or_rate() {
        assert_eq!(daily_interest(dec!(0), dec!(0.03)), dec!(0));
        assert_eq!(daily_interest(dec!(1000), dec!(0)), dec!(0));
    }

    #[test]
    fn interchange_150bps() {
        // $100 at 150 bps = $1.50.
        assert_eq!(interchange_amount(dec!(100), dec!(150)), dec!(1.50));
    }

    #[test]
    fn maintenance_waived_at_threshold() {
        let cfg = FinanceConfig {
            interchange_bps: dec!(150),
            etransfer_fee: dec!(1.50),
            transfer_fee: dec!(1.50),
            maintenance_fee: dec!(4.00),
            maintenance_waiver: dec!(3000),
        };
        assert_eq!(maintenance_due(dec!(2999.99), &cfg), dec!(4.00));
        assert_eq!(maintenance_due(dec!(3000), &cfg), dec!(0));
    }
}
