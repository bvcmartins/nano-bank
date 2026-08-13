pub mod database;

use config::{Config, ConfigError, Environment, File};
use rust_decimal::Decimal;
use serde::Deserialize;
use std::env;

#[derive(Debug, Deserialize, Clone)]
pub struct Settings {
    pub database: DatabaseSettings,
    pub server: ServerSettings,
    pub jwt: JwtSettings,
    pub security: SecuritySettings,
    pub logging: LoggingSettings,
    #[serde(default)]
    pub interac: InteracSettings,
    #[serde(default)]
    pub lynx: LynxSettings,
    #[serde(default)]
    pub agent: AgentSettings,
    #[serde(default)]
    pub finance: FinanceSettings,
    #[serde(default)]
    pub fraud: FraudSettings,
}

/// Interest / NIM engine tunables (spec #2). Overridable via `config/*.toml` or
/// layered env vars (e.g. `NANO_BANK__FINANCE__ETRANSFER_FEE`).
#[derive(Debug, Deserialize, Clone)]
pub struct FinanceSettings {
    /// Interchange income rate on card purchases, in basis points. Default 150.
    #[serde(with = "rust_decimal::serde::str", default = "default_interchange_bps")]
    pub interchange_bps: Decimal,
    /// Flat fee charged per outgoing e-transfer. Default $1.50.
    #[serde(with = "rust_decimal::serde::str", default = "default_etransfer_fee")]
    pub etransfer_fee: Decimal,
    /// Monthly account-maintenance fee. Default $4.00.
    #[serde(with = "rust_decimal::serde::str", default = "default_maintenance_fee")]
    pub maintenance_fee: Decimal,
    /// Maintenance fee is waived at/above this balance. Default $3000.
    #[serde(
        with = "rust_decimal::serde::str",
        default = "default_maintenance_waiver"
    )]
    pub maintenance_waiver: Decimal,
}

fn default_interchange_bps() -> Decimal {
    Decimal::new(150, 0)
}
fn default_etransfer_fee() -> Decimal {
    Decimal::new(150, 2)
}
fn default_maintenance_fee() -> Decimal {
    Decimal::new(400, 2)
}
fn default_maintenance_waiver() -> Decimal {
    Decimal::new(3000, 0)
}

impl Default for FinanceSettings {
    fn default() -> Self {
        Self {
            interchange_bps: default_interchange_bps(),
            etransfer_fee: default_etransfer_fee(),
            maintenance_fee: default_maintenance_fee(),
            maintenance_waiver: default_maintenance_waiver(),
        }
    }
}

impl Settings {
    /// Resolve the finance tunables into the engine's `FinanceConfig`.
    pub fn finance_config(&self) -> crate::finance::FinanceConfig {
        crate::finance::FinanceConfig {
            interchange_bps: self.finance.interchange_bps,
            etransfer_fee: self.finance.etransfer_fee,
            maintenance_fee: self.finance.maintenance_fee,
            maintenance_waiver: self.finance.maintenance_waiver,
        }
    }
}

/// FraudCheck port tunables. Overridable via `config/*.toml` or the layered
/// env vars, e.g. `NANO_BANK__FRAUD__BACKEND=engine`.
#[derive(Debug, Deserialize, Clone)]
pub struct FraudSettings {
    /// "engine" = call the fraud engine; anything else = no-op (default off).
    #[serde(default = "default_fraud_backend")]
    pub backend: String,
    #[serde(default = "default_fraud_engine_url")]
    pub engine_url: String,
    /// Total per-call budget (connect capped at 50ms within it).
    #[serde(default = "default_fraud_timeout_ms")]
    pub timeout_ms: u64,
    /// When the engine is unreachable: movements at or below this amount fail
    /// OPEN (proceed + post-hoc rescore), above it fail CLOSED (503).
    #[serde(
        with = "rust_decimal::serde::str",
        default = "default_fail_closed_above"
    )]
    pub fail_closed_above: Decimal,
    /// Bearer token for the engine's decision API (its FRAUD_ENGINE__AUTH__SERVICE_TOKEN).
    #[serde(default)]
    pub service_token: String,
    /// Budget for one outbox delivery to /v1/outcomes. Separate from
    /// `timeout_ms` on purpose: that is a caller's latency envelope, this runs
    /// in a cron-triggered drain where a couple of seconds is fine.
    #[serde(default = "default_outcomes_timeout_ms")]
    pub outcomes_timeout_ms: u64,
    /// Honour an `X-Simulated-Time` header as the instant a decision is
    /// measured at, instead of wall clock.
    ///
    /// **Off in any deployment where real money moves.** A caller-supplied
    /// clock drives the engine's velocity windows, so a timestamp from last
    /// week makes every window look empty — it turns off the primary lever the
    /// engine has. This exists so a simulated corpus can be replayed with a
    /// real time axis (fraud engine #21, which added the matching opt-in on
    /// the other side); it is a backfill affordance, nothing more.
    #[serde(default)]
    pub accept_simulated_time: bool,
}

fn default_fraud_backend() -> String {
    "off".to_string()
}

fn default_fraud_engine_url() -> String {
    "http://localhost:8092".to_string()
}

fn default_fraud_timeout_ms() -> u64 {
    150
}

fn default_outcomes_timeout_ms() -> u64 {
    2000
}

fn default_fail_closed_above() -> Decimal {
    Decimal::new(100000, 2) // 1000.00 CAD
}

impl Default for FraudSettings {
    fn default() -> Self {
        Self {
            backend: default_fraud_backend(),
            engine_url: default_fraud_engine_url(),
            timeout_ms: default_fraud_timeout_ms(),
            fail_closed_above: default_fail_closed_above(),
            service_token: String::new(),
            outcomes_timeout_ms: default_outcomes_timeout_ms(),
            accept_simulated_time: false,
        }
    }
}

#[derive(Debug, Deserialize, Clone)]
pub struct DatabaseSettings {
    pub host: String,
    pub port: u16,
    pub username: String,
    pub password: String,
    pub database_name: String,
    pub max_connections: u32,
    pub min_connections: u32,
    pub acquire_timeout: u64,
}

#[derive(Debug, Deserialize, Clone)]
pub struct ServerSettings {
    pub host: String,
    pub port: u16,
    pub workers: Option<usize>,
    pub keep_alive: u64,
}

#[derive(Debug, Deserialize, Clone)]
pub struct JwtSettings {
    pub secret: String,
    pub expires_in: i64,
    pub refresh_expires_in: i64,
    /// TTL for agent-plane tokens. Deliberately shorter than `expires_in`: an
    /// agent token is a pointer to a revocable mandate, re-validated on every
    /// request, so a tight expiry costs the agent only a cheap re-mint.
    pub agent_expires_in: i64,
    pub issuer: String,
}

#[derive(Debug, Deserialize, Clone)]
pub struct SecuritySettings {
    pub password_min_length: usize,
    pub max_login_attempts: u32,
    pub lockout_duration: u64,
    pub session_timeout: i64,
    pub require_mfa: bool,
    /// Shared secret presented by the card network/processor to mint a service
    /// token at `POST /auth/service-token` (OAuth client-credentials style).
    pub service_client_secret: String,
    /// Agent registrations allowed from one address per window. Registration is
    /// unauthenticated by design, so it needs a meter rather than a credential.
    /// Defaulted so an existing config file keeps loading.
    #[serde(default = "default_max_agent_registrations")]
    pub max_agent_registrations: u32,
    #[serde(default = "default_agent_registration_window")]
    pub agent_registration_window: u64,
}

fn default_max_agent_registrations() -> u32 {
    60
}

fn default_agent_registration_window() -> u64 {
    60
}

#[derive(Debug, Deserialize, Clone)]
pub struct LoggingSettings {
    pub level: String,
    pub format: String,
}

/// Interac e-Transfer rail tunables. Overridable via `config/*.toml` or the
/// layered env vars `NANO_BANK__INTERAC__EXPIRY_DAYS` /
/// `NANO_BANK__INTERAC__MAX_ETRANSFER_AMOUNT`.
#[derive(Debug, Deserialize, Clone)]
pub struct InteracSettings {
    /// Hold lifetime before auto-expiry (real Interac: 30 days).
    #[serde(default = "default_expiry_days")]
    pub expiry_days: i64,
    /// Max amount per e-Transfer (funds check aside). Real Interac default $3,000.
    #[serde(with = "rust_decimal::serde::str", default = "default_max_etransfer")]
    pub max_etransfer_amount: Decimal,
}

fn default_expiry_days() -> i64 {
    30
}

fn default_max_etransfer() -> Decimal {
    Decimal::new(3000, 0)
}

impl Default for InteracSettings {
    fn default() -> Self {
        Self {
            expiry_days: default_expiry_days(),
            max_etransfer_amount: default_max_etransfer(),
        }
    }
}

/// Lynx RTGS wire rail tunables. Overridable via `config/*.toml` or the layered
/// env vars `NANO_BANK__LYNX__MIN_AMOUNT` / `NANO_BANK__LYNX__STALE_MINUTES`.
#[derive(Debug, Deserialize, Clone)]
pub struct LynxSettings {
    /// High-value floor: the minimum wire amount (real Lynx has no retail cap;
    /// this floor keeps low-value payments on the retail rails). Default $10,000.
    #[serde(with = "rust_decimal::serde::str", default = "default_min_amount")]
    pub min_amount: Decimal,
    /// How old (minutes) a `sent` wire must be before the admin sweep rejects it.
    #[serde(default = "default_stale_minutes")]
    pub stale_minutes: i32,
}

fn default_min_amount() -> Decimal {
    Decimal::new(1000000, 2)
}

fn default_stale_minutes() -> i32 {
    60
}

impl Default for LynxSettings {
    fn default() -> Self {
        Self {
            min_amount: default_min_amount(),
            stale_minutes: default_stale_minutes(),
        }
    }
}

/// Agent-plane (agentic banking) tunables. Overridable via `config/*.toml` or
/// the layered env var `NANO_BANK__AGENT__APPROVAL_TTL_MINUTES`.
#[derive(Debug, Deserialize, Clone)]
pub struct AgentSettings {
    /// How long a step-up pending approval stays actionable before it expires.
    #[serde(default = "default_approval_ttl_minutes")]
    pub approval_ttl_minutes: i64,
}

fn default_approval_ttl_minutes() -> i64 {
    60
}

impl Default for AgentSettings {
    fn default() -> Self {
        Self {
            approval_ttl_minutes: default_approval_ttl_minutes(),
        }
    }
}

impl Settings {
    pub fn new() -> Result<Self, ConfigError> {
        let run_mode = env::var("RUN_MODE").unwrap_or_else(|_| "development".into());

        let s = Config::builder()
            // Start with default configuration
            .add_source(File::with_name("config/default").required(false))
            // Add environment-specific configuration
            .add_source(File::with_name(&format!("config/{}", run_mode)).required(false))
            // Add local configuration (gitignored)
            .add_source(File::with_name("config/local").required(false))
            // Add environment variables with prefix "NANO_BANK"
            .add_source(Environment::with_prefix("NANO_BANK").separator("__"))
            .build()?;

        s.try_deserialize()
    }

    pub fn database_url(&self) -> String {
        let host = if self.database.host.contains(':') {
            format!("[{}]", self.database.host)
        } else {
            self.database.host.clone()
        };
        format!(
            "postgresql://{}:{}@{}:{}/{}?sslmode=disable",
            self.database.username,
            self.database.password,
            host,
            self.database.port,
            self.database.database_name
        )
    }

    pub fn server_address(&self) -> String {
        format!("{}:{}", self.server.host, self.server.port)
    }
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            database: DatabaseSettings {
                host: "localhost".to_string(),
                port: 30432,
                username: "nanobank_user".to_string(),
                password: "secure_nano_password_2024!".to_string(),
                database_name: "nano_bank_db".to_string(),
                max_connections: 10,
                min_connections: 1,
                acquire_timeout: 30,
            },
            server: ServerSettings {
                host: "0.0.0.0".to_string(),
                port: 8081,
                workers: None,
                keep_alive: 60,
            },
            jwt: JwtSettings {
                secret: "your-super-secret-jwt-key-change-this-in-production".to_string(),
                expires_in: 900,            // 15 min (short-lived access token)
                refresh_expires_in: 604800, // 1 week
                agent_expires_in: 300,      // 5 min (agent tokens are mandate pointers)
                issuer: "nano-bank".to_string(),
            },
            security: SecuritySettings {
                password_min_length: 8,
                max_login_attempts: 5,
                lockout_duration: 900,  // 15 minutes
                session_timeout: 86400, // 24 hours
                require_mfa: false,
                service_client_secret: "nano-bank-visa-network-secret-change-me".to_string(),
                max_agent_registrations: default_max_agent_registrations(),
                agent_registration_window: default_agent_registration_window(),
            },
            logging: LoggingSettings {
                level: "info".to_string(),
                format: "json".to_string(),
            },
            interac: InteracSettings::default(),
            lynx: LynxSettings::default(),
            agent: AgentSettings::default(),
            finance: FinanceSettings::default(),
            fraud: FraudSettings::default(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn loads_interac_settings_from_default_toml() {
        // Runs with cwd = crate root (api/), so config/default.toml is found.
        let s = Settings::new().expect("config should load");
        assert_eq!(s.interac.expiry_days, 30);
        assert_eq!(s.interac.max_etransfer_amount, Decimal::new(3000, 0));
    }

    #[test]
    fn loads_lynx_settings_from_default_toml() {
        let s = Settings::new().expect("config should load");
        assert_eq!(s.lynx.min_amount, Decimal::new(10000, 0));
        assert_eq!(s.lynx.stale_minutes, 60);
    }
}
